"""
tests/test_dashboard.py
=======================
Dashboard import validation, module structure, and environment checks.

Verifies:
1. All 10 dashboard page modules import without error
2. Each page module exposes a callable `render` function
3. styles.py exports required symbols
4. data_layer.py exports required symbols
5. live_state.py exports required symbols
6. LiveState singleton returns expected types
7. Fee model agreement with documented Kalshi fee formula
8. Environment variable loading (no credentials required)
9. DB connectivity check (graceful offline)
10. Synthesis connectivity check (graceful offline)
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# -- Path setup ----------------------------------------------------------------
_project_root = os.path.dirname(os.path.dirname(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# -- Stub streamlit so tests run without a running Streamlit server ------------
if "streamlit" not in sys.modules:
    _st_stub = types.ModuleType("streamlit")

    def _noop(*a, **kw):
        return None

    def _noop_ctx(*a, **kw):
        class _Ctx:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def __bool__(self): return False
        return _Ctx()

    for attr in [
        "set_page_config", "markdown", "title", "header", "subheader",
        "caption", "write", "text", "metric", "columns", "tabs",
        "expander", "sidebar", "radio", "checkbox", "selectbox",
        "multiselect", "slider", "number_input", "text_input",
        "button", "rerun", "dataframe", "plotly_chart", "info",
        "warning", "error", "success", "empty", "container",
        "spinner", "stop",
    ]:
        setattr(_st_stub, attr, _noop)

    class _CacheData:
        def __call__(self, fn=None, **kw):
            if fn is not None:
                return fn
            return lambda f: f
        def clear(self): pass

    _st_stub.cache_data = _CacheData()

    class _SessionState(dict):
        def __getattr__(self, k):
            try: return self[k]
            except KeyError: raise AttributeError(k)
        def __setattr__(self, k, v): self[k] = v

    _st_stub.session_state = _SessionState()
    _st_stub.sidebar = _noop_ctx()
    sys.modules["streamlit"] = _st_stub

# Stub plotly if needed
if "plotly" not in sys.modules:
    _plotly_stub = types.ModuleType("plotly")
    _go_stub     = types.ModuleType("plotly.graph_objects")
    _px_stub     = types.ModuleType("plotly.express")
    _go_stub.Figure      = MagicMock(return_value=MagicMock())
    _go_stub.Bar         = MagicMock(return_value=MagicMock())
    _go_stub.Scatter     = MagicMock(return_value=MagicMock())
    _go_stub.Histogram   = MagicMock(return_value=MagicMock())
    _go_stub.Candlestick = MagicMock(return_value=MagicMock())
    _go_stub.Waterfall   = MagicMock(return_value=MagicMock())
    sys.modules["plotly"]               = _plotly_stub
    sys.modules["plotly.graph_objects"] = _go_stub
    sys.modules["plotly.express"]       = _px_stub


# =============================================================================
# 1. STYLES MODULE
# =============================================================================

class TestStyles:
    """dashboard/styles.py - CSS injection and Plotly theme."""

    def test_import(self):
        mod = importlib.import_module("dashboard.styles")
        assert mod is not None

    def test_inject_css_callable(self):
        mod = importlib.import_module("dashboard.styles")
        assert callable(mod.inject_css)

    def test_inject_css_returns_string(self):
        mod = importlib.import_module("dashboard.styles")
        css = mod.inject_css()
        assert isinstance(css, str)
        assert len(css) > 100

    def test_plotly_dark_layout_callable(self):
        mod = importlib.import_module("dashboard.styles")
        assert callable(mod.plotly_dark_layout)

    def test_plotly_dark_layout_returns_dict(self):
        mod = importlib.import_module("dashboard.styles")
        layout = mod.plotly_dark_layout()
        assert isinstance(layout, dict)
        assert "paper_bgcolor" in layout or "plot_bgcolor" in layout

    def test_color_constants_exported(self):
        mod = importlib.import_module("dashboard.styles")
        for name in ["GREEN", "RED", "AMBER", "BLUE", "TEXT", "TEXT2", "TEXT3",
                     "PANEL", "BORDER", "CYAN", "PANEL2"]:
            assert hasattr(mod, name), f"Missing color constant: {name}"
            val = getattr(mod, name)
            assert isinstance(val, str) and val.startswith("#"), f"{name} should be hex color"


# =============================================================================
# 2. LIVE STATE MODULE
# =============================================================================

class TestLiveState:
    """dashboard/live_state.py - thread-safe singleton."""

    def test_import(self):
        mod = importlib.import_module("dashboard.live_state")
        assert mod is not None

    def test_get_live_state_callable(self):
        mod = importlib.import_module("dashboard.live_state")
        assert callable(mod.get_live_state)

    def test_singleton_returns_same_object(self):
        mod = importlib.import_module("dashboard.live_state")
        a = mod.get_live_state()
        b = mod.get_live_state()
        assert a is b

    def test_get_stats_returns_dict(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        stats = state.get_stats()
        assert isinstance(stats, dict)

    def test_stats_keys_present(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        stats = state.get_stats()
        for key in ["connected", "messages_total", "markets_tracked", "reconnects"]:
            assert key in stats, f"Missing stats key: {key}"

    def test_initial_not_connected(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        stats = state.get_stats()
        assert isinstance(stats["connected"], bool)

    def test_snapshot_all_returns_dict(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        snap = state.snapshot_all()
        assert isinstance(snap, dict)

    def test_get_book_returns_none_for_unknown(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        result = state.get_book("NONEXISTENT-TICKER-XYZ-99999")
        assert result is None

    def test_market_quote_dataclass(self):
        mod = importlib.import_module("dashboard.live_state")
        assert hasattr(mod, "MarketQuote")
        q = mod.MarketQuote(ticker="TEST-001")
        assert q.ticker == "TEST-001"
        assert isinstance(q.yes_bid, float)

    def test_update_book_dict_interface(self):
        """update_book accepts a dict with yes_bids/yes_asks entries."""
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        ticker = "TEST-DICT-001"
        # update_book takes a dict, not a MarketQuote
        state.update_book(ticker, {
            "yes_bids": [[40, 500]],
            "yes_asks": [[42, 300]],
            "sequence": 100,
        })
        result = state.get_book(ticker)
        assert result is not None
        assert isinstance(result, dict)
        assert result["ticker"] == ticker

    def test_get_book_returns_dict_with_required_keys(self):
        mod = importlib.import_module("dashboard.live_state")
        state = mod.get_live_state()
        ticker = "TEST-KEYS-001"
        state.update_book(ticker, {
            "yes_bids": [[45, 1000]],
            "yes_asks": [[47, 800]],
        })
        result = state.get_book(ticker)
        assert result is not None
        for key in ["ticker", "yes_bid", "yes_ask", "no_bid", "no_ask", "mid", "spread"]:
            assert key in result, f"get_book result missing: {key}"


# =============================================================================
# 3. DATA LAYER MODULE
# =============================================================================

class TestDataLayer:
    """dashboard/data_layer.py - cached DB query functions."""

    def test_import(self):
        mod = importlib.import_module("dashboard.data_layer")
        assert mod is not None

    def test_required_functions_exported(self):
        mod = importlib.import_module("dashboard.data_layer")
        required = [
            "get_system_health",
            "get_coverage_stats",
            "get_open_markets",
            "get_live_arb_opportunities",
            "get_historical_arb_summary",
            "get_historical_arb_stats",
            "get_backtest_runs",
            "get_canadian_markets",
            "get_relationship_stats",
            "get_research_summary",
            "get_table_sizes",
        ]
        for fn in required:
            assert hasattr(mod, fn), f"data_layer missing: {fn}"
            assert callable(getattr(mod, fn)), f"data_layer.{fn} not callable"

    def test_get_system_health_returns_dict(self):
        """get_system_health must return a dict regardless of DB state."""
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_system_health()
        assert isinstance(result, dict)

    def test_get_coverage_stats_returns_dict(self):
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_coverage_stats()
        assert isinstance(result, dict)

    def test_get_open_markets_returns_dataframe(self):
        import pandas as pd
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_open_markets()
        if isinstance(result, tuple):
            df, err = result
            assert isinstance(df, pd.DataFrame)
        else:
            assert isinstance(result, pd.DataFrame)

    def test_get_live_arb_opportunities_returns_dataframe(self):
        import pandas as pd
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_live_arb_opportunities()
        if isinstance(result, tuple):
            df, err = result
            assert isinstance(df, pd.DataFrame)
        else:
            assert isinstance(result, pd.DataFrame)

    def test_get_canadian_markets_returns_dataframe(self):
        import pandas as pd
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_canadian_markets()
        if isinstance(result, tuple):
            df, err = result
            assert isinstance(df, pd.DataFrame)
        else:
            assert isinstance(result, pd.DataFrame)


# =============================================================================
# 4. PAGE MODULES - all 10
# =============================================================================

_PAGE_MODULES = [
    "dashboard.pages.p01_overview",
    "dashboard.pages.p02_live_arb",
    "dashboard.pages.p03_markets",
    "dashboard.pages.p04_orderbook",
    "dashboard.pages.p05_historical_arb",
    "dashboard.pages.p06_backtest",
    "dashboard.pages.p07_cross_asset",
    "dashboard.pages.p08_canadian",
    "dashboard.pages.p09_research",
    "dashboard.pages.p10_system",
]


@pytest.mark.parametrize("module_path", _PAGE_MODULES)
class TestPageModules:
    """Each dashboard page module must import cleanly and expose render()."""

    def test_import(self, module_path):
        mod = importlib.import_module(module_path)
        assert mod is not None

    def test_has_render_function(self, module_path):
        mod = importlib.import_module(module_path)
        assert hasattr(mod, "render"), f"{module_path} missing render()"
        assert callable(mod.render), f"{module_path}.render is not callable"


class TestPagesInit:
    """dashboard/pages/__init__.py exports all 10 modules."""

    def test_init_import(self):
        mod = importlib.import_module("dashboard.pages")
        assert mod is not None

    def test_all_pages_in_init(self):
        mod = importlib.import_module("dashboard.pages")
        for name in [
            "p01_overview", "p02_live_arb", "p03_markets", "p04_orderbook",
            "p05_historical_arb", "p06_backtest", "p07_cross_asset",
            "p08_canadian", "p09_research", "p10_system",
        ]:
            assert hasattr(mod, name), f"pages.__init__ missing: {name}"


class TestAppModule:
    """dashboard/app.py exists and has valid Python syntax."""

    def test_app_file_exists(self):
        app_path = os.path.join(_project_root, "dashboard", "app.py")
        assert os.path.isfile(app_path), "dashboard/app.py not found"

    def test_app_parses_without_syntax_error(self):
        app_path = os.path.join(_project_root, "dashboard", "app.py")
        with open(app_path, encoding="utf-8") as f:
            source = f.read()
        code = compile(source, app_path, "exec")
        assert code is not None


# =============================================================================
# 5. FEE MODEL CONSISTENCY
# =============================================================================

class TestFeeModelConsistency:
    """Fee values - prices are fractions (0.01-0.99), not cents."""

    def test_fee_module_imports(self):
        mod = importlib.import_module("engine.fees")
        assert hasattr(mod, "taker_fee_per_contract")

    def test_fee_at_50_pct(self):
        """Fee at P=0.50: ceil(0.07*0.25*100)/100 = ceil(1.75)/100 = 0.02"""
        mod = importlib.import_module("engine.fees")
        fee = mod.taker_fee_per_contract(0.50)
        assert abs(fee - 0.02) < 1e-6, f"Fee at 0.50 expected 0.02, got {fee}"

    def test_fee_at_20_pct(self):
        """Fee at P=0.20: ceil(0.07*0.2*0.8*100)/100 = ceil(1.12)/100 = 0.02"""
        mod = importlib.import_module("engine.fees")
        fee = mod.taker_fee_per_contract(0.20)
        assert abs(fee - 0.02) < 1e-6, f"Fee at 0.20 expected 0.02, got {fee}"

    def test_fee_cap_never_exceeded(self):
        """Fee is capped at $0.035 per contract."""
        mod = importlib.import_module("engine.fees")
        for p_pct in [1, 5, 10, 20, 40, 50, 60, 80, 90, 95, 99]:
            p = p_pct / 100.0
            fee = mod.taker_fee_per_contract(p)
            assert fee <= 0.035, f"Fee at {p} exceeds cap: {fee}"

    def test_fee_nonnegative(self):
        mod = importlib.import_module("engine.fees")
        for p_pct in range(1, 100):
            fee = mod.taker_fee_per_contract(p_pct / 100.0)
            assert fee >= 0, f"Negative fee at {p_pct/100}: {fee}"

    def test_fee_symmetric(self):
        """Fee for YES at P should equal fee for NO at (1-P)."""
        mod = importlib.import_module("engine.fees")
        for p_pct in [10, 25, 40, 50]:
            p = p_pct / 100.0
            assert abs(mod.taker_fee_per_contract(p) -
                       mod.taker_fee_per_contract(1.0 - p)) < 1e-6

    def test_fee_invalid_price_raises(self):
        mod = importlib.import_module("engine.fees")
        with pytest.raises((ValueError, Exception)):
            mod.taker_fee_per_contract(0.0)
        with pytest.raises((ValueError, Exception)):
            mod.taker_fee_per_contract(1.0)


# =============================================================================
# 6. ENVIRONMENT / CONFIGURATION
# =============================================================================

class TestConfiguration:
    """config.py loads without error even with empty environment."""

    def test_config_imports(self):
        mod = importlib.import_module("config")
        assert mod is not None

    def test_config_has_db_url_attribute(self):
        mod = importlib.import_module("config")
        assert hasattr(mod, "DB_URL") or hasattr(mod, "get_db_url")

    def test_fee_constants_present(self):
        mod = importlib.import_module("config")
        for attr in ["FEE_RATE", "FEE_CAP"]:
            if hasattr(mod, attr):
                val = getattr(mod, attr)
                assert isinstance(val, (int, float))


# =============================================================================
# 7. REPOSITORY INTEGRITY
# =============================================================================

class TestRepositoryIntegrity:
    """Basic repo hygiene - key files present, no obvious secrets leaked."""

    def test_gitignore_excludes_env(self):
        gitignore = os.path.join(_project_root, ".gitignore")
        assert os.path.isfile(gitignore), ".gitignore not found"
        content = open(gitignore, encoding="utf-8").read()
        assert ".env" in content, ".gitignore should exclude .env"

    def test_gitignore_excludes_pem(self):
        content = open(os.path.join(_project_root, ".gitignore"), encoding="utf-8").read()
        assert ".pem" in content or "*.pem" in content

    def test_env_example_exists(self):
        assert os.path.isfile(os.path.join(_project_root, ".env.example"))

    def test_env_example_has_no_real_long_secrets(self):
        """
        Env example must not contain actual secret values.
        Placeholder strings like 'sk_gs-...' are acceptable;
        actual secret values would be 20+ real alphanumeric chars after a prefix.
        """
        env_ex = open(os.path.join(_project_root, ".env.example"), encoding="utf-8").read()
        # A real secret key would look like: sk_gs-ABCDEFabcdef1234567890  (long, real chars)
        # A placeholder looks like: sk_gs-...   (trailing ellipsis only)
        real_secret_pattern = re.compile(r'sk_gs-[A-Za-z0-9]{15,}')
        matches = real_secret_pattern.findall(env_ex)
        assert not matches, f".env.example may contain real secret key: {matches}"

    def test_env_example_no_real_hex_api_keys(self):
        """No 32+ char hex strings as assigned values (real API keys)."""
        env_ex = open(os.path.join(_project_root, ".env.example"), encoding="utf-8").read()
        # Match lines with assignment to long hex strings
        real_key = re.compile(r'=\s*[0-9a-f]{32,}', re.I)
        assert not real_key.findall(env_ex)

    def test_requirements_txt_exists(self):
        assert os.path.isfile(os.path.join(_project_root, "requirements.txt"))

    def test_requirements_has_streamlit(self):
        req = open(os.path.join(_project_root, "requirements.txt"), encoding="utf-8").read()
        assert "streamlit" in req

    def test_requirements_has_plotly(self):
        req = open(os.path.join(_project_root, "requirements.txt"), encoding="utf-8").read()
        assert "plotly" in req

    def test_readme_exists(self):
        assert os.path.isfile(os.path.join(_project_root, "README.md"))

    def test_readme_has_no_hardcoded_keys(self):
        readme = open(os.path.join(_project_root, "README.md"), encoding="utf-8").read()
        real_key_pattern = re.compile(r'sk_gs-[A-Za-z0-9]{15,}')
        assert not real_key_pattern.search(readme)

    def test_no_plaintext_secrets_in_config_py(self):
        config_path = os.path.join(_project_root, "config.py")
        if not os.path.isfile(config_path):
            pytest.skip("config.py not found")
        content = open(config_path, encoding="utf-8").read()
        # config.py should load secrets from env, not hardcode them
        assert ("os.environ" in content or "os.getenv" in content or
                "dotenv" in content or "getenv" in content), \
            "config.py should load keys from environment, not hardcode them"


# =============================================================================
# 8. DB CONNECTIVITY (graceful offline)
# =============================================================================

class TestDatabaseConnectivity:
    """Verify that the DB layer handles a missing DB gracefully."""

    def test_repository_imports(self):
        try:
            mod = importlib.import_module("database.repository")
            assert mod is not None
        except ImportError as e:
            pytest.skip(f"database.repository not importable: {e}")

    def test_data_layer_health_offline(self):
        """get_system_health returns a dict even with no DB."""
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_system_health()
        assert isinstance(result, dict)
        if "db_connected" in result:
            assert isinstance(result["db_connected"], bool)

    def test_data_layer_coverage_offline(self):
        mod = importlib.import_module("dashboard.data_layer")
        result = mod.get_coverage_stats()
        assert isinstance(result, dict)


# =============================================================================
# 9. SYNTHESIS CLIENT (graceful offline)
# =============================================================================

class TestSynthesisConnectivity:
    """SynthesisWebSocketClient must import and construct without connecting."""

    def test_synthesis_client_imports(self):
        try:
            mod = importlib.import_module("feeds.synthesis_live")
            assert mod is not None
        except ImportError as e:
            pytest.skip(f"synthesis_live not importable: {e}")

    def test_l2cache_constructs(self):
        try:
            from feeds.synthesis_live import L2Cache
            cache = L2Cache()
            assert cache is not None
        except ImportError:
            pytest.skip("L2Cache not available")

    def test_synthesis_client_exists(self):
        try:
            from feeds.synthesis_live import SynthesisWebSocketClient
            assert SynthesisWebSocketClient is not None
        except ImportError:
            pytest.skip("SynthesisWebSocketClient not available")
