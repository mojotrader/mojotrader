"""Scenario tests for the ported strategies.

Each scenario is a hand-built day whose correct trade is known by inspection,
so a port that drifts from its Pine source fails here rather than silently
producing different numbers.
"""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Side
from backtest.strategy import run
from backtest.strategies import ORBFade, PDHPDLBreakout, VWAPReversion
from test_engine import mkbars


def flat_tail(n, price=95.0):
    """Quiet bars so a day can run to its close without new signals."""
    return [(price, price + 0.5, price - 0.5, price)] * n


class TestORBFade(unittest.TestCase):
    """Opening range is 09:30-09:45: three 5m bars, high 100 / low 90."""

    ORB = [(95, 100, 90, 97), (97, 99, 93, 95), (95, 98, 92, 96)]

    def test_wick_poke_above_range_is_faded_short(self):
        bars = mkbars(self.ORB + [
            (96, 103, 95, 99),     # 3: pokes to 103, closes back inside -> fade short
            (99, 99.5, 98, 99),    # 4: entry at this open
            (99, 99, 88, 89),      # 5: runs to the far side -> target 90
        ] + flat_tail(10))
        s = ORBFade(rr=1.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, bars)

        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertIs(t.side, Side.SHORT)
        self.assertEqual(t.entry_price, 99.0, "fills at bar 4's open")
        self.assertEqual(t.exit_reason, "target")
        self.assertEqual(t.exit_price, 90.0, "target is the far side of the range")
        self.assertAlmostEqual(t.points, 9.0)

    def test_wick_poke_below_range_is_faded_long(self):
        bars = mkbars(self.ORB + [
            (94, 95, 87, 92),      # 3: pokes to 87, closes back inside -> fade long
            (92, 93, 91, 92),      # 4: entry
            (92, 101, 92, 100),    # 5: reaches target 100
        ] + flat_tail(10))
        s = ORBFade(rr=1.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, bars)

        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].side, Side.LONG)
        self.assertEqual(trades[0].exit_price, 100.0)

    def test_close_outside_then_back_inside_triggers_the_fade(self):
        """Close-break mode waits: the poke bar arms, a later re-entry fires."""
        bars = mkbars(self.ORB + [
            (99, 104, 98, 103),    # 3: CLOSES above the range -> arm, no entry yet
            (103, 104, 96, 97),    # 4: closes back inside -> signal
            (97, 98, 96, 97),      # 5: entry here
            (97, 97, 88, 89),      # 6: target
        ] + flat_tail(10))
        s = ORBFade(break_mode="Close", rr=1.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, bars)

        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].side, Side.SHORT)
        self.assertEqual(trades[0].entry_price, 97.0, "entry is the bar after the re-entry close")

    def test_running_through_the_far_side_voids_the_wait(self):
        """A genuine breakout that reverses all the way through is not a fade."""
        bars = mkbars(self.ORB + [
            (99, 104, 98, 103),    # 3: closes above -> arm short
            (103, 104, 85, 86),    # 4: blows through the low -> void, no signal
        ] + flat_tail(10, price=86.0))
        s = ORBFade(break_mode="Close", rr=1.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, bars)
        self.assertEqual(len(trades), 0, "voided wait must not produce a trade")

    def test_max_trades_per_day_is_respected(self):
        bars = mkbars(self.ORB + [
            (96, 103, 95, 99), (99, 99, 98, 99), (99, 99, 88, 89),   # trade 1
            (89, 95, 88, 94), (94, 103, 93, 99), (99, 99, 98, 99),   # would signal again
            (99, 99, 88, 89),
        ] + flat_tail(10))
        s = ORBFade(rr=1.0, max_trades=1)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, bars)
        self.assertEqual(len(trades), 1)


