"""
conftest.py
===========
Shared pytest configuration and hooks for the Kalshi arb test suite.
"""
import functools

import pytest

# ---------------------------------------------------------------------------
# Disable Streamlit's memoisation for the whole test session.
#
# ``dashboard.data_layer`` wraps every query helper in ``@st.cache_data(ttl=..)``.
# Under pytest that causes two distinct problems:
#   1. A value memoised while the DB engine was patched one way leaks into the
#      next test that patches it a different way (order-dependent failures).
#   2. ``cache_data`` pickles every return value; tests that inject ``MagicMock``
#      rows blow up with ``CacheError: Failed to pickle ...``.
# Replacing the decorators with pass-throughs *before* any dashboard module is
# imported keeps the production code untouched while making tests deterministic.
# ---------------------------------------------------------------------------
def _install_noop_streamlit_caches():
    try:
        import streamlit as st
    except Exception:  # pragma: no cover - streamlit always present in CI
        return

    def _passthrough(func=None, **_kwargs):
        def _decorate(fn):
            @functools.wraps(fn)
            def _wrapper(*args, **kwargs):
                return fn(*args, **kwargs)
            _wrapper.clear = lambda *a, **k: None
            return _wrapper
        if callable(func):
            return _decorate(func)
        return _decorate

    _passthrough.clear = lambda *a, **k: None
    st.cache_data = _passthrough
    st.cache_resource = _passthrough


_install_noop_streamlit_caches()

# Prevent pytest from collecting source modules that happen to contain
# functions starting with "test_" (statistical hypothesis test helpers).
collect_ignore = [
    "cross_asset/event_study.py",
]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "perf: performance / throughput benchmark (pass --run-perf to enable)",
    )
    config.addinivalue_line(
        "markers",
        "integration: live API integration test (pass --run-integration to enable)",
    )


def pytest_addoption(parser):
    parser.addoption(
        "--run-perf",
        action="store_true",
        default=False,
        help="Run performance / throughput benchmark tests (slow).",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run live API integration tests (requires API keys).",
    )


def pytest_collection_modifyitems(config, items):
    skip_perf = pytest.mark.skip(reason="Pass --run-perf to run performance tests")
    skip_intg = pytest.mark.skip(reason="Pass --run-integration to run live API tests")
    run_perf  = config.getoption("--run-perf")
    run_intg  = config.getoption("--run-integration")
    for item in items:
        if item.get_closest_marker("perf") and not run_perf:
            item.add_marker(skip_perf)
        if item.get_closest_marker("integration") and not run_intg:
            item.add_marker(skip_intg)
