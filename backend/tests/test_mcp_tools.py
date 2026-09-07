"""Tests for the run_backtest / generate_pine_script MCP tools.

`generate_pine_script` is pure (no DB, no auth) so it's tested directly.
`run_backtest` needs a real DB row for the API key plus price/headline data
to simulate against — seeded straight through SQLAlchemy the same way
test_smoke.py relies on a real Postgres being reachable at DATABASE_URL.
"""
import hashlib
import random
from datetime import datetime, timedelta

import pytest

from app import models
from app.database import SessionLocal
from app.mcp_server import generate_pine_script, run_backtest

TEST_TICKER = "MCPTESTBTC"
RAW_API_KEY = "sfx_test_mcp_tools_key"


class _FakeRequest:
    def __init__(self, api_key: str | None):
        self.headers = {"x-api-key": api_key} if api_key else {}


class _FakeRequestContext:
    def __init__(self, api_key: str | None):
        self.request = _FakeRequest(api_key)


class _FakeCtx:
    """Stands in for mcp.server.fastmcp.Context — only the
    `.request_context.request.headers` path _open_authed_session reads."""

    def __init__(self, api_key: str | None = RAW_API_KEY):
        self.request_context = _FakeRequestContext(api_key)


@pytest.fixture
def seeded_ticker():
    """365 days of synthetic prices + sentiment for a scratch ticker, plus
    an unlimited API key that authenticates as RAW_API_KEY. Cleans up
    everything it inserted afterwards regardless of test outcome.
    """
    db = SessionLocal()
    random.seed(7)
    price = 100.0
    now = datetime.utcnow()
    for i in range(365, -1, -1):
        d = now - timedelta(days=i)
        price *= 1 + random.uniform(-0.02, 0.022)
        db.add(models.Price(
            ticker=TEST_TICKER, close_price=price, open_price=price,
            high_price=price * 1.01, low_price=price * 0.99,
            volume=1000, date=d,
        ))
        if i % 2 == 0:
            score = random.uniform(-1, 1)
            db.add(models.Headline(
                ticker=TEST_TICKER, title=f"test headline {i}", source="test",
                url="http://example.com", sentiment_score=score,
                sentiment_label="positive" if score > 0 else "negative",
                published_at=d,
            ))
    db.add(models.APIKey(
        key_hash=hashlib.sha256(RAW_API_KEY.encode()).hexdigest(),
        key_prefix="sfx_test", email="mcp-tools-test@example.com",
        active=True, unlimited=True,
    ))
    db.commit()
    db.close()

    yield

    db = SessionLocal()
    db.query(models.Price).filter(models.Price.ticker == TEST_TICKER).delete()
    db.query(models.Headline).filter(models.Headline.ticker == TEST_TICKER).delete()
    db.query(models.APIKey).filter(
        models.APIKey.key_hash == hashlib.sha256(RAW_API_KEY.encode()).hexdigest()
    ).delete()
    db.commit()
    db.close()


def test_generate_pine_script_is_a_real_donchian_strategy():
    result = generate_pine_script("btc", donchian_n=15)
    assert result["ticker"] == "BTC"
    assert result["donchian_n"] == 15
    script = result["pine_script"]
    assert script.startswith("//@version=5")
    assert "strategy(" in script
    assert "ta.highest(close, n)" in script
    assert "ta.lowest(close, n)" in script
    assert "strategy.entry(" in script
    assert "input.int(15" in script
    # Honest about what it can't export.
    assert "shift" in result["note"] and "divergence" in result["note"]


def test_generate_pine_script_clamps_lookback():
    result = generate_pine_script("ETH", donchian_n=1000)
    assert result["donchian_n"] == 200
    result = generate_pine_script("ETH", donchian_n=1)
    assert result["donchian_n"] == 5


def test_run_backtest_rejects_bad_signal():
    with pytest.raises(ValueError, match="signal must be"):
        run_backtest(_FakeCtx(api_key=None), ticker=TEST_TICKER, signal="nonsense")


def test_run_backtest_rejects_bad_direction_mode():
    with pytest.raises(ValueError, match="direction_mode must be"):
        run_backtest(_FakeCtx(api_key=None), ticker=TEST_TICKER, direction_mode="sideways")


def test_run_backtest_requires_api_key():
    with pytest.raises(ValueError, match="Missing X-API-Key"):
        run_backtest(_FakeCtx(api_key=None), ticker=TEST_TICKER, signal="donchian")


def test_run_backtest_rejects_unknown_api_key():
    with pytest.raises(ValueError, match="Invalid or revoked"):
        run_backtest(_FakeCtx(api_key="not-a-real-key"), ticker=TEST_TICKER, signal="donchian")


def test_run_backtest_donchian_end_to_end(seeded_ticker):
    result = run_backtest(_FakeCtx(), ticker=TEST_TICKER, signal="donchian",
                           hold_days=7, donchian_n=20)
    assert result["ticker"] == TEST_TICKER
    assert result["signal"] == "donchian"
    assert result["calls_used"] == 1
    assert "summary" in result, result.get("note")
    for block in ("gross", "net"):
        stats = result["summary"][block]
        assert stats["trades"] > 0
        assert 0.0 <= stats["win_rate"] <= 1.0
    assert "buy_hold_return_pct" in result["summary"]["net"]
    assert "alpha_pct" in result["summary"]["net"]
    assert len(result["recent_trades"]) <= 10


def test_run_backtest_sentiment_shift_end_to_end(seeded_ticker):
    result = run_backtest(_FakeCtx(), ticker=TEST_TICKER, signal="shift", hold_days=7)
    assert result["signal"] == "shift"
    assert result["calls_used"] == 1
    assert "summary" in result or "note" in result


def test_run_backtest_no_data_reports_a_note(seeded_ticker):
    result = run_backtest(_FakeCtx(), ticker="NOSUCHTICKERXYZ", signal="donchian")
    assert result["calls_used"] == 1
    assert "note" in result
    assert "summary" not in result
