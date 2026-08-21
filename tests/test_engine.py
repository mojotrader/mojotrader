"""Fill-model and metric tests.

The engine's value is entirely in its timing and cost rules, and those rules
are invisible in aggregate results -- a lookahead bug produces a beautiful
equity curve, not an exception. So each rule is pinned here against bars
constructed so the correct answer is known by hand.
"""

from __future__ import annotations

import sys
import os
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.bars import Bar, NY, parse_hhmm, in_session
from backtest.engine import Broker, Side, Trade
from backtest.indicators import ATR, SessionVWAP
from backtest.metrics import compute
from backtest.strategy import Strategy, run


def mkbars(specs, day="2024-06-03", start_min=570, step=5):
    """Build 5m bars from (o,h,l,c[,v]) tuples, starting 09:30 New York."""
    base = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=NY)
    base += timedelta(minutes=start_min)
    out = []
    for i, s in enumerate(specs):
        o, h, l, c = s[:4]
        v = s[4] if len(s) > 4 else 100.0
        out.append(Bar(
            ts=(base + timedelta(minutes=i * step)).astimezone(timezone.utc),
            open=o, high=h, low=l, close=c, volume=v,
        ))
    return out


class SignalOnce(Strategy):
    """Fires one market entry on a chosen bar, then attaches a fixed bracket."""

    name = "signal_once"

    def __init__(self, at_index, side, stop=None, target=None):
        self.at_index = at_index
        self.side = side
        self.stop = stop
        self.target = target
        self.i = -1
        self.fill_price = None

    def on_bar(self, bar, broker, in_sess):
        self.i += 1
        if broker.position is not None and broker.position.stop is None:
            self.fill_price = broker.position.entry_price
            broker.set_bracket(self.stop, self.target)
        if self.i == self.at_index and broker.flat:
            broker.market_entry(self.side, 1.0)


class TestFillTiming(unittest.TestCase):
    def test_market_entry_fills_next_bar_open_not_signal_bar(self):
        """A signal on bar N must not fill inside bar N."""
        bars = mkbars([
            (100, 101, 99, 100),   # 0: signal here
            (105, 106, 104, 105),  # 1: fill at this open
            (105, 106, 104, 105),
        ])
        s = SignalOnce(0, Side.LONG)
        s.slippage_ticks = 0
        trades, broker = run(s, bars)
        self.assertEqual(s.fill_price, 105.0, "should fill at bar 1 open, not bar 0")

    def test_slippage_hurts_both_directions(self):
        bars = mkbars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])

        long_s = SignalOnce(0, Side.LONG)
        long_s.slippage_ticks, long_s.tick_size = 1, 0.25
        run(long_s, bars)
        self.assertEqual(long_s.fill_price, 100.25, "a buy should pay up one tick")

        short_s = SignalOnce(0, Side.SHORT)
        short_s.slippage_ticks, short_s.tick_size = 1, 0.25
        run(short_s, bars)
        self.assertEqual(short_s.fill_price, 99.75, "a sell should receive one tick less")

    def test_bracket_is_not_live_on_the_entry_bar(self):
        """Entry bar 1 spans the stop, but the bracket only arms from bar 2."""
        bars = mkbars([
            (100, 101, 99, 100),
            (100, 110, 90, 100),   # entry bar: range covers stop 95 and target 105
            (100, 101, 99, 100),   # nothing hit here
            (100, 101, 94, 100),   # stop finally hit
        ])
        s = SignalOnce(0, Side.LONG, stop=95, target=105)
        s.slippage_ticks = 0
        trades, _ = run(s, bars)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "stop")
        self.assertEqual(trades[0].exit_ts, bars[3].ts, "must exit on bar 3, not the entry bar")


class TestIntrabarAmbiguity(unittest.TestCase):
    def test_stop_wins_when_one_bar_covers_stop_and_target(self):
        """The pessimistic resolution: unknowable path resolves to the loss."""
        bars = mkbars([
            (100, 101, 99, 100),
            (100, 101, 99, 100),   # entry at 100
            (100, 110, 90, 100),   # covers both stop 95 and target 105
            (100, 101, 99, 100),
        ])
        s = SignalOnce(0, Side.LONG, stop=95, target=105)
        s.slippage_ticks = 0
        trades, _ = run(s, bars)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "stop")
        self.assertEqual(trades[0].exit_price, 95.0)

    def test_same_rule_applies_to_shorts(self):
        bars = mkbars([
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 110, 90, 100),   # short stop 105 and target 95 both covered
            (100, 101, 99, 100),
        ])
        s = SignalOnce(0, Side.SHORT, stop=105, target=95)
        s.slippage_ticks = 0
        trades, _ = run(s, bars)
        self.assertEqual(trades[0].exit_reason, "stop")


class TestGaps(unittest.TestCase):
    def test_stop_entry_gapped_through_fills_at_open_not_the_level(self):
        broker = Broker(slippage_ticks=0, commission_per_contract=0)
        bars = mkbars([(100, 101, 99, 100), (103, 104, 102, 103)])
        broker.process_bar_open(bars[0], 0)
        broker.stop_entry(Side.LONG, 100.5, 1.0, "L")
        broker.process_bar_open(bars[1], 1)
        self.assertIsNotNone(broker.position)
        self.assertEqual(broker.position.entry_price, 103.0,
                         "gap above the stop must fill at the open, which is worse")

    def test_limit_target_gapped_through_fills_at_the_better_open(self):
        bars = mkbars([
            (100, 101, 99, 100),
            (100, 101, 99, 100),   # entry 100
            (100, 101, 99, 100),
            (108, 109, 107, 108),  # gaps past target 105
        ])
        s = SignalOnce(0, Side.LONG, stop=90, target=105)
        s.slippage_ticks = 0
        trades, _ = run(s, bars)
        self.assertEqual(trades[0].exit_reason, "target")
        self.assertEqual(trades[0].exit_price, 108.0, "a gap through a limit fills better")


