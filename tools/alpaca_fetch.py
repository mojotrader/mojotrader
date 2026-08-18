#!/usr/bin/env python3
"""Fetch intraday bars from Alpaca and write the CSV the ORB/IB backtester reads.

    export APCA_API_KEY_ID=...        # both halves are required
    export APCA_API_SECRET_KEY=...
    python3 tools/alpaca_fetch.py QQQ --start 2025-08-18 --end 2026-08-17 --tf 5Min -o data/qqq_5m.csv

Output columns are time,open,high,low,close - the same shape as a TradingView chart export,
so either source drops straight into orbib_backtest.py.

Notes
  - The free data plan serves the IEX feed, a fraction of consolidated volume, so highs and
    lows can differ from the tape. Range strategies live exactly on those extremes, so use
    --feed sip if the account has it.
  - --adjustment split is the sane default for a multi-year equity pull; raw prices around a
    split turn one session's range into nonsense.
"""
import argparse, csv, datetime, json, os, sys, time, urllib.parse, urllib.request

DATA = "https://data.alpaca.markets/v2/stocks/bars"

def fetch(symbol, start, end, tf, feed, adjustment, key, secret, limit=10000):
    bars, page = [], None
    while True:
        q = dict(symbols=symbol, timeframe=tf, start=start, end=end, limit=limit,
                 feed=feed, adjustment=adjustment, sort="asc")
        if page: q["page_token"] = page
        req = urllib.request.Request(DATA + "?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": key,
                                              "APCA-API-SECRET-KEY": secret,
                                              "accept": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:          # rate limited - back off and retry
                    time.sleep(2 ** attempt)
                    continue
                sys.exit(f"HTTP {e.code} from Alpaca: {e.read()[:400].decode('utf-8','replace')}")
            except Exception as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                sys.exit(f"request failed: {e}")
        got = body.get("bars", {}).get(symbol) or []
        bars.extend(got)
        print(f"  ... {len(bars)} bars", file=sys.stderr)
        page = body.get("next_page_token")
        if not page: break
    return bars

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end",   required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--tf", default="5Min", help="1Min / 5Min / 15Min / 1Hour ...")
    ap.add_argument("--feed", default="iex", choices=["iex", "sip"])
    ap.add_argument("--adjustment", default="split", choices=["raw", "split", "dividend", "all"])
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        sys.exit("set APCA_API_KEY_ID and APCA_API_SECRET_KEY (the key id alone will 401)")
    bars = fetch(a.symbol, a.start + "T00:00:00Z", a.end + "T23:59:59Z",
                 a.tf, a.feed, a.adjustment, key, secret)
    if not bars: sys.exit("no bars returned - check the symbol, the dates and the feed")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["time", "open", "high", "low", "close"])
        for b in bars:
            ts = datetime.datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            w.writerow([int(ts.timestamp()), b["o"], b["h"], b["l"], b["c"]])
    print(f"wrote {len(bars)} bars to {a.out}")

if __name__ == "__main__":
    main()
