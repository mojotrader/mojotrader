"""Streaming indicators matching Pine's definitions bar by bar.

Each indicator is fed one bar at a time and returns its current value, so a
strategy sees exactly what it would see live -- no lookahead is possible.
"""

from __future__ import annotations

from .bars import Bar


class ATR:
    """Average true range using Wilder smoothing, as ``ta.atr`` does.

    Note this is RMA, not SMA: an SMA-based ATR drifts away from TradingView's
    values and quietly changes every ATR-sized stop in a backtest.
    """

    def __init__(self, length: int = 14) -> None:
        self.length = length
        self._prev_close: float | None = None
        self._value: float | None = None
        self._seeded = 0
        self._seed_sum = 0.0

    def update(self, bar: Bar) -> float | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close

        if self._value is None:
            self._seeded += 1
            self._seed_sum += tr
            if self._seeded >= self.length:
                self._value = self._seed_sum / self.length
        else:
            self._value = (self._value * (self.length - 1) + tr) / self.length
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class SessionVWAP:
    """Anchored VWAP plus the volume-weighted standard deviation of price.

    Reset at each session open and accumulate only inside the session, which is
    what anchoring at the NY open means. The variance is clamped at zero: the
    sum-of-squares form is fast but can go slightly negative on rounding.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pv = 0.0
        self._v = 0.0
        self._p2v = 0.0

    def update(self, bar: Bar) -> None:
        src = bar.hlc3
        vol = bar.volume if bar.volume > 0 else 1.0
        self._pv += src * vol
        self._v += vol
        self._p2v += src * src * vol

    @property
    def vwap(self) -> float | None:
        return self._pv / self._v if self._v > 0 else None

    @property
    def dev(self) -> float | None:
        vw = self.vwap
        if vw is None:
            return None
        var = max(self._p2v / self._v - vw * vw, 0.0)
        return var ** 0.5

    def band(self, mult: float) -> tuple[float | None, float | None]:
        vw, dv = self.vwap, self.dev
        if vw is None or dv is None:
            return None, None
        return vw - mult * dv, vw + mult * dv
