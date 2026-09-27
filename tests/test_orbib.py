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


# Halyard alone, original behaviour: market entry at the 15m close, stop on the range edge
HAL_MARKET = dict(run_orb=False, run_ib=False, hal_entry_mode="Market at Close", hal_stop_pct=100.0)
HAL_ONLY = dict(run_orb=False, run_ib=False)          # the pullback defaults (10% pullback, 96% stop, 90% target)

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
    t = run_backtest(bars, ORBIB(dict(HAL_MARKET)), CFG).trades
    assert len(t) == 1
    r = t.iloc[0]
    assert r.tag == "HAL-L" and r.qty == 4
    assert r.entry_price == 101.75                       # 15m close 101.5 + 1 tick slippage
    assert r.exit_reason == "Target" and r.exit_price == 103.75   # 101.5 + (101.5 - 99) * 0.9


def test_halyard_monday_is_long_only():
    ov = {"00:00": (100, 101, 99, 100), "00:29": (99.5, 99.5, 98.5, 98.5)}   # short break on a Monday
    bars = day_bars("2026-03-02", "00:00", "02:00", ov)
    assert run_backtest(bars, ORBIB(dict(HAL_MARKET)), CFG).trades.empty


def test_halyard_reverse_after_stop():
    ov = {"00:00": (100, 101, 99, 100),
          "00:29": (100.5, 101.5, 100.5, 101.5),        # long first trade at 101.5, stop 99
          "00:35": (101, 101, 98.5, 98.75),             # stop hit
          "00:44": (98.75, 98.75, 98.5, 98.5),          # 15m close below 99 -> reverse short
          "01:00": (98.5, 98.5, 95.5, 96)}              # short target 98.5 - 2.5 = 96
    bars = day_bars("2026-03-03", "00:00", "02:00", ov)
    t = run_backtest(bars, ORBIB(dict(HAL_MARKET)), CFG).trades
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


# Pullback defaults on a 99-101 range and a 101.5 signal close: ruler = 2.5 points
#   entry 101.5 - 10% = 101.25 | stop 101.5 - 96% = 99.10 -> 99.00 (tick) | target 101.5 + 90% = 103.75
HAL_OV = {"00:00": (100, 101, 99, 100), "00:29": (100.5, 101.5, 100.5, 101.5)}


def test_halyard_pullback_limit_fills_and_hits_target():
    ov = dict(HAL_OV, **{"00:33": (101.5, 101.5, 101.25, 101.5), "00:40": (101.5, 104, 101.5, 103.5)})
    t = run_backtest(day_bars("2026-03-03", "00:00", "02:00", ov), ORBIB(HAL_ONLY), CFG).trades
    assert len(t) == 1
    r = t.iloc[0]
    assert (r.tag, r.entry_price, r.exit_price, r.exit_reason) == ("HAL-L", 101.25, 103.75, "Target")
    assert r.entry_time.strftime("%H:%M") == "00:33"


def test_halyard_limit_cancelled_when_target_trades_first_then_reverse():
    ov = dict(HAL_OV, **{"00:35": (101.5, 104, 101.5, 103.75),          # target before any pullback -> cancel
                         "00:59": (99, 99, 98.5, 98.5),                 # 15m close below 99 -> reverse short limit
                         "01:02": (98.5, 98.75, 98.5, 98.5)})           # short entry 98.5 + 10% of 2 = 98.75
    t = run_backtest(day_bars("2026-03-03", "00:00", "02:00", ov), ORBIB(HAL_ONLY), CFG).trades
    assert list(t.tag) == ["HAL-RS"]
    assert t.iloc[0].entry_price == 98.75


def test_halyard_limit_expires_after_n_15m_bars():
    # price drifts up slowly and never pulls back to 101.25 nor reaches 103.75
    ov = dict(HAL_OV)
    for k in range(30, 120):
        h, m = divmod(k, 60)
        px = 101.5 + (k - 30) * 0.02
        q = round(px * 4) / 4
        ov[f"{h:02d}:{m:02d}"] = (q, q, q, q)
    ov["01:40"] = (101.25, 101.5, 101.0, 101.25)       # pullback after the 4-bar (1 hour) expiry -> no fill
    t = run_backtest(day_bars("2026-03-03", "00:00", "02:00", ov), ORBIB(HAL_ONLY), CFG).trades
    assert t.empty


