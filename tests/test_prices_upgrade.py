"""Price table: current Claude prices, safe auto-upgrade of untouched old copies, repricing of stored costs."""
import hashlib

from dinoguard.tokens import prices


OLD_V1 = """# USD per 1M tokens + context window, matched by substring of the model id (first match wins).
# These are ESTIMATES - edit ~/.dinoguard/prices.yaml (copied from this file on first use)
# to match your provider's current price sheet.
models:
  - match: opus
    input: 15.0
    output: 75.0
    cache_read: 1.5
    cache_write: 18.75
    context_window: 200000
"""


def test_current_claude_rows():
    p = prices.for_model("claude-opus-5-5")
    assert (p["input"], p["output"], p["cache_read"], p["context_window"]) == (4.0, 20.0, 0.2, 1_000_000)
    assert prices.for_model("claude-opus-5")["input"] == 5.0               # not shadowed by opus-5-5
    assert prices.for_model("claude-sonnet-5-5")["cache_read"] == 0.1
    assert prices.for_model("claude-haiku-4-5")["context_window"] == 200_000
    assert prices.for_model("claude-fable-5-2")["input"] == 8.0
    assert prices.for_model("claude-opus-4-1")["input"] == 15.0            # older Opus keeps old prices
    # 1M cache reads on Opus 5.5 = $0.20, not the old $1.50
    assert abs(prices.cost("claude-opus-5-5", 0, 0, cache_read=1_000_000) - 0.2) < 1e-9


def test_untouched_old_copy_is_upgraded(dg_home, monkeypatch):
    user = dg_home / "prices.yaml"
    user.write_bytes(OLD_V1.encode())
    monkeypatch.setattr(prices, "OLD_DEFAULTS", {hashlib.sha256(OLD_V1.encode()).hexdigest()})
    prices.user_prices_path()
    assert user.read_bytes() == prices.BUNDLED.read_bytes()


def test_edited_copy_is_never_replaced(dg_home, monkeypatch):
    user = dg_home / "prices.yaml"
    edited = OLD_V1.replace("15.0", "1.0")
    user.write_text(edited, encoding="utf-8")
    monkeypatch.setattr(prices, "OLD_DEFAULTS", {hashlib.sha256(OLD_V1.encode()).hexdigest()})
    prices.user_prices_path()
    assert user.read_text(encoding="utf-8") == edited


def test_stored_costs_follow_the_price_table(conn, dg_home):
    conn.execute("INSERT INTO model_calls(id, session_id, ts, model, tokens_in, tokens_out, cache_read, cache_write,"
                 " cost_usd) VALUES ('m1','s','2026-11-25T10:00:00Z','claude-opus-5-5',0,0,1000000,0,1.5)")
    conn.commit()
    assert prices.ensure_repriced(conn) == 1
    assert abs(conn.execute("SELECT cost_usd FROM model_calls").fetchone()[0] - 0.2) < 1e-9
    assert prices.ensure_repriced(conn) == 0                               # nothing changed since
    user = prices.user_prices_path()
    user.write_text(user.read_text(encoding="utf-8").replace("cache_read: 0.2\n", "cache_read: 0.4\n", 1)
                    .replace("cache_read: 0.4\n", "cache_read: 0.2\n", 1)
                    .replace("  - match: opus-5-5\n    input: 4.0\n    output: 20.0\n    cache_read: 0.2",
                             "  - match: opus-5-5\n    input: 4.0\n    output: 20.0\n    cache_read: 0.4"),
                    encoding="utf-8")
    assert prices.ensure_repriced(conn) == 1                               # user edit -> recalculated
    assert abs(conn.execute("SELECT cost_usd FROM model_calls").fetchone()[0] - 0.4) < 1e-9


def test_context_window_never_reads_over_100_percent(conn):
    from dinoguard.tokens.transcript import stats
    conn.execute("INSERT INTO model_calls(id, session_id, ts, model, tokens_in, tokens_out, cache_read, cache_write,"
                 " cost_usd, context_tokens) VALUES ('m1','s','2026-11-25T10:00:00Z','some-new-model',0,0,0,0,0,"
                 "450000)")
    conn.commit()
    st = stats(conn, "s")
    assert st.context_tokens == 450_000 and st.context_window == 1_000_000 and st.context_pct < 1


def test_token_tips_on_by_default_budget_off():
    from dinoguard.engine import registry
    reg = registry.build()
    by = {p.name: p for p in reg.policies}
    for name in ("token-repeat-read", "token-big-output", "token-context-high", "token-model-fit"):
        assert by[name].default_enabled, name
    assert not by["token-budget"].default_enabled