class TestPDHPDL(unittest.TestCase):
    def test_breaks_prior_day_high_and_stops_out(self):
        day1 = mkbars(
            [(95, 100, 90, 95)] + flat_tail(5), day="2024-06-03")
        day2 = mkbars([
            (95, 96, 94, 95),      # first bar of day 2: arms stops at 100 / 90
            (96, 101, 95, 100),    # breaks 100 -> long fills at the level
            (99, 100, 94, 95),     # stop at 95 hit
        ] + flat_tail(6), day="2024-06-04")

        s = PDHPDLBreakout(stop_pts=5.0, rr=2.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, day1 + day2)

        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertIs(t.side, Side.LONG)
        self.assertEqual(t.entry_price, 100.0, "stop order fills at the level, not the open")
        self.assertEqual(t.exit_reason, "stop")
        self.assertAlmostEqual(t.points, -5.0)

    def test_no_trade_on_the_first_day_since_there_is_no_prior_range(self):
        day1 = mkbars([(95, 110, 80, 95)] + flat_tail(8), day="2024-06-03")
        s = PDHPDLBreakout(stop_pts=5.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, day1)
        self.assertEqual(len(trades), 0)

    def test_short_side_breaks_prior_day_low(self):
        day1 = mkbars([(95, 100, 90, 95)] + flat_tail(5), day="2024-06-03")
        day2 = mkbars([
            (95, 96, 94, 95),
            (94, 95, 89, 90),      # breaks 90 -> short
            (91, 96, 90, 95),      # stop 95
        ] + flat_tail(6), day="2024-06-04")
        s = PDHPDLBreakout(stop_pts=5.0, rr=2.0)
        s.slippage_ticks, s.commission_per_contract = 0, 0
        trades, _ = run(s, day1 + day2)
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].side, Side.SHORT)
        self.assertEqual(trades[0].entry_price, 90.0)


class TestVWAPReversion(unittest.TestCase):
    def test_runs_and_produces_structurally_valid_trades(self):
        """Drive a stretch away from VWAP and back to trigger a reversion."""
        specs = [(100, 100.5, 99.5, 100, 500)] * 20
        specs += [(100 + i, 101 + i, 99 + i, 100.5 + i, 800) for i in range(1, 8)]
        specs += [(107, 107.5, 103, 104, 900)]     # closes back inside the upper band
        specs += [(104, 104.5, 100, 101, 700)] * 6
        bars = mkbars(specs)

        s = VWAPReversion(band_mult=1.5, atr_len=5, atr_mult=1.5)
        trades, _ = run(s, bars)

        for t in trades:
            self.assertGreater(t.exit_ts, t.entry_ts, "exit must follow entry")
            self.assertGreater(t.entry_price, 0)
            self.assertIn(t.exit_reason, ("stop", "target", "EOD flat", "end of data"))

    def test_no_entries_before_atr_has_seeded(self):
        """An unseeded ATR would size the stop off nothing; entries must wait."""
        bars = mkbars([(100, 105, 95, 96, 500)] * 4)
        s = VWAPReversion(atr_len=14)
        trades, broker = run(s, bars)
        self.assertEqual(len(trades), 0)


class TestNoLookahead(unittest.TestCase):
    def test_trades_are_unchanged_when_future_bars_are_removed(self):
        """Truncating after a trade's exit must not alter that trade.

        If any rule peeked forward, cutting the tail would change the earlier
        decisions and this comparison would diverge.
        """
        specs = TestORBFade.ORB + [
            (96, 103, 95, 99), (99, 99.5, 98, 99), (99, 99, 88, 89),
        ] + flat_tail(20)
        full = mkbars(specs)

        s1 = ORBFade(rr=1.0)
        s1.slippage_ticks, s1.commission_per_contract = 0, 0
        full_trades, _ = run(s1, full)
        self.assertGreater(len(full_trades), 0)

        cut_at = 8
        s2 = ORBFade(rr=1.0)
        s2.slippage_ticks, s2.commission_per_contract = 0, 0
        short_trades, _ = run(s2, full[:cut_at])

        self.assertEqual(full_trades[0].entry_ts, short_trades[0].entry_ts)
        self.assertEqual(full_trades[0].entry_price, short_trades[0].entry_price)
        self.assertEqual(full_trades[0].exit_price, short_trades[0].exit_price)


if __name__ == "__main__":
    unittest.main(verbosity=2)