def test_halyard_entry_cutoff():
    # EST: range 00:00. Signal candle opening at 10:45 ET is after the 10:30 cutoff -> no trade
    ov = {"00:00": (100, 101, 99, 100), "10:59": (100.5, 101.5, 100.5, 101.5), "11:05": (101.5, 101.5, 101.0, 101.25)}
    bars = day_bars("2026-03-03", "00:00", "12:00", ov)
    assert run_backtest(bars, ORBIB(dict(HAL_ONLY, run_orb=False)), CFG).trades.empty
    t = run_backtest(bars, ORBIB(dict(HAL_ONLY, hal_use_cutoff=False)), CFG).trades
    assert list(t.tag) == ["HAL-L"]


def test_pullback_must_be_inside_stop():
    with pytest.raises(ValueError):
        ORBIB(dict(hal_pullback_pct=50.0, hal_stop_pct=40.0))


# Halyard long from the overnight session (market entry at 101.5, stop 99, target 103.75), then an ORB setup.
def _hal_then_orb(orb_rows):
    ov = {"00:00": (100, 101, 99, 100), "00:29": (100.5, 101.5, 100.5, 101.5)}
    ov.update(orb_rows)
    return day_bars("2026-03-03", "00:00", "16:00", ov)


P_PRIORITY = dict(HAL_MARKET, run_orb=True, run_ib=False, orb_min_rng_pct=0.0)


def test_opposite_orb_cancels_halyard():
    rows = {"09:31": (101.5, 103, 101.5, 102.5),          # ORB high first ...
            "09:43": (102.5, 102.5, 100, 100.25),         # ... low later, close near the low -> shallow SHORT
            "09:45": (100.25, 100.25, 99.5, 99.75),       # close below 100 -> ORB short arms -> Halyard closed
            "09:47": (99.75, 100.75, 99.75, 100.5),       # short limit 100.75 fills
            "09:50": (100.5, 100.5, 98.75, 99)}           # target 100 - 30% of 3 = 99.1 -> 99.0
    for m in range(32, 43):
        rows[f"09:{m}"] = (102.5,) * 4
    t = run_backtest(_hal_then_orb(rows), ORBIB(P_PRIORITY), CFG).trades
    hal = t[t.group == "HAL"].iloc[0]
    assert hal.exit_reason.startswith("Halyard cancelled") and hal.exit_time.strftime("%H:%M") == "09:45"
    orb = t[t.group == "ORB"].iloc[0]
    assert (orb.side, orb.entry_price, orb.exit_reason) == (-1, 100.75, "Target")


def test_same_direction_orb_fills_while_halyard_open():
    rows = {"09:31": (101.5, 101.5, 100, 100.5),          # ORB low first ...
            "09:43": (100.5, 103, 100.5, 102.75),         # ... high later, close near the high -> shallow LONG
            "09:45": (102.75, 103.5, 102.75, 103.25),     # close above 103 -> ORB long arms, Halyard stays
            "09:47": (103.25, 103.25, 102.25, 102.5),     # long limit 102.25 fills
            "09:50": (102.5, 103.5, 102.5, 103.25)}       # ORB target 103 + 10% of 3 = 103.3 -> 103.25
    for m in range(32, 43):
        rows[f"09:{m}"] = (100.5,) * 4
    t = run_backtest(_hal_then_orb(rows), ORBIB(P_PRIORITY), CFG).trades
    hal = t[t.group == "HAL"].iloc[0]
    orb = t[t.group == "ORB"].iloc[0]
    assert orb.side == 1 and orb.exit_reason == "Target"
    assert hal.entry_time < orb.entry_time < hal.exit_time          # both open at the same time
    assert not hal.exit_reason.startswith("Halyard cancelled")
