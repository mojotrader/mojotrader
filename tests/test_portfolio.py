"""Tests for the multi-entry broker and the higher-timeframe aggregator."""

from __future__ import annotations

import sys, os, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.bars import Bar, NY
from backtest.engine import Side
from backtest.portfolio import Portfolio
from backtest.tf import TFAggregator
from backtest.strategies.halyard_orbib import (
    et_offset_minutes, hal_range_start_min, hal_session_end_min,
    hal_session_key, hal_session_weekday, resolve_tp,
)
from test_engine import mkbars


class TestPortfolioFills(unittest.TestCase):
    def setUp(self):
        self.pf = Portfolio(point_value=20.0, tick_size=0.25,
                            commission_per_contract=0.0, slippage_ticks=0.0,
                            process_on_close=True)

    def test_limit_entry_not_eligible_on_the_bar_it_is_placed(self):
        bars = mkbars([(100, 101, 95, 100), (100, 101, 95, 100)])
        self.pf.process_bar(bars[0], 0)
        self.pf.limit_entry("L", Side.LONG, 96.0, 1.0)
        self.pf.process_close(bars[0], 0)
        self.assertFalse(self.pf.is_open("L"), "bar 0 dipped to 95 but the order was placed at its close")
        self.pf.process_bar(bars[1], 1)
        self.assertTrue(self.pf.is_open("L"), "fills on the next bar")

    def test_limit_gapped_through_fills_better_than_the_level(self):
        bars = mkbars([(100, 101, 99, 100), (90, 91, 89, 90)])
        self.pf.process_bar(bars[0], 0)
        self.pf.limit_entry("L", Side.LONG, 96.0, 1.0)
        self.pf.process_close(bars[0], 0)
        self.pf.process_bar(bars[1], 1)
        self.assertEqual(self.pf.positions["L"].entry_price, 90.0,
                         "a buy limit gapped through fills at the open")

    def test_two_units_share_one_bracket_and_close_independently(self):
        bars = mkbars([(100, 101, 99, 100)] * 6)
        self.pf.process_bar(bars[0], 0)
        self.pf.limit_entry("A", Side.LONG, 100.5, 1.0)
        self.pf.limit_entry("B", Side.LONG, 100.5, 2.0)
        self.pf.process_close(bars[0], 0)
        self.pf.process_bar(bars[1], 1)
        self.assertTrue(self.pf.is_open("A") and self.pf.is_open("B"))

        self.pf.set_bracket_group(("A", "B"), stop=95.0, target=105.0)
        self.assertEqual(self.pf.positions["A"].stop, 95.0)
        self.assertEqual(self.pf.positions["B"].stop, 95.0)

        self.pf.close("A", reason="one leg only")
        self.pf.process_close(bars[2], 2)
        self.assertFalse(self.pf.is_open("A"))
        self.assertTrue(self.pf.is_open("B"), "closing one id must not touch the other")

    def test_net_position_and_equity_track_open_units(self):
        bars = mkbars([(100, 101, 99, 100)] * 4)
        self.pf.process_bar(bars[0], 0)
        self.pf.limit_entry("A", Side.LONG, 100.5, 2.0)
        self.pf.process_close(bars[0], 0)
        self.pf.process_bar(bars[1], 1)
        self.assertEqual(self.pf.net_position(), 2.0)
        # The limit rested at 100.5 but the bar opened at 100, so it filled at
        # the better price: 2 contracts, +10 points, $20/pt.
        self.assertEqual(self.pf.positions["A"].entry_price, 100.0)
        self.assertAlmostEqual(self.pf.equity(110.0), 10.0 * 2 * 20.0)

    def test_qty_below_one_contract_is_not_ordered(self):
        self.pf.limit_entry("A", Side.LONG, 100.0, 0.0)
        self.assertFalse(self.pf.has_order("A"), "a zero-size setup must be skipped, not sent")


