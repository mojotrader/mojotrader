import pandas as pd

from backtester.data import read_csv_bars, resample
from backtester.ibkr import plan_windows, roll_boundary


def test_tradingview_export_unix_seconds():
    csv = "time,open,high,low,close,Volume\n1772548200,100,101,99,100.5,10\n1772548260,100.5,102,100,101,12\n"
    df = read_csv_bars(csv.encode())
    assert len(df) == 2
    assert str(df.index.tz) == "America/New_York"
    assert df.index[0] == pd.Timestamp("2026-03-03 09:30", tz="America/New_York")


def test_naive_datetime_strings_and_separate_date_time():
    csv = "Date,Time,Open,High,Low,Close\n2026-03-03,09:30,1,2,0.5,1.5\n2026-03-03,09:31,1.5,2,1,1\n"
    df = read_csv_bars(csv.encode())
    assert df.index[1] == pd.Timestamp("2026-03-03 09:31", tz="America/New_York")


def test_resample_5min():
    idx = pd.date_range(pd.Timestamp("2026-03-03 09:30", tz="America/New_York"), periods=10, freq="1min")
    df = pd.DataFrame({"open": range(10), "high": range(1, 11), "low": range(10), "close": range(10),
                       "volume": 1.0}, index=idx)
    r = resample(df, 5)
    assert len(r) == 2 and r.iloc[0].open == 0 and r.iloc[0].high == 5 and r.iloc[0].close == 4 and r.iloc[0].volume == 5


def test_contract_roll_windows():
    # MNQ Mar 2026 expires Fri 2026-03-20 -> roll on Thu 03-12, switch 18:00 ET on 03-11
    assert roll_boundary("20260320") == pd.Timestamp("2026-03-11 18:00", tz="America/New_York")
    cons = [("M", "MNQM6", "20260619"), ("H", "MNQH6", "20260320"), ("U", "MNQU6", "20260918")]
    start = pd.Timestamp("2026-01-01", tz="America/New_York")
    end = pd.Timestamp("2026-07-01", tz="America/New_York")
    w = plan_windows(cons, start, end)
    assert [x.local_symbol for x in w] == ["MNQH6", "MNQM6", "MNQU6"]
    assert w[0].start == start and w[0].end == w[1].start and w[1].end == w[2].start and w[2].end == end


class _Event:
    def __iadd__(self, fn):
        return self

    def __isub__(self, fn):
        return self


class FakeIB:
    """Pretends to be ib_async.IB: two contracts, 1-minute bars for whatever window is asked."""

    def __init__(self):
        self.errorEvent = _Event()
        self.requests = 0

    def reqContractDetails(self, fut):
        from types import SimpleNamespace as NS
        return [NS(contract=NS(localSymbol="MNQM6", lastTradeDateOrContractMonth="20260619", includeExpired=False)),
                NS(contract=NS(localSymbol="MNQU6", lastTradeDateOrContractMonth="20260918", includeExpired=False))]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, **kw):
        from types import SimpleNamespace as NS
        self.requests += 1
        end = pd.Timestamp(endDateTime.replace("-", " "), tz="UTC") if endDateTime else pd.Timestamp("2026-07-01", tz="UTC")
        days = int(durationStr.split()[0])
        idx = pd.date_range(end - pd.Timedelta(days=days), end, freq="1min", inclusive="left")
        return [NS(date=t.to_pydatetime(), open=1.0, high=2.0, low=0.5, close=1.5, volume=3) for t in idx]


def test_download_stitches_contracts(tmp_path, monkeypatch):
    import backtester.data as dstore
    import backtester.ibkr as ibkr
    monkeypatch.setattr(dstore, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ibkr.pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp("2026-07-01", tz=tz)))
    name = ibkr.download("MNQ", "1 min", days=30, pause=0, ib=FakeIB())
    df = dstore.load_bars(name)
    assert name == "MNQ_1min"
    roll = pd.Timestamp("2026-06-10 18:00", tz="America/New_York")
    assert set(df.loc[df.index < roll, "contract"]) == {"MNQM6"}
    assert set(df.loc[df.index >= roll, "contract"]) == {"MNQU6"}
    assert df.index.is_unique and df.index.is_monotonic_increasing
