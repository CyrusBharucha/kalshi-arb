"""
tests/test_db_models.py
========================
Unit tests for database/models.py:
  - All ORM model classes importable
  - __tablename__ attributes match expected names
  - Key columns exist on each model (accessed via __table__.c attribute names)

No DB connection — only SQLAlchemy ORM introspection.
"""
from __future__ import annotations

import pytest


class TestModelTableNames:
    """Each model class has the expected __tablename__."""

    def _get_models(self):
        from database.models import (
            Event, Market, MarketSnapshot, Candlestick,
            OrderBookSnapshot, Trade, ContractRelationship,
            ArbitrageOpportunity, BacktestTrade, ExternalMarketData,
            MacroEvent, CrossAssetSpread, IngestionLog,
        )
        return {
            "events":                   Event,
            "markets":                  Market,
            "market_snapshots":         MarketSnapshot,
            "candlesticks":             Candlestick,
            "order_book_snapshots":     OrderBookSnapshot,
            "trades":                   Trade,
            "contract_relationships":   ContractRelationship,
            "arbitrage_opportunities":  ArbitrageOpportunity,
            "backtest_trades":          BacktestTrade,
            "external_market_data":     ExternalMarketData,
            "macro_events":             MacroEvent,
            "cross_asset_spreads":      CrossAssetSpread,
            "ingestion_log":            IngestionLog,
        }

    def test_all_models_importable(self):
        models = self._get_models()
        assert len(models) > 0

    def test_table_names_correct(self):
        for expected_name, model_cls in self._get_models().items():
            assert model_cls.__tablename__ == expected_name, (
                f"{model_cls.__name__}: expected '{expected_name}', "
                f"got '{model_cls.__tablename__}'"
            )


class TestEventModel:
    """Event model columns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.models import Event
        self.model = Event

    def _col_names(self):
        return {c.name for c in self.model.__table__.c}

    def test_has_event_ticker(self):
        assert "event_ticker" in self._col_names()

    def test_has_title(self):
        assert "title" in self._col_names()

    def test_has_status(self):
        assert "status" in self._col_names()

    def test_has_canadian_relevance(self):
        assert "canadian_relevance" in self._col_names()

    def test_has_created_at(self):
        assert "created_at" in self._col_names()


class TestMarketModel:
    """Market model columns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.models import Market
        self.model = Market

    def _col_names(self):
        return {c.name for c in self.model.__table__.c}

    def test_has_market_id(self):
        assert "market_id" in self._col_names()

    def test_has_ticker(self):
        assert "ticker" in self._col_names()

    def test_has_status(self):
        assert "status" in self._col_names()

    def test_has_event_ticker(self):
        assert "event_ticker" in self._col_names()

    def test_has_floor_strike(self):
        assert "floor_strike" in self._col_names()

    def test_has_cap_strike(self):
        assert "cap_strike" in self._col_names()


class TestTradeModel:
    """Trade model columns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.models import Trade
        self.model = Trade

    def _col_names(self):
        return {c.name for c in self.model.__table__.c}

    def test_has_trade_id(self):
        assert "trade_id" in self._col_names()

    def test_has_market_id(self):
        assert "market_id" in self._col_names()

    def test_has_price(self):
        assert "price" in self._col_names()

    def test_has_quantity(self):
        assert "quantity" in self._col_names()

    def test_has_trade_ts(self):
        assert "trade_ts" in self._col_names()


class TestContractRelationshipModel:
    """ContractRelationship model columns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.models import ContractRelationship
        self.model = ContractRelationship

    def _col_names(self):
        return {c.name for c in self.model.__table__.c}

    def test_has_market_id_1(self):
        assert "market_id_1" in self._col_names()

    def test_has_market_id_2(self):
        assert "market_id_2" in self._col_names()

    def test_has_relationship_type(self):
        assert "relationship_type" in self._col_names()

    def test_has_confidence(self):
        assert "confidence" in self._col_names()


class TestArbitrageOpportunityModel:
    """ArbitrageOpportunity model columns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from database.models import ArbitrageOpportunity
        self.model = ArbitrageOpportunity

    def _col_names(self):
        return {c.name for c in self.model.__table__.c}

    def test_has_opportunity_id(self):
        assert "opportunity_id" in self._col_names()

    def test_has_net_edge(self):
        assert "net_edge" in self._col_names()

    def test_has_gross_edge(self):
        assert "gross_edge" in self._col_names()

    def test_has_strategy_type(self):
        assert "strategy_type" in self._col_names()

    def test_has_detected_at(self):
        assert "detected_at" in self._col_names()

    def test_has_classification(self):
        assert "classification" in self._col_names()
