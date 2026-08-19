"""
tests/test_synthesis_helpers.py
================================
Unit tests for pure-logic helpers in data/synthesis_live.py:
  - Constants: WS_URL, KALSHI_FEE_PCT, MAX_FEE
  - TopOfBook dataclass: defaults, midpoint_yes(), executable_cost_yes/no
  - _kalshi_fee(): formula correctness and cap

No WebSocket, no DB — all tests are pure Python.
"""
from __future__ import annotations

import math
import pytest


class TestSynthesisConstants:
    """Module-level constants."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.synthesis_live import (
            WS_URL, KALSHI_FEE_PCT, MAX_FEE,
            RECONNECT_DELAY_BASE, RECONNECT_DELAY_MAX, HEARTBEAT_INTERVAL
        )
        self.ws_url           = WS_URL
        self.fee_pct          = KALSHI_FEE_PCT
        self.max_fee          = MAX_FEE
        self.recon_base       = RECONNECT_DELAY_BASE
        self.recon_max        = RECONNECT_DELAY_MAX
        self.heartbeat        = HEARTBEAT_INTERVAL

    def test_ws_url_is_string(self):
        assert isinstance(self.ws_url, str)

    def test_ws_url_starts_with_wss(self):
        assert self.ws_url.startswith("wss://")

    def test_fee_pct_is_007(self):
        assert abs(self.fee_pct - 0.07) < 1e-9

    def test_max_fee_is_0035(self):
        assert abs(self.max_fee - 0.035) < 1e-9

    def test_reconnect_base_positive(self):
        assert self.recon_base > 0

    def test_reconnect_max_gt_base(self):
        assert self.recon_max > self.recon_base

    def test_heartbeat_positive(self):
        assert self.heartbeat > 0


class TestTopOfBook:
    """TopOfBook dataclass: defaults and computed properties."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.synthesis_live import TopOfBook
        self.TopOfBook = TopOfBook

    def _make(self, **kwargs):
        defaults = {"market_id": "KXTEST-YES"}
        defaults.update(kwargs)
        return self.TopOfBook(**defaults)

    def test_instantiates(self):
        tob = self._make()
        assert tob.market_id == "KXTEST-YES"

    def test_default_yes_bid_zero(self):
        assert self._make().yes_bid == 0.0

    def test_default_yes_ask_one(self):
        assert self._make().yes_ask == 1.0

    def test_default_no_bid_zero(self):
        assert self._make().no_bid == 0.0

    def test_default_no_ask_one(self):
        assert self._make().no_ask == 1.0

    def test_midpoint_yes_correct(self):
        tob = self._make(yes_bid=0.4, yes_ask=0.6)
        assert abs(tob.midpoint_yes() - 0.5) < 1e-9

    def test_midpoint_yes_at_extremes(self):
        tob = self._make(yes_bid=0.0, yes_ask=1.0)
        assert abs(tob.midpoint_yes() - 0.5) < 1e-9

    def test_midpoint_yes_tight_spread(self):
        tob = self._make(yes_bid=0.51, yes_ask=0.53)
        assert abs(tob.midpoint_yes() - 0.52) < 1e-9

    def test_executable_cost_yes_is_ask(self):
        tob = self._make(yes_ask=0.72)
        assert tob.executable_cost_yes() == 0.72

    def test_executable_cost_no_is_no_ask(self):
        tob = self._make(no_ask=0.31)
        assert tob.executable_cost_no() == 0.31

    def test_default_sequence_zero(self):
        assert self._make().sequence == 0


class TestKalshiFee:
    """_kalshi_fee(): formula = ceil(0.07 * P * (1-P) * 100) / 100, cap $0.035."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from feeds.synthesis_live import _kalshi_fee
        self.fn = _kalshi_fee

    def test_returns_float(self):
        assert isinstance(self.fn(0.5), float)

    def test_at_extremes_fee_is_zero(self):
        """P=0 or P=1: P*(1-P)=0, fee=0."""
        assert self.fn(0.0) == 0.0
        assert self.fn(1.0) == 0.0

    def test_at_half_fee_is_max(self):
        """P=0.5 gives maximum theoretical fee: 0.07 * 0.25 = 0.0175 -> ceil -> 0.02."""
        fee = self.fn(0.5)
        # ceil(0.07 * 0.5 * 0.5 * 100) / 100 = ceil(1.75) / 100 = 2 / 100 = 0.02
        assert abs(fee - 0.02) < 1e-9

    def test_fee_capped_at_max_fee(self):
        """Fee is capped at MAX_FEE = 0.035."""
        from feeds.synthesis_live import MAX_FEE
        for p in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            assert self.fn(p) <= MAX_FEE + 1e-9

    def test_fee_always_nonnegative(self):
        for p in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            assert self.fn(p) >= 0.0

    def test_fee_symmetric(self):
        """Fee should be the same for P and 1-P."""
        for p in [0.1, 0.2, 0.3, 0.4, 0.5]:
            assert abs(self.fn(p) - self.fn(1.0 - p)) < 1e-9

    def test_formula_sample_value(self):
        """Manual: P=0.3 -> 0.07 * 0.3 * 0.7 * 100 = 1.47 -> ceil=2 -> 0.02."""
        fee = self.fn(0.3)
        assert abs(fee - 0.02) < 1e-9
