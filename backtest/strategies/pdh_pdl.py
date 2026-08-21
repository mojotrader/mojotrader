"""Prior-day high/low breakout.

Port of ``pinescript/pdh_pdl_breakout_strategy.pine``.

Track the previous RTH session's range and rest stop orders just beyond both
extremes. Stop orders rather than market orders is the honest choice here: on a
gap through the level the fill comes at the open, which is worse than the
level, and that cost belongs in the results.

The Pine header notes the backtested edge is modest. Nothing in this port
changes that -- it exists so the same rules can be measured against the others
on identical data.
"""

from __future__ import annotations

from ..bars import Bar
from ..engine import Broker, Side
from ..strategy import Strategy


class PDHPDLBreakout(Strategy):
    name = "pdh_pdl"

    def __init__(
        self,
        stop_pts: float = 40.0,
        rr: float = 2.0,
        buffer_pts: float = 0.0,
        max_trades: int = 1,
        qty: float = 1.0,
        start: int = 570,    # 09:30 NY
        cutoff: int = 840,   # 14:00 NY
        flatten: int = 955,  # 15:55 NY
    ) -> None:
        self.stop_pts = stop_pts
        self.rr = rr
        self.buffer_pts = buffer_pts
        self.max_trades = max_trades
        self.qty = qty
        self.start = start
        self.cutoff = cutoff
        self.flatten_at = flatten

        self.sess_high: float | None = None
        self.sess_low: float | None = None
        self.pdh: float | None = None
        self.pdl: float | None = None
        self.n_trades = 0

    def on_session_start(self, bar: Bar) -> None:
        """Roll the session that just ended into the prior-day levels."""
        self.pdh, self.pdl = self.sess_high, self.sess_low
        self.sess_high, self.sess_low = bar.high, bar.low
        self.n_trades = 0

    def on_bar(self, bar: Bar, broker: Broker, in_sess: bool) -> None:
        minute = bar.ny_minute()

        if in_sess:
            self.sess_high = bar.high if self.sess_high is None else max(self.sess_high, bar.high)
            self.sess_low = bar.low if self.sess_low is None else min(self.sess_low, bar.low)

        if broker.position is not None and broker.position.stop is None:
            self._attach_bracket(broker)

        if minute >= self.flatten_at:
            broker.cancel_entries()
            broker.close("EOD flat")
            return

        in_window = in_sess and self.start <= minute <= self.cutoff
        can_arm = (
            broker.flat and in_window and self.n_trades < self.max_trades
            and self.pdh is not None and self.pdl is not None
        )
        if can_arm:
            broker.stop_entry(Side.LONG, self.pdh + self.buffer_pts, self.qty, "L")
            broker.stop_entry(Side.SHORT, self.pdl - self.buffer_pts, self.qty, "S")
        elif broker.flat:
            broker.cancel_entries()

    def _attach_bracket(self, broker: Broker) -> None:
        pos = broker.position
        assert pos is not None
        self.n_trades += 1
        entry = pos.entry_price
        if pos.side is Side.LONG:
            broker.set_bracket(entry - self.stop_pts, entry + self.rr * self.stop_pts)
        else:
            broker.set_bracket(entry + self.stop_pts, entry - self.rr * self.stop_pts)

    def describe(self) -> str:
        return f"{self.name}(stop={self.stop_pts}pt, rr={self.rr}, buf={self.buffer_pts})"
