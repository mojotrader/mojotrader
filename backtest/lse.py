"""Client for the London Strategic Edge vault API.

SCHEMA ASSUMPTIONS -- READ THIS FIRST
=====================================
This client was written from the dashboard screenshot, not from the published
API reference, because the sandbox that generated it could not reach the docs.
The transport (host, ``x-api-key`` header, HTTPS) is certain. The endpoint
paths and response shapes are inferred, and they are the parts most likely to
be wrong on first run.

Everything uncertain is confined to two places:

* ``ENDPOINTS`` -- the path for each operation. If a call 404s, fix the path
  here and nothing else changes.
* ``_rows`` / ``_to_bar`` -- response parsing. These accept every common OHLCV
  encoding (list-of-dicts, list-of-lists, and the usual envelope keys, with
  epoch-second, epoch-millisecond, or ISO-8601 timestamps), so a shape mismatch
  is more likely to be handled than to crash.

Run ``python3 fetch_data.py --probe`` to see what the API actually returns and
correct the paths from real output rather than from guesswork.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .bars import Bar

BASE_URL = "https://api.londonstrategicedge.com/vault"
WS_URL = "wss://data-ws.londonstrategicedge.com"

# Inferred paths. Correct these first if a call returns 404.
ENDPOINTS = {
    "candles": "/candles",
    "catalog": "/catalog",
    "series": "/series",
    "options": "/options",
    "export": "/export",
}


class LSEError(RuntimeError):
    """An API call failed. Carries the status code where there is one."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class LSEClient:
    """Thin, dependency-free wrapper over the vault HTTP API.

    The key is read from the ``LSE_API_KEY`` environment variable by default so
    it never has to be written into a file that might be committed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        timeout: int = 60,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("LSE_API_KEY", "")
        if not self.api_key:
            raise LSEError(
                "No API key. Set it in your shell:\n"
                "    export LSE_API_KEY='lse_live_...'\n"
                "or pass api_key= explicitly."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------- transport
    def request(self, path: str, params: dict | None = None,
                method: str = "GET", body: dict | None = None) -> object:
        """One API call, with backoff on transient failures.

        Retries 429 and 5xx with exponential backoff; 4xx other than 429 fail
        immediately, since retrying a bad key or a bad symbol just wastes quota.
        """
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean)

        payload = json.dumps(body).encode() if body is not None else None
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code == 401 or e.code == 403:
                    raise LSEError(f"Auth rejected ({e.code}). Check LSE_API_KEY.", e.code, detail)
                if e.code == 404:
                    raise LSEError(
                        f"404 for {url}\nThe path in ENDPOINTS is probably wrong -- "
                        f"see the SCHEMA ASSUMPTIONS note in backtest/lse.py.", e.code, detail)
                if e.code != 429 and e.code < 500:
                    raise LSEError(f"HTTP {e.code} for {url}: {detail}", e.code, detail)
                last_err = LSEError(f"HTTP {e.code} for {url}: {detail}", e.code, detail)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e

            if attempt < self.max_retries - 1:
                time.sleep(2 ** attempt)

        raise LSEError(f"Failed after {self.max_retries} attempts: {last_err}")

    # -------------------------------------------------------------- endpoints
    def catalog(self) -> object:
        """List the symbols available to this key."""
        return self.request(ENDPOINTS["catalog"])

    def candles(self, symbol: str, resolution: str,
                start: str | None = None, end: str | None = None) -> list[Bar]:
        """OHLCV for one symbol. Dates are ``YYYY-MM-DD``."""
        raw = self.request(ENDPOINTS["candles"], {
            "symbol": symbol, "resolution": resolution, "start": start, "end": end,
        })
        return [b for b in (_to_bar(r) for r in _rows(raw)) if b is not None]

    def series(self, series_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
        """A macro/bond series as date-value rows."""
        raw = self.request(ENDPOINTS["series"], {"series": series_id, "start": start, "end": end})
        return list(_rows(raw))

    def options(self, symbol: str, **params) -> list[dict]:
        """Options chains, prints, or contract candles."""
        raw = self.request(ENDPOINTS["options"], {"symbol": symbol, **params})
        return list(_rows(raw))

    def export(self, dataset: str, symbol: str, poll_seconds: int = 5,
               max_wait: int = 1800, **params) -> object:
        """Start a server-side bulk export and poll until it is ready.

        Bulk is the right path for raw tick history; ``candles`` is paginated
        and will be slow for multi-year pulls.
        """
        job = self.request(ENDPOINTS["export"], method="POST",
                           body={"dataset": dataset, "symbol": symbol, **params})
        job_id = _first(job, ("job_id", "id", "jobId"))
        if job_id is None:
            return job

        deadline = time.time() + max_wait
        while time.time() < deadline:
            status = self.request(f"{ENDPOINTS['export']}/{job_id}")
            state = str(_first(status, ("status", "state")) or "").lower()
            if state in ("ready", "complete", "completed", "done", "succeeded"):
                return status
            if state in ("failed", "error", "cancelled"):
                raise LSEError(f"Export job {job_id} ended as {state}: {status}")
            time.sleep(poll_seconds)

        raise LSEError(f"Export job {job_id} not ready after {max_wait}s.")


# ------------------------------------------------------------------ parsing
_ENVELOPE_KEYS = ("candles", "data", "rows", "results", "bars", "items", "values")


def _rows(payload: object) -> list:
    """Pull the row list out of whatever envelope the API used."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _ENVELOPE_KEYS:
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # Columnar form: {"t": [...], "o": [...], ...}
        if isinstance(payload.get("t"), list):
            keys = ("t", "o", "h", "l", "c", "v")
            cols = [payload.get(k, []) for k in keys]
            return [list(row) for row in zip(*cols)]
    return []


def _first(obj: object, keys: tuple[str, ...]) -> object:
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _to_bar(row: object) -> Bar | None:
    """Normalise one row into a :class:`Bar`, or None if it is unusable."""
    try:
        if isinstance(row, (list, tuple)):
            if len(row) < 5:
                return None
            ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
            v = row[5] if len(row) > 5 else 0.0
        elif isinstance(row, dict):
            ts = _first(row, ("t", "ts", "time", "timestamp", "date", "datetime"))
            o = _first(row, ("o", "open", "Open"))
            h = _first(row, ("h", "high", "High"))
            l = _first(row, ("l", "low", "Low"))
            c = _first(row, ("c", "close", "Close"))
            v = _first(row, ("v", "volume", "Volume")) or 0.0
        else:
            return None

        if ts is None or o is None or h is None or l is None or c is None:
            return None
        return Bar(_to_utc(ts), float(o), float(h), float(l), float(c), float(v))
    except (TypeError, ValueError):
        return None


def _to_utc(ts: object) -> datetime:
    """Accept epoch seconds, epoch milliseconds, or ISO-8601."""
    if isinstance(ts, (int, float)):
        # Anything past ~2001 in milliseconds exceeds this bound in seconds.
        seconds = ts / 1000.0 if ts > 1e11 else float(ts)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    text = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unrecognised timestamp: {ts!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
