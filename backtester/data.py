"""Local price-data store.

Bars are saved as Parquet files in ``data/`` (one file per dataset, e.g. ``MNQ_1min.parquet``) with columns
``time`` (UTC), ``open``, ``high``, ``low``, ``close``, ``volume`` and optionally ``contract``.
Every loader returns a DataFrame indexed by bar-OPEN time in America/New_York.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "America/New_York"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def list_datasets() -> list[str]:
    DATA_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in DATA_DIR.glob("*.parquet"))


def dataset_path(name: str) -> Path:
    return DATA_DIR / f"{name}.parquet"


def save_bars(df: pd.DataFrame, name: str) -> Path:
    """Save bars (index = tz-aware bar-open times) to ``data/<name>.parquet``."""
    DATA_DIR.mkdir(exist_ok=True)
    out = df.copy()
    out.index = out.index.tz_convert("UTC")
    out.index.name = "time"
    path = dataset_path(name)
    out.reset_index().to_parquet(path, index=False)
    return path


def load_bars(name: str) -> pd.DataFrame:
    df = pd.read_parquet(dataset_path(name))
    t = pd.to_datetime(df.pop("time"), utc=True)
    df.index = pd.DatetimeIndex(t).tz_convert(TZ)
    df.index.name = "time"
    return df.sort_index()


def dataset_info(name: str) -> dict:
    df = load_bars(name)
    step = df.index.to_series().diff().median() if len(df) > 1 else pd.Timedelta(0)
    return {"bars": len(df), "start": df.index.min(), "end": df.index.max(),
            "bar_minutes": step.total_seconds() / 60 if pd.notna(step) else None,
            "demo": name.startswith("DEMO")}


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Combine bars into ``minutes``-minute bars (bucketed on the clock, labelled by bar open)."""
    if minutes <= 1:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df:
        agg["volume"] = "sum"
    out = df.resample(f"{minutes}min", label="left", closed="left").agg(agg)
    return out.dropna(subset=["open"])


# ------------------------------------------------------------------ CSV import
_TIME_COLS = ("time", "datetime", "date_time", "timestamp", "ts_event", "date")


def read_csv_bars(data: bytes | str | Path, naive_tz: str = TZ) -> pd.DataFrame:
    """Read OHLC bars from a CSV file.

    Works with TradingView chart exports ("Export chart data"), Databento OHLCV CSVs and most generic files:
    a time column (unix seconds / ms / ns or a date string) plus open, high, low, close[, volume].
    Timestamps without a timezone are assumed to be in ``naive_tz``.
    """
    if isinstance(data, bytes):
        data = io.BytesIO(data)
    df = pd.read_csv(data)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:          # separate date + time columns
        df["datetime"] = df.pop("date").astype(str) + " " + df.pop("time").astype(str)
    tcol = next((c for c in _TIME_COLS if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"No time column found. Columns: {list(df.columns)}")
    for k in ("open", "high", "low", "close"):
        if k not in df.columns:
            raise ValueError(f"Missing '{k}' column. Columns: {list(df.columns)}")
    raw = df[tcol]
    if pd.api.types.is_numeric_dtype(raw):
        mx = raw.abs().max()
        unit = "ns" if mx > 1e17 else "ms" if mx > 1e11 else "s"
        t = pd.to_datetime(raw, unit=unit, utc=True)
    else:
        t = pd.to_datetime(raw, utc=False, format="mixed")
        if getattr(t.dt, "tz", None) is None:
            t = t.dt.tz_localize(naive_tz, ambiguous="NaT", nonexistent="NaT")
    out = pd.DataFrame({k: pd.to_numeric(df[k], errors="coerce") for k in ("open", "high", "low", "close")})
    out["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0) if "volume" in df.columns else 0.0
    out.index = pd.DatetimeIndex(t).tz_convert(TZ)
    out.index.name = "time"
    out = out[out.index.notna()].dropna(subset=["open", "high", "low", "close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def merge_bars(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new.sort_index()
    both = pd.concat([old, new])
    return both[~both.index.duplicated(keep="last")].sort_index()


# ------------------------------------------------------------------ demo data
def make_demo_data(days: int = 260, start_price: float = 18000.0, seed: int = 7,
                   end: str | None = None, trend: float = 0.000002) -> pd.DataFrame:
    """FAKE 1-minute futures bars (random walk with a realistic daily rhythm) so the dashboard can be tried
    before any real data is downloaded. Never judge a strategy on this data."""
    rng = np.random.default_rng(seed)
    end_day = pd.Timestamp(end or pd.Timestamp.now(tz=TZ).normalize()).tz_localize(None).normalize()
    sessions = pd.bdate_range(end=end_day, periods=days)
    frames = []
    price = start_price
    for day in sessions:
        # CME Globex: 18:00 ET the evening before -> 17:00 ET
        t0 = pd.Timestamp(day - pd.Timedelta(days=1)).replace(hour=18).tz_localize(TZ)
        idx = pd.date_range(t0, periods=23 * 60, freq="1min")
        tod = (idx.hour * 60 + idx.minute).to_numpy()
        vol = np.where((tod >= 570) & (tod < 960), 1.0, 0.35)          # busier during RTH
        vol = vol * np.where((tod >= 570) & (tod < 630), 1.8, 1.0)     # busiest after the open
        day_vol = rng.lognormal(0, 0.35)
        drift = rng.normal(0, trend)          # per-minute drift for the day
        rets = rng.standard_t(4, len(idx)) * 0.00032 * vol * day_vol + drift
        close = price * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[price], close[:-1]])
        wick = np.abs(rng.normal(0, 0.00025, len(idx))) * close * vol * day_vol
        high = np.maximum(open_, close) + wick * rng.random(len(idx))
        low = np.minimum(open_, close) - wick * rng.random(len(idx))
        q = lambda a: np.round(a * 4) / 4                                   # 0.25 tick
        frames.append(pd.DataFrame({"open": q(open_), "high": q(high), "low": q(low), "close": q(close),
                                    "volume": (rng.poisson(120, len(idx)) * vol).astype(float)}, index=idx))
        price = close[-1]
    df = pd.concat(frames)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df.index.name = "time"
    return df
