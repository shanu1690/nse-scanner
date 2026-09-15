# Phase 2 integration — concrete wiring examples

These are illustrative, not a diff against your real files — I don't have their
current contents in this session. Hand this file to Claude Code alongside
`PROJECT_BRIEF.md` and let it match these call sites to your actual function and
variable names.

---

## 1. `nse/data.py` — corporate action adjustment before anything reads price history

```python
from nse.quality.corporate_actions import adjust_ohlcv, detect_unadjusted, CorporateAction

def load_price_history(symbol: str, actions: list[CorporateAction]) -> pd.DataFrame:
    raw = fetch_ohlcv(symbol)                       # your existing fetch
    adjusted = adjust_ohlcv(raw, actions)

    # Safety net: anything that still looks like an unhandled split/bonus
    # should halt the run for that symbol rather than silently score around it.
    flagged = detect_unadjusted(adjusted, known_actions=actions)
    if not flagged.empty:
        log.error(f"{symbol}: unexplained price jump(s) after adjustment — "
                  f"halting until events pipeline confirms or corrects: "
                  f"{flagged[['gap', 'suspicious_ratio']].to_dict('records')}")
        raise DataQualityError(symbol, flagged)

    return adjusted
```

`actions` should come from your events/corporate-actions ingest (Phase 4), keyed
by symbol and ex-date.

---

## 2. `nse/backtest.py` — point-in-time fundamentals, never a direct fundamentals read

```python
from nse.pit.store import PointInTimeStore

pit = PointInTimeStore("data/pit.db")

def build_feature_row(symbol: str, decision_time: datetime) -> dict:
    fundamentals = pit.latest_as_of(
        [symbol], ["revenue", "eps", "roe", "debt_equity"], decision_time,
        max_age_days=400,   # a company that stopped reporting drops out, not lingers
    )
    # fundamentals is keyed by (symbol, field) -> row with 'value', 'period_end', 'published_at'
    # Never fall back to a plain fundamentals dataframe indexed by period end —
    # that reintroduces the exact leak this store exists to close.
    return {
        "revenue": fundamentals.get((symbol, "revenue"), {}).get("value"),
        "eps": fundamentals.get((symbol, "eps"), {}).get("value"),
        ...
    }
```

Add this assertion inside the walk-forward loop, once per decision date, as a
belt-and-braces check:

```python
pit.assert_no_lookahead(decision_time)
```

---

## 3. `nse/backtest.py` — session-counted horizons instead of calendar-day math

```python
from nse.calendar.market_calendar import MarketCalendar

cal = MarketCalendar("config/holidays")

# Before: exit_date = decision_date + timedelta(days=5)   <- wrong across holiday weeks
exit_date = cal.shift_sessions(decision_date, 5)
holding_sessions = cal.sessions_between(decision_date, exit_date)
```

Anywhere the backtest currently says "5 days" it almost certainly means "5
sessions" — check every occurrence, not just the obvious ones.

---

## 4. `nse/sitebuilder.py` — validator gates the publish step

```python
from nse.quality.validators import DataValidator, scan_bundle_for_credentials

def publish_bundle(picks_df: pd.DataFrame, universe: list[str], previous_count: int):
    report = DataValidator().validate(
        picks_df, universe=universe, as_of=datetime.now(timezone.utc),
        previous_row_count=previous_count,
    )
    log.info(report.render())

    if not report.may_publish:
        log.error("Publish blocked — leaving previous bundle live")
        alert_on_call(report.render())          # your alerting hook
        return  # <-- do not write the new bundle

    payload = picks_df.to_json(orient="records")
    leaked = scan_bundle_for_credentials(payload)
    if leaked:
        log.critical(f"Credential-shaped keys in outgoing bundle: {leaked}")
        alert_on_call(f"BLOCKED: possible credential leak in bundle: {leaked}")
        return  # <-- also do not write

    write_bundle(payload)   # your existing write step
```

This is the one call site I'd treat as non-negotiable — it's the difference
between "a bad run gets caught" and "a bad run gets published because nobody
gated it."

---

## 5. Wiring order for Claude Code to follow

Do these in this order, and run `pytest tests/ -q` after each — the Phase 2
package's 37 tests should keep passing throughout, and any new integration test
you add should fail before the wiring and pass after:

1. Corporate action adjustment into the price-loading path (#1)
2. Validator gating the publish step (#4) — highest leverage, do this even if
   the others slip
3. Session-counted horizons in the backtest (#3)
4. Point-in-time fundamentals lookup (#2) — this one waits on Phase 5
   (fundamentals ingest) actually populating the store, so it can land as a
   stub now and go live later

---

## On model selection for running this (and future phases) in Claude Code

You asked earlier which model to use given token constraints — worth answering
directly:

- **Sonnet 5** for the bulk of this: wiring code, writing tests, following the
  phase prompts. It's the efficient default and these are well-specified tasks.
- **Opus 5** specifically for the Phase 1 repo audit and for the
  `adversarial-validator` work in Phase 6 (hunting leakage, stress-testing the
  fusion model). Those are the two places where a subtler reasoning error is
  expensive — a missed leak there produces a fake edge that looks real for
  months. Everywhere else, Opus is spending more than the task needs.
- **Start a fresh Claude Code session per phase** rather than one long-running
  conversation. Each phase prompt is already scoped to stand alone with
  `PROJECT_BRIEF.md` as context, so a new session re-reads only what it needs
  instead of carrying forward an ever-growing history — that's most of your
  token efficiency right there, independent of which model you pick.
