"""A bar-by-bar broker emulator that mirrors TradingView's default fill model.

The point of this module is that a result produced here should be comparable to
the same strategy's TradingView report. That requires copying Pine's timing
rules exactly, and they are easy to get subtly wrong:

Order timing
    With ``process_orders_on_close = false`` (the Pine default), an order placed
    while bar N is being evaluated is eligible to fill from bar N+1 onward,
    never on bar N. That applies to exit brackets too: the stop/limit attached
    on the entry bar's close is live from the *following* bar. Setting
    ``process_on_close=True`` on the engine models the other Pine mode, where
    market orders fill at the current bar's close.

Intrabar ambiguity
    A single bar carries no path information. When one bar's range covers both
    the stop and the target, the true sequence is unknowable, so the engine
    resolves it pessimistically and fills the STOP. TradingView does the same.
    Backtests that silently assume the target came first are the single most
    common source of inflated results.

Gaps
    A stop order whose level is jumped over at the open fills at the open, not
    at the requested level. A limit order gapped through fills at the open too,
    which is better than requested. Both match live routing.

Costs
    Commission is cash per contract per fill, charged on entry and on exit.
    Slippage is applied in ticks against the fill on market and stop orders, in
    the direction that hurts. Limit exits are not slipped: they fill at the
    resting price or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .bars import Bar


class Side(Enum):
    LONG = 1
    SHORT = -1


@dataclass(slots=True)
class Trade:
    """One round-trip, recorded on exit."""

    side: Side
    qty: float
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    exit_reason: str
    commission: float
    bars_held: int
    entry_tag: str = ""

    @property
    def gross_pnl(self) -> float:
        """P&L in points x qty, before costs."""
        return (self.exit_price - self.entry_price) * self.side.value * self.qty

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.commission

    @property
    def points(self) -> float:
        return (self.exit_price - self.entry_price) * self.side.value


@dataclass(slots=True)
class _StopEntry:
    """A resting stop order used to enter on a breakout."""

    side: Side
    level: float
    qty: float
    tag: str


@dataclass(slots=True)
class _Position:
    side: Side
    qty: float
    entry_price: float
    entry_ts: datetime
    entry_bar: int
    tag: str
    stop: float | None = None
    target: float | None = None
    # Brackets set on bar N only become live on bar N+1, matching Pine.
    bracket_live_from: int = 0


class Broker:
    """Single-position broker. Mirrors ``pyramiding = 0``.

    Strategies interact with this object only: they inspect ``position`` and
    call ``market_entry`` / ``stop_entry`` / ``set_bracket`` / ``close``.
    """

    def __init__(
        self,
        point_value: float = 20.0,
        tick_size: float = 0.25,
        commission_per_contract: float = 0.5,
        slippage_ticks: float = 1.0,
        process_on_close: bool = False,
    ) -> None:
        self.point_value = point_value
        self.tick_size = tick_size
        self.commission_per_contract = commission_per_contract
        self.slippage_ticks = slippage_ticks
        self.process_on_close = process_on_close

        self.position: _Position | None = None
        self.trades: list[Trade] = []

        self._pending_market: tuple[Side, float, str] | None = None
        self._stop_entries: list[_StopEntry] = []
        self._pending_close: str | None = None
        self._bar_index = 0

    # ---------------------------------------------------------------- orders
    def market_entry(self, side: Side, qty: float = 1.0, tag: str = "") -> None:
        """Queue a market entry. Fills next bar's open (or this close in on-close mode)."""
        if self.position is not None:
            return
        self._pending_market = (side, qty, tag)

    def stop_entry(self, side: Side, level: float, qty: float = 1.0, tag: str = "") -> None:
        """Rest a breakout stop order. Replaces any existing order with the same tag."""
        if self.position is not None:
            return
        self._stop_entries = [o for o in self._stop_entries if o.tag != tag]
        self._stop_entries.append(_StopEntry(side, level, qty, tag))

    def cancel_entries(self) -> None:
        self._stop_entries.clear()
        self._pending_market = None

    def set_bracket(self, stop: float | None, target: float | None) -> None:
        """Attach or update the protective stop/target on the open position."""
        if self.position is None:
            return
        self.position.stop = stop
        self.position.target = target
        if self.position.bracket_live_from <= self.position.entry_bar:
            self.position.bracket_live_from = self._bar_index + 1

    def close(self, reason: str = "close") -> None:
        """Queue a market exit, filled at the next bar's open."""
        if self.position is not None:
            self._pending_close = reason

    @property
    def flat(self) -> bool:
        return self.position is None

    # ----------------------------------------------------------- bar machine
    def process_bar_open(self, bar: Bar, bar_index: int) -> None:
        """Run every fill that can happen on this bar, before strategy logic.

        Order matters: a queued flatten is honoured before new entries, and the
        protective bracket is checked before a fresh signal can be acted on.
        """
        self._bar_index = bar_index

        if self._pending_close is not None and self.position is not None:
            self._exit(bar, bar.open, self._pending_close, slip=True)
            self._pending_close = None

        if self.position is not None:
            self._check_bracket(bar, bar_index)

        if self.position is None and self._pending_market is not None:
            side, qty, tag = self._pending_market
            self._pending_market = None
            self._enter(side, qty, bar.open, bar, bar_index, tag, slip=True)

        if self.position is None and self._stop_entries:
            self._check_stop_entries(bar, bar_index)

        # A resting entry is only in force for the bar it was armed for; the
        # strategy re-arms it each bar, exactly as the Pine scripts do.
        if self.position is not None:
            self._stop_entries.clear()

    def process_bar_close(self, bar: Bar, bar_index: int) -> None:
        """On-close fills, for strategies running ``process_orders_on_close = true``."""
        if not self.process_on_close:
            return
        self._bar_index = bar_index
        if self._pending_close is not None and self.position is not None:
            self._exit(bar, bar.close, self._pending_close, slip=True)
            self._pending_close = None
        if self.position is None and self._pending_market is not None:
            side, qty, tag = self._pending_market
            self._pending_market = None
            self._enter(side, qty, bar.close, bar, bar_index, tag, slip=True)

    def force_flat(self, bar: Bar, reason: str = "end of data") -> None:
        if self.position is not None:
            self._exit(bar, bar.close, reason, slip=False)

    # ---------------------------------------------------------------- internals
    def _check_stop_entries(self, bar: Bar, bar_index: int) -> None:
        """Fill breakout orders touched by this bar; a gap fills at the open."""
        for order in sorted(self._stop_entries, key=lambda o: o.level):
            if order.side is Side.LONG and bar.high >= order.level:
                px = max(order.level, bar.open)
                self._enter(order.side, order.qty, px, bar, bar_index, order.tag, slip=True)
                break
            if order.side is Side.SHORT and bar.low <= order.level:
                px = min(order.level, bar.open)
                self._enter(order.side, order.qty, px, bar, bar_index, order.tag, slip=True)
                break
        self._stop_entries.clear()

    def _check_bracket(self, bar: Bar, bar_index: int) -> None:
        """Resolve stop/target against this bar, stop-first when both are covered."""
        pos = self.position
        assert pos is not None
        if bar_index < pos.bracket_live_from:
            return

        stop, target = pos.stop, pos.target
        if stop is None and target is None:
            return

        if pos.side is Side.LONG:
            stop_hit = stop is not None and bar.low <= stop
            targ_hit = target is not None and bar.high >= target
            if stop_hit:
                self._exit(bar, min(stop, bar.open), "stop", slip=True)
            elif targ_hit:
                self._exit(bar, max(target, bar.open), "target", slip=False)
        else:
            stop_hit = stop is not None and bar.high >= stop
            targ_hit = target is not None and bar.low <= target
            if stop_hit:
                self._exit(bar, max(stop, bar.open), "stop", slip=True)
            elif targ_hit:
                self._exit(bar, min(target, bar.open), "target", slip=False)

    def _enter(self, side: Side, qty: float, price: float, bar: Bar,
               bar_index: int, tag: str, slip: bool) -> None:
        fill = self._slip(price, side.value) if slip else price
        self.position = _Position(
            side=side, qty=qty, entry_price=fill, entry_ts=bar.ts,
            entry_bar=bar_index, tag=tag, bracket_live_from=bar_index + 1,
        )

    def _exit(self, bar: Bar, price: float, reason: str, slip: bool) -> None:
        pos = self.position
        assert pos is not None
        fill = self._slip(price, -pos.side.value) if slip else price
        commission = self.commission_per_contract * pos.qty * 2.0
        self.trades.append(Trade(
            side=pos.side, qty=pos.qty,
            entry_ts=pos.entry_ts, entry_price=pos.entry_price,
            exit_ts=bar.ts, exit_price=fill, exit_reason=reason,
            commission=commission,
            bars_held=max(self._bar_index - pos.entry_bar, 0),
            entry_tag=pos.tag,
        ))
        self.position = None

    def _slip(self, price: float, direction: int) -> float:
        """Move the fill against us: buys pay up, sells get less."""
        return price + direction * self.slippage_ticks * self.tick_size
