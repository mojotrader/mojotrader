import pandas as pd
import pytest

from backtester.engine import BacktestConfig, run_backtest

TZ = "America/New_York"


def frame(rows, start="2026-03-03 10:00"):
    idx = pd.date_range(pd.Timestamp(start).tz_localize(TZ), periods=len(rows), freq="1min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


class Script:
    """Tiny strategy: runs ``actions[i](ctx)`` at the close of bar i."""

    def __init__(self, actions):
        self.actions = actions
        self.params = {}

    def on_bar(self, ctx, bar):
        fn = self.actions.get(ctx.i)
        if fn:
            fn(ctx)


def cfg(**kw):
    base = dict(point_value=2.0, tick_size=0.25, commission=0.0, slippage_ticks=0, limit_through_ticks=0)
    base.update(kw)
    return BacktestConfig(**base)


def test_limit_fills_on_touch_then_target():
    bars = frame([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (99.5, 102, 99.5, 101.5)])
    s = Script({0: lambda c: c.place_entry("L", 1, 1, 99, stop=97, target=101)})
    t = run_backtest(bars, s, cfg()).trades
    assert len(t) == 1
    assert t.iloc[0].entry_price == 99 and t.iloc[0].exit_price == 101 and t.iloc[0].exit_reason == "Target"
    assert t.iloc[0].net_pnl == pytest.approx(2 * 2.0)


def test_trade_through_rule_blocks_touch_fill():
    bars = frame([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (99.5, 102, 99.5, 101.5)])
    s = Script({0: lambda c: c.place_entry("L", 1, 1, 99, stop=97, target=101)})
    assert run_backtest(bars, s, cfg(limit_through_ticks=1)).trades.empty


def test_gap_below_buy_limit_fills_at_open():
    bars = frame([(100, 100, 100, 100), (97, 98, 96.5, 97.5)])
    s = Script({0: lambda c: c.place_entry("L", 1, 1, 99)})
    t = run_backtest(bars, s, cfg()).trades
    assert t.iloc[0].entry_price == 97


def test_entry_and_stop_in_same_bar_follow_path():
    # open near the high -> path open, high, low, close: entry at 99 fills, then stop at 98 is hit
    bars = frame([(100, 100, 100, 100), (100, 100.25, 97.5, 98)])
    s = Script({0: lambda c: c.place_entry("L", 1, 1, 99, stop=98, target=103)})
    t = run_backtest(bars, s, cfg(slippage_ticks=1)).trades
    assert t.iloc[0].exit_reason == "Stop"
    assert t.iloc[0].exit_price == 97.75          # 1 tick slippage on the stop


def test_two_lots_with_same_target_both_exit_on_same_bar():
    bars = frame([(100, 100, 100, 100), (100, 100, 97, 97.5), (97.5, 103, 97.5, 102.5)])
    s = Script({0: lambda c: (c.place_entry("A", 1, 1, 99, 95, 102), c.place_entry("B", 1, 1, 98, 95, 102))})
    t = run_backtest(bars, s, cfg()).trades
    assert len(t) == 2 and set(t.exit_time) == {bars.index[2]}


def test_pessimistic_prefers_stop():
    bars = frame([(100, 100, 100, 100), (100, 100, 99, 99), (101, 103, 96, 100)])  # open nearer the high
    s = Script({0: lambda c: c.place_entry("L", 1, 1, 99, stop=97, target=102)})
    assert run_backtest(bars, s, cfg()).trades.iloc[0].exit_reason == "Target"
    assert run_backtest(bars, s, cfg(pessimistic=True)).trades.iloc[0].exit_reason.startswith("Stop")


def test_market_entry_and_close_on_bar_close():
    bars = frame([(100, 100, 100, 100), (100, 101, 99, 100.5), (100.5, 101, 100, 100.75)])
    s = Script({0: lambda c: c.market_entry("M", -1, 2, stop=105, target=90), 1: lambda c: c.close("M", "exit")})
    t = run_backtest(bars, s, cfg(commission=0.5)).trades
    r = t.iloc[0]
    assert r.entry_price == 100 and r.exit_price == 100.5 and r.qty == 2
    assert r.net_pnl == pytest.approx(-0.5 * 2 * 2.0 - 0.5 * 2 * 2)


def test_short_limit_and_modify_stop():
    bars = frame([(100, 100, 100, 100), (100, 101.5, 100, 101), (101, 101.25, 100.5, 100.5), (100.5, 101.1, 100.5, 101)])
    s = Script({0: lambda c: c.place_entry("S", -1, 1, 101, stop=103, target=95),
                2: lambda c: c.set_exits("S", stop=101)})
    t = run_backtest(bars, s, cfg()).trades
    assert t.iloc[0].entry_price == 101 and t.iloc[0].exit_reason == "Stop" and t.iloc[0].exit_price == 101