class TestCosts(unittest.TestCase):
    def test_commission_charged_on_both_sides(self):
        bars = mkbars([
            (100, 101, 99, 100), (100, 101, 99, 100),
            (100, 101, 99, 100), (100, 101, 94, 100),
        ])
        s = SignalOnce(0, Side.LONG, stop=95, target=200)
        s.slippage_ticks = 0
        s.commission_per_contract = 0.5
        trades, _ = run(s, bars)
        self.assertEqual(trades[0].commission, 1.0, "0.5 per contract per side = 1.00 round turn")


class TestIndicators(unittest.TestCase):
    def test_atr_uses_wilder_smoothing(self):
        atr = ATR(3)
        bars = mkbars([(9, 10, 8, 9), (10, 11, 9, 10), (11, 12, 10, 11), (12, 15, 11, 14)])
        vals = [atr.update(b) for b in bars]
        self.assertIsNone(vals[0])
        self.assertIsNone(vals[1])
        self.assertAlmostEqual(vals[2], 2.0, places=9, msg="seed is the mean of the first 3 TRs")
        self.assertAlmostEqual(vals[3], 8.0 / 3.0, places=9, msg="RMA: (2*2 + 4)/3")

    def test_session_vwap_is_volume_weighted_and_resets(self):
        v = SessionVWAP()
        v.update(Bar(datetime.now(timezone.utc), 10, 10, 10, 10, 1))
        v.update(Bar(datetime.now(timezone.utc), 20, 20, 20, 20, 3))
        self.assertAlmostEqual(v.vwap, (10 * 1 + 20 * 3) / 4)
        self.assertGreater(v.dev, 0)
        v.reset()
        self.assertIsNone(v.vwap)


class TestMetrics(unittest.TestCase):
    def _trades(self, points):
        t0 = datetime(2024, 6, 3, 14, 0, tzinfo=timezone.utc)
        out = []
        for i, p in enumerate(points):
            out.append(Trade(
                side=Side.LONG, qty=1.0,
                entry_ts=t0 + timedelta(days=i), entry_price=100.0,
                exit_ts=t0 + timedelta(days=i, hours=1), exit_price=100.0 + p,
                exit_reason="target" if p > 0 else "stop",
                commission=0.0, bars_held=5,
            ))
        return out

    def test_headline_numbers(self):
        s = compute(self._trades([10, -5, 8, -3, -4]), point_value=1.0)
        self.assertEqual(s.trades, 5)
        self.assertEqual(s.wins, 2)
        self.assertAlmostEqual(s.net_pnl, 6.0)
        self.assertAlmostEqual(s.gross_profit, 18.0)
        self.assertAlmostEqual(s.gross_loss, 12.0)
        self.assertAlmostEqual(s.profit_factor, 1.5)
        self.assertAlmostEqual(s.expectancy, 1.2)
        self.assertEqual(s.max_consec_losses, 2)

    def test_max_drawdown_is_peak_to_trough_not_worst_trade(self):
        # equity 10, 5, 13, 10, 6 -> deepest fall is 13 -> 6
        s = compute(self._trades([10, -5, 8, -3, -4]), point_value=1.0)
        self.assertAlmostEqual(s.max_drawdown, 7.0)

    def test_no_losses_gives_infinite_profit_factor_not_a_crash(self):
        s = compute(self._trades([5, 5]), point_value=1.0)
        self.assertEqual(s.profit_factor, float("inf"))

    def test_empty_is_safe(self):
        s = compute([], point_value=2.0)
        self.assertEqual(s.trades, 0)
        self.assertEqual(s.net_pnl, 0.0)


class TestSessions(unittest.TestCase):
    def test_rth_window_and_weekend_exclusion(self):
        [bar] = mkbars([(1, 1, 1, 1)], day="2024-06-03", start_min=570)
        self.assertTrue(in_session(bar, 570, 960))
        self.assertFalse(in_session(bar, 585, 960), "09:30 is before a 09:45 start")
        [sat] = mkbars([(1, 1, 1, 1)], day="2024-06-08", start_min=570)
        self.assertFalse(in_session(sat, 570, 960), "Saturday is not a session")

    def test_dst_boundary_maps_to_the_same_local_open(self):
        [summer] = mkbars([(1, 1, 1, 1)], day="2024-06-03", start_min=570)
        [winter] = mkbars([(1, 1, 1, 1)], day="2024-01-03", start_min=570)
        self.assertEqual(summer.ny_minute(), 570)
        self.assertEqual(winter.ny_minute(), 570)
        self.assertNotEqual(summer.ts.hour, winter.ts.hour, "UTC hour differs across DST")

    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("0930"), 570)
        self.assertEqual(parse_hhmm("15:55"), 955)


if __name__ == "__main__":
    unittest.main(verbosity=2)
