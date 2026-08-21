"""Strategy base class and the bar loop that drives it."""

from __future__ import annotations

from .bars import Bar, in_session
from .engine import Broker, Trade


class Strategy:
    """Subclass this and implement :meth:`on_bar`.

    Contract settings default to MNQ (micro Nasdaq). Override ``point_value``
    and ``tick_size`` per instrument -- getting these wrong scales every dollar
    figure in the report while leaving the point figures correct, which makes
    the error easy to miss.
    """

    name: str = "unnamed"

    point_value: float = 2.0
    tick_size: float = 0.25
    commission_per_contract: float = 0.5
    slippage_ticks: float = 1.0
    process_on_close: bool = False

    session_start: int = 570   # 09:30 NY
    session_end: int = 960     # 16:00 NY

    def on_session_start(self, bar: Bar) -> None:
        """Called on the first in-session bar of each new trading day."""

    def on_bar(self, bar: Bar, broker: Broker, in_sess: bool) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


def run(strategy: Strategy, bars: list[Bar]) -> tuple[list[Trade], Broker]:
    """Feed bars through the broker and the strategy, in that order.

    Fills for orders placed on earlier bars are resolved first, then the
    strategy sees the completed bar. That ordering is what keeps a signal from
    being acted on before the bar that produced it has finished.
    """
    broker = Broker(
        point_value=strategy.point_value,
        tick_size=strategy.tick_size,
        commission_per_contract=strategy.commission_per_contract,
        slippage_ticks=strategy.slippage_ticks,
        process_on_close=strategy.process_on_close,
    )

    prev_in_sess = False
    last_session_date = None
    last_bar: Bar | None = None

    for i, bar in enumerate(bars):
        broker.process_bar_open(bar, i)

        sess = in_session(bar, strategy.session_start, strategy.session_end)
        # A new session is a new calendar date, not merely a re-entry into the
        # session window. RTH-only feeds have no overnight bars, so day N's
        # last bar and day N+1's first bar are both in-session and the
        # transition alone would never fire.
        if sess and (not prev_in_sess or bar.ny_date() != last_session_date):
            strategy.on_session_start(bar)
            last_session_date = bar.ny_date()
        prev_in_sess = sess

        strategy.on_bar(bar, broker, sess)
        broker.process_bar_close(bar, i)
        last_bar = bar

    if last_bar is not None:
        broker.force_flat(last_bar)

    return broker.trades, broker
