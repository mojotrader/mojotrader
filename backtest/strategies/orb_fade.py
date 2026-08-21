"""15m opening-range false-breakout fade.

Port of ``pinescript/orb_fade_strategy.pine``. Defined on 5m candles.

The opening range is 09:30-09:45. After it closes, a candle that pokes outside
the range but closes back inside is treated as a failed breakout and faded. A
candle that *closes* outside instead arms a wait state, and the fade triggers
when a later candle closes back inside the range. Running through the opposite
side voids the wait rather than triggering it -- that is a real breakout, not
a failed one.
"""

from __future__ import annotations

from ..bars import Bar
from ..engine import Broker, Side
from ..strategy import Strategy


class ORBFade(Strategy):
    name = "orb_fade"

    def __init__(
        self,
        break_mode: str = "Both",   # "Wick" | "Close" | "Both"
        rr: float = 1.0,
        max_trades: int = 1,
        qty: float = 1.0,
        orb_end: int = 585,         # 09:45 NY
        cutoff: int = 900,          # 15:00 NY
        flatten: int = 955,         # 15:55 NY
    ) -> None:
        self.break_mode = break_mode
        self.rr = rr
        self.max_trades = max_trades
        self.qty = qty
        self.orb_end = orb_end
        self.cutoff = cutoff
        self.flatten_at = flatten
        self._reset()

    def _reset(self) -> None:
        self.orb_high: float | None = None
        self.orb_low: float | None = None
        self.wait_short = False
        self.wait_long = False
        self.n_trades = 0

    def on_session_start(self, bar: Bar) -> None:
        self._reset()

    def on_bar(self, bar: Bar, broker: Broker, in_sess: bool) -> None:
        minute = bar.ny_minute()

        if in_sess and minute < self.orb_end:
            self.orb_high = bar.high if self.orb_high is None else max(self.orb_high, bar.high)
            self.orb_low = bar.low if self.orb_low is None else min(self.orb_low, bar.low)
            return

        # A freshly filled position has no bracket yet; that is the port's
        # equivalent of Pine's justLong / justShort edge detection.
        if broker.position is not None and broker.position.stop is None:
            self._attach_bracket(broker)

        if minute >= self.flatten_at:
            broker.cancel_entries()
            broker.close("EOD flat")
            return

        if not in_sess or self.orb_high is None or self.orb_low is None:
            return

        take_wick = self.break_mode in ("Wick", "Both")
        take_close = self.break_mode in ("Close", "Both")
        sig_short = sig_long = False

        if take_wick and bar.high > self.orb_high and bar.close <= self.orb_high:
            sig_short = True
        if take_close:
            if bar.close > self.orb_high:
                self.wait_short = True
            elif self.wait_short:
                if self.orb_low < bar.close < self.orb_high:
                    sig_short = True
                    self.wait_short = False
                elif bar.close <= self.orb_low:
                    self.wait_short = False

        if take_wick and bar.low < self.orb_low and bar.close >= self.orb_low:
            sig_long = True
        if take_close:
            if bar.close < self.orb_low:
                self.wait_long = True
            elif self.wait_long:
                if self.orb_low < bar.close < self.orb_high:
                    sig_long = True
                    self.wait_long = False
                elif bar.close >= self.orb_high:
                    self.wait_long = False

        can_trade = broker.flat and minute <= self.cutoff and self.n_trades < self.max_trades
        if not can_trade:
            return
        if sig_short:
            broker.market_entry(Side.SHORT, self.qty, "S")
        elif sig_long:
            broker.market_entry(Side.LONG, self.qty, "L")

    def _attach_bracket(self, broker: Broker) -> None:
        """Target the far side of the range; derive the stop from the R:R."""
        pos = broker.position
        assert pos is not None and self.orb_high is not None and self.orb_low is not None
        self.n_trades += 1
        entry = pos.entry_price
        if pos.side is Side.SHORT:
            target = self.orb_low
            reward = abs(entry - target)
            broker.set_bracket(stop=entry + reward / self.rr, target=target)
        else:
            target = self.orb_high
            reward = abs(target - entry)
            broker.set_bracket(stop=entry - reward / self.rr, target=target)

    def describe(self) -> str:
        return f"{self.name}(mode={self.break_mode}, rr={self.rr}, max/day={self.max_trades})"
