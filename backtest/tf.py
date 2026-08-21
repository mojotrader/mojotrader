"""Build higher-timeframe candles from lower-timeframe bars, in one pass.

A strategy defined on 15m candles cannot simply be run on 15m bars when other
setups in the same script need 1m resolution for their limit fills. The fix is
to aggregate on the fly: every chart bar is assigned to a bucket, and the last
chart bar of a bucket closes that higher-timeframe candle.

Buckets are aligned to the UTC epoch, which is where TradingView's own 15m
bars sit for CME instruments -- the 18:00 ET reopen falls on a 15-minute
boundary in UTC too, so no special-casing is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bars import Bar


@dataclass(slots=True)
class HTFBar:
    """A completed higher-timeframe candle."""

    open_ts: datetime
    high: float
    low: float
    close: float


class TFAggregator:
    """Emit a higher-timeframe candle on the chart bar that completes it.

    Feed every chart bar to :meth:`update`; it returns the candle that closed
    on this bar, or None. Two paths produce a candle:

    * **Projected close** -- the normal case. This bar is the last of its
      bucket because the next bar's timestamp lands in a different one, so the
      candle closes here at this bar's close. Detecting it needs the chart's
      bar interval, which is why ``step_seconds`` is required.
    * **Catch-up** -- a bucket whose final bar never arrived (a data gap, a
      halt, a session end) is closed on the first bar of the *next* bucket,
      out of the aggregate as it stood. Without this a gap would swallow a
      candle and the strategy would silently skip a signal.
    """

    def __init__(self, minutes: int = 15, step_seconds: int = 60) -> None:
        self.bucket_ms = minutes * 60 * 1000
        self.step_ms = step_seconds * 1000
        self._bucket: int | None = None
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._open_ts: datetime | None = None
        self._done = False

    def update(self, bar: Bar) -> HTFBar | None:
        t_ms = int(bar.ts.timestamp() * 1000)
        bucket = t_ms // self.bucket_ms
        emitted: HTFBar | None = None

        # (1) Catch-up, read from the aggregate before it is rolled. ``_close``
        #     still holds the previous bar's close, which is the last price the
        #     unfinished bucket actually saw.
        if self._bucket is not None and bucket != self._bucket and not self._done:
            emitted = HTFBar(self._open_ts, self._high, self._low, self._close)

        # (2) Roll: start a fresh aggregate on a new bucket, else extend.
        if self._bucket is None or bucket != self._bucket:
            self._bucket = bucket
            self._high = bar.high
            self._low = bar.low
            self._open_ts = bar.ts
            self._done = False
        else:
            self._high = max(self._high, bar.high)
            self._low = min(self._low, bar.low)
        self._close = bar.close

        # (3) Projected close: the next bar belongs to a different bucket, so
        #     this bar completes the candle.
        if not self._done and (t_ms + self.step_ms) // self.bucket_ms != bucket:
            self._done = True
            emitted = HTFBar(self._open_ts, self._high, self._low, self._close)

        return emitted
