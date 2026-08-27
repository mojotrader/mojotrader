# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

### `pinescript/bms_market_structure.pine`
BMS Market Structure — a ZigZag that plots confirmed swing points and labels them
`HH / HL / LH / LL`.

**Swing rule**
- A swing **LOW** is confirmed when a later candle **closes above** the low candle's high.
- A swing **HIGH** is confirmed when a later candle **closes below** the high candle's low.
- Toggle `Confirm swings on candle CLOSE` off to confirm the instant a wick breaks the level instead.

**Close-level markers**
When a swing point is confirmed, a small horizontal line is drawn at the **close
of the confirming candle** — the candle that closed above the low candle's high
(for a swing low) or below the high candle's low (for a swing high).

**Higher-timeframe structure on a lower-timeframe chart**
The same structure engine also runs on a **selectable higher timeframe** and is
overlaid on the current chart, so you can trade a fast chart while seeing the
slower structure. Configurable under the *Higher-timeframe structure* group:
- `Show higher-timeframe structure` — on/off
- `Higher timeframe` — a dropdown; pick any timeframe (defaults to `15`).
- Separate leg colors/width, label toggle, and confirming-candle close markers,
  styled distinctly (blue/orange, thicker) so they stand apart from the chart-
  timeframe structure.

The HTF engine is **non-repainting**: a higher-timeframe bar is only processed
once it has fully closed.

## Strategies

### `pinescript/orbib_strategy.pine`
**ORBIB** — three setups on one equity curve: Halyard (a 15-minute opening-range
break that builds its own 15m candles, so the chart can be 1m/3m/5m/15m), ORB
(09:30–09:45 range) and IB (09:30–10:30 range), both entering on a fib pullback
after the range breaks. Pine nets everything into one position, so a "seat" rule
lets only one setup hold a trade at a time: **Halyard > ORB > IB**.

#### PickMyTrade webhook — the three things that broke live entries

Symptoms were an `unsupported media type` error on the webhook, and stop-loss /
take-profit orders arriving at Tradovate while the **entry** they were meant to
bracket did not.

1. **The plain-text break alerts were sharing the webhook channel.**
   TradingView picks a request's `Content-Type` by parsing the alert text: valid
   JSON is POSTed as `application/json`, anything else as `text/plain`, which
   PickMyTrade rejects with **HTTP 415**. One TradingView alert set to
   *"alert() function calls only"* receives **every** `alert()` call in the
   script — there is no way to route the JSON to the broker and the prose to your
   phone from a single alert. The human-readable `ORB BREAK LONG | broke …`
   messages were therefore being POSTed to PickMyTrade as plain text, on the
   break bar, which is the same bar the entry goes out on.
   *Fix:* break alerts are force-disabled whenever the webhook is enabled
   (`brkLive = brkAlerts and not autoOn`). To keep both, put a second copy of the
   script on the chart with the webhook off and give it its own alert.

2. **`order_type` was sending a word the broker does not use.**
   The payload carried `"market"` / `"limit"`; PickMyTrade documents `MKT` /
   `LMT`. The numeric `sl` and `tp` fields were readable regardless, which is why
   the bracket showed up and the order it belonged to did not — and a naked stop
   order on a flat account is an entry order, so a manually added stop then opened
   a trade of its own.
   *Fix:* both words are inputs (**Broker word for a MARKET / LIMIT order**,
   defaulting to `MKT` / `LMT`) — copy whatever your own PickMyTrade dashboard
   template shows. A boolean, not a string comparison, now decides whether the
   `price` key is written, so renaming the word cannot silently drop the limit
   price.

3. **`quantity` was not NaN-guarded.**
   `str.tostring(na)` renders `NaN`, which is not valid JSON (RFC 8259 has no such
   literal) — so a single unknown value did not produce a partial order, it
   produced a request TradingView downgraded to `text/plain` and PickMyTrade
   refused. Prices go through `f_num` (na → `0`, mintick precision) and sizes
   through `f_qty` (na → `0`, whole contracts).

The debug label now also prints a **preview** of the entry payload before anything
has fired, so the JSON can be pasted into a validator and checked against your
PickMyTrade template without waiting for a setup to arm.
