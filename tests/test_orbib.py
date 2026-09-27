import pandas as pd
import pytest

from backtester.data import make_demo_data
from backtester.engine import BacktestConfig, run_backtest
from backtester.strategies.orbib import ORBIB, f_size

TZ = "America/New_York"


def day_bars(day: str, start: str, end: str, overrides: dict, base: float = 100.0) -> pd.DataFrame:
    """Flat 1-minute bars from start to end (ET); ``overrides`` maps 'HH:MM' -> (o, h, l, c).
    Flat bars sit at the previous bar's close."""
    idx = pd.date_range(pd.Timestamp(f"{day} {start}").tz_localize(TZ), pd.Timestamp(f"{day} {end}").tz_localize(TZ),
                        freq="1min")
    rows, last = [], base
    for t in idx:
        k = t.strftime("%H:%M")
        if k in overrides:
            o, h, l, c = overrides[k]
        else:
            o = h = l = c = last
        rows.append((o, h, l, c))
        last = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


CFG = BacktestConfig(point_value=2.0, tick_size=0.25, commission=0.5, slippage_ticks=1, limit_through_ticks=0)


def test_f_size_rounding():
    assert f_size(150, 10, 2.0) == 7         # 7.5 -> down
    assert f_size(150, 9, 2.0) == 8          # 8.33 -> down
    assert f_size(150, 8.5, 2.0) == 9        # 8.82 -> up
    assert f_size(10, 50, 2.0) == 0          # budget too small -> skip


def test_orb_shallow_long_fills_pullback_and_hits_target():
    # Tuesday (EST). Range 09:30-09:45: low 95 first (09:31), high 110 later (09:43), last close 109 -> shallow long.
    ov = {"09:30": (100, 100, 99, 99), "09:31": (99, 99, 95, 96), "09:43": (105, 110, 105, 109),
          "09:44": (109, 109.5, 108.5, 109),
          "09:45": (109, 110.75, 109, 110.5),       # close above 110 -> break (Close beyond)
          "09:46": (110.5, 110.5, 106, 107),        # touches e1 = 106.25
          "09:47": (107, 112, 107, 111.75)}         # target = 110 + 10% of 15 = 111.5
    for m in range(32, 43):
        ov[f"09:{m}"] = (100 + (m - 32) * 0.5,) * 4
    bars = day_bars("2026-03-03", "09:30", "16:00", ov)
    p = dict(run_hal=False, run_ib=False, orb_min_rng_pct=0.0)
    t = run_backtest(bars, ORBIB(p), CFG).trades
    assert len(t) == 1
    r = t.iloc[0]
    assert (r.tag, r.side, r.entry_price, r.exit_price, r.exit_reason) == ("ORB-1", 1, 106.25, 111.5, "Target")
    assert r.net_pnl == pytest.approx(5.25 * 2 - 1.0)


def test_orb_min_range_filter_skips_small_range():
    ov = {"09:31": (99, 99, 95, 96), "09:43": (105, 110, 105, 109), "09:44": (109, 109.5, 108.5, 109),
          "09:45": (109, 110.75, 109, 110.5), "09:46": (110.5, 110.5, 106, 107)}
    bars = day_bars("2026-03-03", "09:30", "16:00", ov, base=100)
    # range 15 on price ~95 = 15.8% -> a 20% minimum skips it
    t = run_backtest(bars, ORBIB(dict(run_hal=False, run_ib=False, orb_min_rng_pct=20.0)), CFG).trades
    assert t.empty


def test_halyard_long_break_hits_target():
    # EST: the range candle is 00:00-00:15 ET (05:00 UTC). 15m close of the next candle above the range -> long.
    ov = {"00:00": (100, 101, 99, 100), "00:29": (100.5, 101.5, 100.5, 101.5), "00:40": (101.5, 104, 101.5, 103.5)}
    bars = day_bars("2026-03-03", "00:00", "02:00", ov)
    t = run_backtest(bars, ORBIB(dict(run_orb=False, run_ib=False)), CFG).trades
    assert len(t) == 1
    r = t.iloc[0]
    assert r.tag == "HAL-L" and r.qty == 4
    assert r.entry_price == 101.75                       # 15m close 101.5 + 1 tick slippage
    assert r.exit_reason == "Target" and r.exit_price == 103.75   # 101.5 + (101.5 - 99) * 0.9


def test_halyard_monday_is_long_only():
    ov = {"00:00": (100, 101, 99, 100), "00:29": (99.5, 99.5, 98.5, 98.5)}   # short break on a Monday
    bars = day_bars("2026-03-02", "00:00", "02:00", ov)
    assert run_backtest(bars, ORBIB(dict(run_orb=False, run_ib=False)), CFG).trades.empty


def test_halyard_reverse_after_stop():
    ov = {"00:00": (100, 101, 99, 100),
          "00:29": (100.5, 101.5, 100.5, 101.5),        # long first trade at 101.5, stop 99
          "00:35": (101, 101, 98.5, 98.75),             # stop hit
          "00:44": (98.75, 98.75, 98.5, 98.5),          # 15m close below 99 -> reverse short
          "01:00": (98.5, 98.5, 95.5, 96)}              # short target 98.5 - 2.5 = 96
    bars = day_bars("2026-03-03", "00:00", "02:00", ov)
    t = run_backtest(bars, ORBIB(dict(run_orb=False, run_ib=False)), CFG).trades
    assert list(t.tag) == ["HAL-L", "HAL-RS"]
    assert list(t.exit_reason) == ["Stop", "Target"]


def test_full_run_on_demo_data_is_consistent():
    bars = make_demo_data(days=30, end="2026-06-30")
    r = run_backtest(bars, ORBIB(), CFG)
    t = r.trades
    assert not t.empty
    assert set(t.group) <= {"HAL", "ORB", "IB"}
    # nothing is ever held across the 15:30 flatten, and the equity curve adds up to the trades
    assert (t[t.group != "HAL"].exit_time.dt.hour * 60 + t[t.group != "HAL"].exit_time.dt.minute <= 15 * 60 + 31).all()
    assert r.equity.iloc[-1] - CFG.initial_capital == pytest.approx(t.net_pnl.sum())
