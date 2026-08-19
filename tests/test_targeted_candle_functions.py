"""
tests/test_targeted_candle_functions.py
=========================================
Unit tests for analysis/targeted_candle_pull:
  - _get_candidate_markets(): calls session_scope, returns list of dicts
  - pull_active_candles(): iterates tickers, calls client + _flush_candles
  - pull_historical_candles(): calls client.get_historical_candlesticks, returns count
  - run_targeted_candle_pull(): orchestration, returns summary dict

All DB and network calls mocked.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock, call
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_imports():
    with patch.dict(sys.modules, {
        "database.repository": MagicMock(),
        "database.models": MagicMock(),
        "config": MagicMock(DB_URL="postgresql://localhost/test"),
        "feeds.kalshi_client": MagicMock(),
    }):
        yield


def _session_ctx():
    mock_ss = MagicMock()
    mock_sess = MagicMock()
    mock_ss.return_value.__enter__ = MagicMock(return_value=mock_sess)
    mock_ss.return_value.__exit__ = MagicMock(return_value=False)
    return mock_ss, mock_sess


def _mock_row(market_id, ticker, status, trade_count=0):
    row = MagicMock()
    row._mapping = {
        "market_id": market_id, "ticker": ticker,
        "status": status, "trade_count": trade_count,
    }
    return row


# ---------------------------------------------------------------------------
# _get_candidate_markets
# ---------------------------------------------------------------------------

class TestGetCandidateMarkets:
    def test_returns_list(self):
        from analysis.targeted_candle_pull import _get_candidate_markets
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("analysis.targeted_candle_pull.session_scope", mock_ss):
            result = _get_candidate_markets()
        assert isinstance(result, list)

    def test_empty_returns_empty_list(self):
        from analysis.targeted_candle_pull import _get_candidate_markets
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("analysis.targeted_candle_pull.session_scope", mock_ss):
            result = _get_candidate_markets()
        assert result == []

    def test_calls_session_scope(self):
        from analysis.targeted_candle_pull import _get_candidate_markets
        mock_ss, mock_sess = _session_ctx()
        mock_sess.execute.return_value.fetchall.return_value = []
        with patch("analysis.targeted_candle_pull.session_scope", mock_ss):
            _get_candidate_markets()
        mock_ss.assert_called_once()

    def test_rows_converted_to_dicts(self):
        from analysis.targeted_candle_pull import _get_candidate_markets
        mock_ss, mock_sess = _session_ctx()
        rows = [_mock_row("MID-1", "TICK-1", "active", 100)]
        mock_sess.execute.return_value.fetchall.return_value = rows
        with patch("analysis.targeted_candle_pull.session_scope", mock_ss):
            result = _get_candidate_markets()
        assert len(result) == 1
        assert result[0]["ticker"] == "TICK-1"
        assert result[0]["status"] == "active"

    def test_multiple_rows(self):
        from analysis.targeted_candle_pull import _get_candidate_markets
        mock_ss, mock_sess = _session_ctx()
        rows = [
            _mock_row("MID-1", "TICK-1", "active", 100),
            _mock_row("MID-2", "TICK-2", "finalized", 50),
        ]
        mock_sess.execute.return_value.fetchall.return_value = rows
        with patch("analysis.targeted_candle_pull.session_scope", mock_ss):
            result = _get_candidate_markets()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# pull_active_candles
# ---------------------------------------------------------------------------

class TestPullActiveCandles:
    def _make_client(self, candles_map=None):
        client = MagicMock()
        client.get_candlesticks.return_value = {"candlesticks": candles_map or {}}
        return client

    def test_empty_tickers_returns_zero(self):
        from analysis.targeted_candle_pull import pull_active_candles
        client = self._make_client()
        result = pull_active_candles(client, [], 1000, 2000)
        assert result == 0

    def test_empty_tickers_no_client_call(self):
        from analysis.targeted_candle_pull import pull_active_candles
        client = self._make_client()
        pull_active_candles(client, [], 1000, 2000)
        client.get_candlesticks.assert_not_called()

    def test_single_ticker_calls_client(self):
        from analysis.targeted_candle_pull import pull_active_candles
        client = self._make_client({"TICK-A": []})
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=0):
            pull_active_candles(client, ["TICK-A"], 1000, 2000)
        client.get_candlesticks.assert_called_once()

    def test_empty_candle_data_returns_zero(self):
        from analysis.targeted_candle_pull import pull_active_candles
        client = self._make_client({"TICK-A": []})
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=0):
            result = pull_active_candles(client, ["TICK-A"], 1000, 2000)
        assert result == 0

    def test_exception_in_client_does_not_raise(self):
        from analysis.targeted_candle_pull import pull_active_candles
        client = MagicMock()
        client.get_candlesticks.side_effect = Exception("network error")
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=0):
            try:
                result = pull_active_candles(client, ["TICK-A"], 1000, 2000)
            except Exception:
                pytest.fail("pull_active_candles raised unexpectedly")

    def test_candles_parsed_and_flushed(self):
        from analysis.targeted_candle_pull import pull_active_candles
        candle = {"end_ts": 1700000000, "yes_bid_close": 0.55, "close": 0.55, "volume": 100}
        client = self._make_client({"TICK-A": [candle]})
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=1) as mock_flush:
            result = pull_active_candles(client, ["TICK-A"], 1000, 2000)
        mock_flush.assert_called()


# ---------------------------------------------------------------------------
# pull_historical_candles
# ---------------------------------------------------------------------------

class TestPullHistoricalCandles:
    def test_empty_candles_returns_zero(self):
        from analysis.targeted_candle_pull import pull_historical_candles
        client = MagicMock()
        client.get_historical_candlesticks.return_value = {"candlesticks": []}
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=0):
            result = pull_historical_candles(client, "TICK-A", 1000, 2000)
        assert result == 0

    def test_exception_returns_zero(self):
        from analysis.targeted_candle_pull import pull_historical_candles
        client = MagicMock()
        client.get_historical_candlesticks.side_effect = Exception("timeout")
        result = pull_historical_candles(client, "TICK-A", 1000, 2000)
        assert result == 0

    def test_exception_does_not_raise(self):
        from analysis.targeted_candle_pull import pull_historical_candles
        client = MagicMock()
        client.get_historical_candlesticks.side_effect = Exception("timeout")
        try:
            pull_historical_candles(client, "TICK-A", 1000, 2000)
        except Exception:
            pytest.fail("pull_historical_candles raised unexpectedly")

    def test_calls_client_get_historical_candlesticks(self):
        from analysis.targeted_candle_pull import pull_historical_candles
        client = MagicMock()
        client.get_historical_candlesticks.return_value = {"candlesticks": []}
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=0):
            pull_historical_candles(client, "TICK-A", 1000, 2000)
        client.get_historical_candlesticks.assert_called_once()

    def test_returns_flush_result(self):
        from analysis.targeted_candle_pull import pull_historical_candles
        candle = {"end_ts": 1700000000, "close": 0.55, "volume": 100}
        client = MagicMock()
        client.get_historical_candlesticks.return_value = {"candlesticks": [candle]}
        with patch("analysis.targeted_candle_pull._flush_candles", return_value=5):
            result = pull_historical_candles(client, "TICK-A", 1000, 2000)
        assert result == 5


# ---------------------------------------------------------------------------
# run_targeted_candle_pull
# ---------------------------------------------------------------------------

class TestRunTargetedCandlePull:
    def _base_markets(self, active=1, finalized=1):
        markets = []
        for i in range(active):
            markets.append({"ticker": f"ACT-{i}", "status": "active", "trade_count": 100})
        for i in range(finalized):
            markets.append({"ticker": f"FIN-{i}", "status": "finalized", "trade_count": 50})
        return markets

    def test_returns_dict(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=[]), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=[]), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull()
        for key in ("active_markets", "finalized_markets", "total_candles",
                    "period_interval", "lookback_days"):
            assert key in result

    def test_empty_markets_returns_zero_candles(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=[]), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull()
        assert result["total_candles"] == 0

    def test_active_market_count_correct(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        markets = self._base_markets(active=3, finalized=2)
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=markets), \
             patch("analysis.targeted_candle_pull.pull_active_candles", return_value=10), \
             patch("analysis.targeted_candle_pull.pull_historical_candles", return_value=5), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull()
        assert result["active_markets"] == 3

    def test_finalized_market_count_correct(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        markets = self._base_markets(active=3, finalized=2)
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=markets), \
             patch("analysis.targeted_candle_pull.pull_active_candles", return_value=10), \
             patch("analysis.targeted_candle_pull.pull_historical_candles", return_value=5), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull()
        assert result["finalized_markets"] == 2

    def test_period_interval_in_result(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=[]), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull(period_interval=5)
        assert result["period_interval"] == 5

    def test_lookback_days_in_result(self):
        from analysis.targeted_candle_pull import run_targeted_candle_pull
        with patch("analysis.targeted_candle_pull._get_candidate_markets", return_value=[]), \
             patch("analysis.targeted_candle_pull.KalshiClient"):
            result = run_targeted_candle_pull(lookback_days=14)
        assert result["lookback_days"] == 14
