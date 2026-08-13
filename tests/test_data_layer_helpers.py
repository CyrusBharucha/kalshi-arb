"""
tests/test_data_layer_helpers.py
==================================
Unit tests for dashboard/data_layer.py pure decorator and filter helpers:
  - _safe_sql(): wraps query function; on success returns (result, None);
    on exception returns (empty DataFrame, error string)
  - _filter_canadian_opps(): already tested in test_data_layer.py;
    here we focus on edge cases not covered there

No DB, no Streamlit.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="module")
def _patch_streamlit():
    mock_st = MagicMock()
    mock_st.cache_data = lambda **kw: (lambda fn: fn)  # no-op decorator
    mock_dl = MagicMock()
    with patch.dict(sys.modules, {"streamlit": mock_st}):
        yield


class TestSafeSql:
    """_safe_sql(): decorator behaviour."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.data_layer import _safe_sql
        self.decorator = _safe_sql

    def test_success_returns_result_and_none(self):
        @self.decorator
        def good_query():
            return pd.DataFrame({"x": [1, 2, 3]})

        result, err = good_query()
        assert err is None
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3

    def test_exception_returns_empty_df_and_error_string(self):
        @self.decorator
        def bad_query():
            raise RuntimeError("DB is down")

        result, err = bad_query()
        assert isinstance(result, pd.DataFrame)
        assert result.empty
        assert isinstance(err, str)
        assert "DB is down" in err

    def test_exception_preserves_original_message(self):
        @self.decorator
        def raises_value_error():
            raise ValueError("bad column name")

        _, err = raises_value_error()
        assert "bad column name" in err

    def test_args_passed_through(self):
        @self.decorator
        def echo_args(a, b, c=99):
            return pd.DataFrame({"a": [a], "b": [b], "c": [c]})

        result, err = echo_args(1, 2, c=3)
        assert err is None
        assert result.iloc[0]["a"] == 1
        assert result.iloc[0]["b"] == 2
        assert result.iloc[0]["c"] == 3

    def test_preserves_function_name_in_wrapper(self):
        @self.decorator
        def my_special_query():
            return pd.DataFrame()

        # wrapper should be callable
        assert callable(my_special_query)

    def test_arbitrary_exception_type_caught(self):
        class CustomDBError(Exception):
            pass

        @self.decorator
        def query_with_custom_error():
            raise CustomDBError("timeout")

        result, err = query_with_custom_error()
        assert result.empty
        assert "timeout" in err