class TestAggregator(unittest.TestCase):
    def test_emits_one_candle_per_completed_bucket(self):
        bars = mkbars([(i, i + 1, i - 1, i) for i in range(1, 31)], step=1)
        agg = TFAggregator(15, 60)
        out = [c for b in bars if (c := agg.update(b))]
        self.assertEqual(len(out), 2, "30 one-minute bars make two 15m candles")

    def test_candle_carries_bucket_high_low_close(self):
        specs = [(10, 12, 8, 11)] + [(11, 20, 5, 15)] + [(15, 16, 14, 15)] * 13
        bars = mkbars(specs, step=1)
        agg = TFAggregator(15, 60)
        out = [c for b in bars if (c := agg.update(b))]
        self.assertEqual(out[0].high, 20.0)
        self.assertEqual(out[0].low, 5.0)
        self.assertEqual(out[0].close, 15.0)

    def test_gap_is_closed_by_catch_up_rather_than_swallowed(self):
        """A bucket whose final bars never arrive still produces a candle."""
        base = mkbars([(10, 12, 8, 11)], step=1)[0]
        later = Bar(base.ts + timedelta(minutes=40), 20, 21, 19, 20, 1)
        agg = TFAggregator(15, 60)
        self.assertIsNone(agg.update(base), "one bar does not complete the bucket")
        got = agg.update(later)
        self.assertIsNotNone(got, "the stale bucket must be caught up, not lost")
        self.assertEqual(got.close, 11.0, "caught up from the last price it saw")


class TestHalyardClock(unittest.TestCase):
    def test_india_anchor_moves_on_the_new_york_clock_across_dst(self):
        winter = datetime(2024, 1, 15, 12, tzinfo=timezone.utc)
        summer = datetime(2024, 7, 15, 12, tzinfo=timezone.utc)
        self.assertEqual(et_offset_minutes(winter), 300)
        self.assertEqual(et_offset_minutes(summer), 240)
        self.assertEqual(hal_range_start_min(winter), 0, "00:00 EST")
        self.assertEqual(hal_range_start_min(summer), 60, "01:00 EDT")
        self.assertEqual(hal_session_end_min(winter), 810, "13:30 EST")
        self.assertEqual(hal_session_end_min(summer), 870, "14:30 EDT")

    def test_session_key_rolls_at_the_boundary_not_at_midnight(self):
        before = datetime(2024, 1, 15, 18, 0, tzinfo=timezone.utc)   # 13:00 ET, before roll
        after = datetime(2024, 1, 15, 19, 0, tzinfo=timezone.utc)    # 14:00 ET, after roll
        self.assertNotEqual(hal_session_key(before), hal_session_key(after))

    def test_session_key_does_not_split_a_session_at_a_month_end(self):
        """Bumping a YYYYMMDD key by one would break here: 20240531 + 1 != 20240601."""
        a = datetime(2024, 6, 1, 2, 0, tzinfo=timezone.utc)     # 31 May 22:00 ET
        b = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)    # 1 Jun 06:00 ET
        self.assertEqual(hal_session_key(a), hal_session_key(b),
                         "both belong to the session ending 1 June")

    def test_session_weekday_belongs_to_the_session_not_the_bar(self):
        # 15:00 ET Monday is already Tuesday's session (roll was 13:30/14:30).
        late_monday = datetime(2024, 1, 15, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(hal_session_weekday(late_monday), 1, "Tuesday")


class TestTPResolver(unittest.TestCase):
    def test_extension_targets_sit_beyond_the_range(self):
        self.assertAlmostEqual(resolve_tp(1, 110, 100, 10, 999, 0, "Ext 10%", "Ext 30%"), 111.0)
        self.assertAlmostEqual(resolve_tp(-1, 110, 100, 10, 999, 0, "Ext 10%", "Ext 30%"), 97.0)

    def test_hod_lod_reads_the_running_session_extremes(self):
        self.assertEqual(resolve_tp(1, 110, 100, 10, 125, 90, "HOD/LOD", "HOD/LOD"), 125)
        self.assertEqual(resolve_tp(-1, 110, 100, 10, 125, 90, "HOD/LOD", "HOD/LOD"), 90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
