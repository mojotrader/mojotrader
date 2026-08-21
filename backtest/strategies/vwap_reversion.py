"""VWAP mean reversion against session standard-deviation bands.

Port of ``pinescript/vwap_mean_reversion_strategy.pine``.

Fade a stretch to a band once price closes back inside it, targeting the VWAP
itself. The target is dynamic -- the VWAP moves every bar -- so the bracket is
refreshed on each bar rather than fixed at entry. The stop is not: it is
captured from ATR at the moment of entry and left alone.

The Pine source sets ``process_orders_on_close = true``, so entries fill at the
signal bar's close instead of the next open. That is modelled faithfully here,
but it is the more optimistic of the two Pine fill modes.
"""

from __future__ import annotations

from ..bars import Bar
from ..engine import Broker, Side
from ..indicators import ATR, SessionVWAP
from ..strategy import Strategy


class VWAPReversion(Strategy):
    name = "vwap_reversion"

    process_on_close = True
    commission_per_contract = 2.5

    def __init__(
        self,
        band_mult: float = 2.0,
        atr_len: int = 14,
        atr_mult: float = 1.5,
        min_reward_pts: float = 0.0,
        qty: float = 1.0,
        cutoff: int = 930,   # 15:30 NY
        flatten: int = 955,  # 15:55 NY
    ) -> None:
        self.band_mult = band_mult
        self.atr_mult = atr_mult
        self.min_reward_pts = min_reward_pts
        self.qty = qty
        self.cutoff = cutoff
        self.flatten_at = flatten

        self.atr = ATR(atr_len)
        self.vwap = SessionVWAP()
        self._prev_close: float | None = None
        self._prev_lower: float | None = None
        self._prev_upper: float | None = None
        self._entry_stop: float | None = None

    def on_session_start(self, bar: Bar) -> None:
        self.vwap.reset()
        self._prev_close = None
        self._prev_lower = None
        self._prev_upper = None

    def on_bar(self, bar: Bar, broker: Broker, in_sess: bool) -> None:
        # ATR runs continuously: Pine's ta.atr does not pause outside the
        # session, and restarting it each morning would give the first trades
        # of the day a differently-scaled stop.
        self.atr.update(bar)
        minute = bar.ny_minute()

        if not in_sess:
            self._prev_close = bar.close
            return

        self.vwap.update(bar)
        lower, upper = self.vwap.band(self.band_mult)
        vwap_now = self.vwap.vwap

        if minute >= self.flatten_at:
            broker.close("EOD flat")
            self._remember(bar, lower, upper)
            return

        # Refresh the bracket each bar so the target tracks the moving VWAP.
        if broker.position is not None and vwap_now is not None and self._entry_stop is not None:
            broker.set_bracket(self._entry_stop, vwap_now)

        long_sig = self._crossover(bar.close, lower, self._prev_lower)
        short_sig = self._crossunder(bar.close, upper, self._prev_upper)

        atr_now = self.atr.value
        if broker.flat and minute <= self.cutoff and atr_now is not None:
            if long_sig and self._reward_ok(bar.close, vwap_now, Side.LONG):
                broker.market_entry(Side.LONG, self.qty, "L")
                self._entry_stop = bar.close - self.atr_mult * atr_now
            elif short_sig and self._reward_ok(bar.close, vwap_now, Side.SHORT):
                broker.market_entry(Side.SHORT, self.qty, "S")
                self._entry_stop = bar.close + self.atr_mult * atr_now

        self._remember(bar, lower, upper)

    def _reward_ok(self, entry: float, vwap_now: float | None, side: Side) -> bool:
        """Reject entries whose target is not far enough away to be worth taking.

        Early in a session the deviation bands sit almost on top of the VWAP,
        so a band touch can produce a target within a fraction of a point of
        the entry -- or, when the VWAP has already crossed back past price, on
        the wrong side of it entirely. Those trades pay a full round turn for
        no reward.

        Default ``min_reward_pts = 0.0`` keeps the Pine behaviour exactly,
        including the degenerate cases. Raise it to filter them.
        """
        if self.min_reward_pts <= 0.0:
            return True
        if vwap_now is None:
            return False
        reward = (vwap_now - entry) if side is Side.LONG else (entry - vwap_now)
        return reward >= self.min_reward_pts

    def _remember(self, bar: Bar, lower: float | None, upper: float | None) -> None:
        self._prev_close = bar.close
        self._prev_lower = lower
        self._prev_upper = upper

    def _crossover(self, close: float, level: float | None, prev_level: float | None) -> bool:
        """ta.crossover: was at or below the level, now above it."""
        if level is None or prev_level is None or self._prev_close is None:
            return False
        return self._prev_close <= prev_level and close > level

    def _crossunder(self, close: float, level: float | None, prev_level: float | None) -> bool:
        if level is None or prev_level is None or self._prev_close is None:
            return False
        return self._prev_close >= prev_level and close < level

    def describe(self) -> str:
        guard = f", minR={self.min_reward_pts}pt" if self.min_reward_pts > 0 else ""
        return f"{self.name}(band={self.band_mult}sd, stop={self.atr_mult}xATR{guard})"