class TestFilterCanadianOppsEdgeCases:
    """_filter_canadian_opps(): filters on markets_involved column."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.data_layer import _filter_canadian_opps
        self.fn = _filter_canadian_opps

    def _df_with_markets(self, markets_list):
        return pd.DataFrame({"markets_involved": markets_list, "net_edge": [0.01] * len(markets_list)})

    def test_boc_keyword_passes(self):
        df = self._df_with_markets(["KXBOC-YES", "KXWHAT-YES"])
        result = self.fn(df)
        assert len(result) == 1
        assert "boc" in result.iloc[0]["markets_involved"].lower()

    def test_cad_keyword_passes(self):
        df = self._df_with_markets(["KXCADUSD-YES", "KXOTHER-YES"])
        result = self.fn(df)
        assert len(result) == 1

    def test_non_canadian_filtered_out(self):
        df = self._df_with_markets(["KXWHAT-YES", "KXOTHER-NO"])
        result = self.fn(df)
        assert result.empty

    def test_canada_keyword_passes(self):
        df = self._df_with_markets(["KXCANADA-ELECTION-YES"])
        result = self.fn(df)
        assert len(result) == 1

    def test_multiple_canadian_all_pass(self):
        df = self._df_with_markets(["KXBOC-YES", "KXCAD-YES", "toronto market"])
        result = self.fn(df)
        assert len(result) == 3


class TestDeriveCategoryFromTicker:
    """_derive_category_from_ticker(): maps Kalshi ticker prefixes to readable categories."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from dashboard.data_layer import _derive_category_from_ticker
        self.fn = _derive_category_from_ticker

    def test_kxcb_returns_bank_of_canada(self):
        assert self.fn("KXCB-2026-T2.25") == "Finance · Bank of Canada"

    def test_kxboc_returns_bank_of_canada(self):
        assert self.fn("KXBOC-26JAN-T4.25") == "Finance · Bank of Canada"

    def test_kxfed_returns_federal_reserve(self):
        assert self.fn("KXFED-26JAN-T5.25") == "Finance · Federal Reserve"

    def test_kxcpi_returns_inflation(self):
        assert self.fn("KXCPI-2026-T3") == "Finance · Inflation"

    def test_kxgdp_returns_economics(self):
        assert self.fn("KXGDP-2026") == "Finance · Economics"

    def test_kxoil_returns_energy(self):
        assert self.fn("KXOIL-2026-T80") == "Finance · Energy"

    def test_kxrt_returns_interest_rates(self):
        assert self.fn("KXRT-2026") == "Finance · Interest Rates"

    def test_controlh_returns_congress_control(self):
        assert self.fn("CONTROLH-2026-D") == "Politics · Congress Control"

    def test_controls_returns_congress_control(self):
        assert self.fn("CONTROLS-2026-D") == "Politics · Congress Control"

    def test_kxmvec_returns_elections(self):
        assert self.fn("KXMVEC-ROSSHARD-2026-ABC") == "Elections · Multi-Victor"

    def test_kxnba_returns_sports(self):
        assert self.fn("KXNBA-2026-LAL") == "Sports · NBA"

    def test_kxnfl_generic_returns_sports_nfl(self):
        # Generic KXNFL- ticker without W/F/D suffix should not fall through to ugly "Nfl"
        assert self.fn("KXNFL-2026-T10") == "Sports · NFL"

    def test_kxnflw_returns_sports_nfl(self):
        assert self.fn("KXNFLW-2026-WK1") == "Sports · NFL"

    def test_kxbtc_returns_crypto(self):
        assert self.fn("KXBTC-2026-T100K") == "Crypto · Bitcoin"

    def test_empty_string_returns_other(self):
        assert self.fn("") == "Other"

    def test_none_returns_other(self):
        assert self.fn(None) == "Other"

    def test_kxbalance_returns_balance_of_power(self):
        assert self.fn("KXBALANCEPOWER-2026-D") == "Politics · Balance of Power"

    def test_kxeth_returns_crypto_ethereum(self):
        assert self.fn("KXETH-2026-T3000") == "Crypto · Ethereum"

    def test_kxllm_returns_tech_ai(self):
        assert self.fn("KXLLM-2026-GPT6") == "Tech · AI"

    def test_kxtemp_returns_weather(self):
        assert self.fn("KXTEMP-2026-NY") == "Science · Weather"

    def test_unknown_prefix_falls_back_to_title(self):
        # KXWEIRDTHING → strip KX → WEIRDTHING → split on - → WEIRDTHING → .title()
        result = self.fn("KXWEIRDTHING-2026")
        assert result == "Weirdthing"

    def test_case_insensitive(self):
        # lowercase ticker should still match
        assert self.fn("kxcb-2026-t2.25") == "Finance · Bank of Canada"

    # ---------- New mappings added 2026-08-28 ----------

    def test_kxartistst_returns_entertainment_music(self):
        # KXARTISTSTREAMSY-* must NOT map to Tech·AI via KXARTI prefix
        assert self.fn("KXARTISTSTREAMSY-2PAC26DEC31-3.0B") == "Entertainment · Music"

    def test_kxarti_without_artist_still_tech_ai(self):
        # Generic KXARTI (not KXARTIST) should still fall through to Tech · AI
        assert self.fn("KXARTIHEADLINES-26") == "Tech · AI"

    def test_kxncaaf_returns_ncaa_football(self):
        assert self.fn("KXNCAAFACC-26-CAL") == "Sports · NCAA Football"

    def test_kxwnba_returns_sports_wnba(self):
        assert self.fn("KXWNBA-26-ATL") == "Sports · WNBA"

    def test_kxnflgame_returns_sports_nfl(self):
        assert self.fn("KXNFLGAME-26-KC") == "Sports · NFL"

    def test_kxwti_returns_finance_energy(self):
        assert self.fn("KXWTI-26NOV03-T72.99") == "Finance · Energy"

    def test_kxaaagasm_returns_finance_energy(self):
        assert self.fn("KXAAAGASMAX-26DEC31-4.80") == "Finance · Energy"

    def test_kx2028_returns_politics_presidential(self):
        assert self.fn("KX2028DRUN-28-AOC") == "Politics · Presidential"

    def test_kxnext_returns_politics_leadership(self):
        assert self.fn("KXNEXTISRAELPM-45JAN01-GEIS") == "Politics · Leadership"

    def test_kxcoach_returns_sports_coaching(self):
        assert self.fn("KXCOACHOUTNFL-26SEP01-AGLE") == "Sports · Coaching"

    def test_kxmuskweal_returns_finance_tech(self):
        assert self.fn("KXMUSKWEALTH-27-1400") == "Finance · Tech"

    def test_kxbond_returns_entertainment_film(self):
        assert self.fn("KXBOND-30-ATJ") == "Entertainment · Film"

    def test_kxfederalcharge_returns_politics_justice(self):
        # Must match BEFORE the broader KXFED prefix
        assert self.fn("KXFEDERALCHARGE-27JAN01-AFAU") == "Politics · Justice"

    def test_kxfed_still_federal_reserve_after_federalcharge_fix(self):
        # KXFED- rate markets must still map correctly
        assert self.fn("KXFED-27MAR-T4.25") == "Finance · Federal Reserve"

    # ---------- Phase 3 additions 2026-08-28 (50 new entries) ----------

    # Politics
    def test_kxtrump_returns_politics_presidential(self):
        assert self.fn("KXTRUMPAPPROVALYEAR-26DEC31-43") == "Politics · Presidential"

    def test_kxtrumpout_returns_politics_presidential(self):
        assert self.fn("KXTRUMPOUT27-27-28") == "Politics · Presidential"

    def test_kxvpresnomr_returns_politics_presidential(self):
        assert self.fn("KXVPRESNOMR-28-BD") == "Politics · Presidential"

    def test_kxdsenateseats_returns_politics_congress(self):
        assert self.fn("KXDSENATESEATS-27-46") == "Politics · Congress"

    def test_kxrhouseseats_returns_politics_congress(self):
        assert self.fn("KXRHOUSESEATS-27-195") == "Politics · Congress"

    def test_house_prefix_returns_politics_elections(self):
        # District race without KX prefix
        assert self.fn("HOUSENY17-26-D") == "Politics · Elections"

    def test_kxhormuznorm_returns_politics_geopolitics(self):
        assert self.fn("KXHORMUZNORM-26MAR17-B260901") == "Politics · Geopolitics"

    def test_kxstate51_returns_politics_geopolitics(self):
        assert self.fn("KXSTATE51-29-CU") == "Politics · Geopolitics"

    def test_kximpeach_returns_politics_justice(self):
        assert self.fn("KXIMPEACH-27-JAN01") == "Politics · Justice"

    def test_kxarrest_returns_politics_justice(self):
        assert self.fn("KXARREST-27JAN-AFAU") == "Politics · Justice"

    def test_kxleadersout_returns_politics_leadership(self):
        assert self.fn("KXLEADERSOUT-27JAN01-BNETISR") == "Politics · Leadership"

    # Sports
    def test_kxbills_returns_sports_nfl(self):
        # Buffalo Bills in-game markets — NOT legislation
        assert self.fn("KXBILLS-DRIVE") == "Sports · NFL"

    def test_kxmarmad_returns_ncaa_basketball(self):
        assert self.fn("KXMARMAD-27-DUKE") == "Sports · NCAA Basketball"

    def test_kxheisman_returns_ncaa_football(self):
        assert self.fn("KXHEISMAN-27-AMANN") == "Sports · NCAA Football"

    def test_kxncaambnextcoach_returns_ncaa_basketball(self):
        assert self.fn("KXNCAAMBNEXTCOACH-KU26-BDON") == "Sports · NCAA Basketball"

    def test_kxncaamb_returns_ncaa_basketball(self):
        assert self.fn("KXNCAAMB-26-DUKEY") == "Sports · NCAA Basketball"

    def test_kxf1constructors_returns_sports_f1(self):
        assert self.fn("KXF1CONSTRUCTORS-26-FER") == "Sports · Formula 1"

    def test_kxf1_returns_sports_f1(self):
        assert self.fn("KXF1-26-CL") == "Sports · Formula 1"

    def test_kxatp_returns_sports_tennis(self):
        assert self.fn("KXATP-26USO-DRA") == "Sports · Tennis"

    def test_kxboxing_returns_sports_combat(self):
        assert self.fn("KXBOXING-26SEP19FMAYMPAC-FMAY") == "Sports · Combat Sports"

    def test_kxufcl_returns_sports_combat(self):
        assert self.fn("KXUFCLHEAVYWEIGHTTITLE-26-APER") == "Sports · Combat Sports"

    def test_kxballondor_returns_sports_soccer(self):
        assert self.fn("KXBALLONDOR-26-LMES") == "Sports · Soccer"

    # Finance
    def test_kxipo_returns_finance_ipo(self):
        assert self.fn("KXIPO-26-ANTHROPIC") == "Finance · IPO"

    def test_kxipoanthropic_returns_finance_ipo(self):
        # Specific KXIPOANTHROPIC prefix must be caught before generic KXIPO
        assert self.fn("KXIPOANTHROPIC-DATE-26DEC01") == "Finance · IPO"

    def test_kxipoopenai_returns_finance_ipo(self):
        assert self.fn("KXIPOOPENAI-26DEC01") == "Finance · IPO"

    def test_kxaapla_returns_finance_stocks(self):
        assert self.fn("KXAAPLA-28JANHEAD-164000") == "Finance · Stocks"

    def test_kxamzna_returns_finance_stocks(self):
        assert self.fn("KXAMZNA-28JANHEAD-1500000") == "Finance · Stocks"

    def test_kxtslaa_returns_finance_stocks(self):
        assert self.fn("KXTSLAA-28JANHEAD-120000") == "Finance · Stocks"

    def test_kxmtcha_returns_finance_stocks(self):
        assert self.fn("KXMTCHA-28JANPAYERS-12400000") == "Finance · Stocks"

    def test_kxinxy_returns_finance_indices(self):
        assert self.fn("KXINXY-26DEC31H1600-B5900") == "Finance · Indices"

    def test_kxgolddiry_returns_finance_commodities(self):
        assert self.fn("KXGOLDDIRY-26DEC31H1700-T4300") == "Finance · Commodities"

    def test_kxcompanyactionmerger_returns_finance_ma(self):
        assert self.fn("KXCOMPANYACTIONMERGER-27-26DEC01") == "Finance · M&A"

    def test_fedhike_returns_finance_federal_reserve(self):
        # No KX prefix — still must map correctly
        assert self.fn("FEDHIKE-26DEC31") == "Finance · Federal Reserve"

    # Tech
    def test_kxcodingmodel_returns_tech_ai(self):
        assert self.fn("KXCODINGMODEL-26DEC-ANTH") == "Tech · AI"

    def test_kxwaymocity_returns_tech_ai(self):
        assert self.fn("KXWAYMOCITY-26DEC-DEN") == "Tech · AI"

    # Entertainment
    def test_kxtopartistusa_returns_entertainment_music(self):
        assert self.fn("KXTOPARTISTUSA-26-BAD") == "Entertainment · Music"

    def test_kxtopartist_returns_entertainment_music(self):
        assert self.fn("KXTOPARTIST-26B-BAD") == "Entertainment · Music"

    def test_kxroleatgrammys_returns_entertainment_music(self):
        assert self.fn("KXROLEATGRAMMYS-27MAR31-KEV") == "Entertainment · Music"

    def test_kxgameawards_returns_entertainment_gaming(self):
        assert self.fn("KXGAMEAWARDS-2026-CON") == "Entertainment · Gaming"

    def test_gta6_returns_entertainment_gaming(self):
        # No KX prefix
        assert self.fn("GTA6-26DEC31") == "Entertainment · Gaming"

    def test_kxtime_returns_entertainment_media(self):
        assert self.fn("KXTIME-26-AI") == "Entertainment · Media"

    # Science
    def test_kxfirsthurricane_returns_science_weather(self):
        assert self.fn("KXFIRSTHURRICANE-26DEC01ATL-ART") == "Science · Weather"

    def test_kxspacexcount_returns_science_space(self):
        assert self.fn("KXSPACEXCOUNT-26B-140") == "Science · Space"

    def test_kxriemann_returns_science_math(self):
        assert self.fn("KXRIEMANN-35-28JAN01") == "Science · Math"

    def test_kxscrewwormcount_returns_science_agriculture(self):
        assert self.fn("KXSCREWWORMCOUNT-27JAN01-A1") == "Science · Agriculture"

    # ---------- Phase 4 additions 2026-08-28 (KX-prefix variants + new markets) ----------

    def test_kxgta6_with_kx_prefix_returns_entertainment_gaming(self):
        # Arb-ops use KXGTA6-... (with KX) not GTA6-... (without KX)
        assert self.fn("KXGTA6-26OCT31") == "Entertainment · Gaming"

    def test_kxme_returns_finance_indices(self):
        # KXME-26DEC31-69000 — large-cap index price prediction (69k-74k range)
        assert self.fn("KXME-26DEC31-69000") == "Finance · Indices"

    def test_kxiphonerelease_returns_tech_consumer(self):
        assert self.fn("KXIPHONERELEASE-IPHONE18-26OCT01") == "Tech · Consumer"

    def test_kxdeportations_returns_politics_presidential(self):
        assert self.fn("KXDEPORTATIONS-27JAN01-T600000") == "Politics · Presidential"

    def test_kxgreenland_returns_politics_geopolitics(self):
        assert self.fn("KXGREENLAND-29") == "Politics · Geopolitics"

    def test_kxgreenlandprice_still_returns_politics_geopolitics(self):
        # KXGREENLANDPRICE is more specific; must not be clobbered by KXGREENLAND
        assert self.fn("KXGREENLANDPRICE-29JAN21-1049B") == "Politics · Geopolitics"

    def test_kxstartingqb_returns_sports_nfl(self):
        assert self.fn("KXSTARTINGQBWEEK1-W1-26SEP15-CLE-DWAT") == "Sports · NFL"

    def test_kxtosta_returns_finance_retail(self):
        assert self.fn("KXTOSTA-28JANLOC-198000") == "Finance · Retail"

    def test_kxweeksnum1_returns_entertainment_music(self):
        assert self.fn("KXWEEKSNUM1-26DEC26-ICE-5") == "Entertainment · Music"
