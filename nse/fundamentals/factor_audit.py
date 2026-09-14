"""Walk-forward OOS audit for fundamental factors -- the other half of
Phase 5's "run the factor audit on each new factor exactly as factors/
does today, and report which ones are noise."

This does NOT reuse backtest.py's daily-bar block machinery directly: a
fundamental factor only changes once a quarter, so resampling it on every
trading day (as the technical audit does) would count the same value
~60 times over and manufacture a false sense of sample size -- an extreme
version of the pseudo-replication problem Risk #5 already flagged for
technical signals. Instead, the sampling grid here is the FILING EVENT
itself: one observation per (symbol, quarter actually published), which is
the honest unit of independent information for a fundamental factor.

Given how few filing events even a few years of quarterly data produces
per symbol (typically 8-12), this uses a single trailing/held-out split
rather than backtest.py's multi-block rolling design -- there usually
isn't enough data to support more than one honest split, and pretending
otherwise would just be p-hacking with extra steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from nse import data as data_mod
from nse import indicators as ind
from nse.pit.store import PointInTimeStore

from . import factors as fac

__all__ = ["FilingEvent", "collect_filing_events", "run_fundamental_factor_audit"]

MIN_EVENTS_TOTAL = 20       # below this, refuse to report anything
MIN_EVENTS_PER_SPLIT = 8    # below this on either side of the split, refuse
HELD_OUT_FRACTION = 0.3     # most recent 30% of events, chronologically
HORIZON_TRADING_DAYS = 20   # ~1 month forward, matched to quarterly cadence
TARGET_PCT = 3.0            # same fixed target as the technical audit


@dataclass
class FilingEvent:
    symbol: str
    published_at: datetime
    factor_values: dict
    fwd_pct: Optional[float]


def collect_filing_events(store: PointInTimeStore, universe: list[str],
                           *, anchor_field: str = "net_profit",
                           horizon_trading_days: int = HORIZON_TRADING_DAYS) -> list[FilingEvent]:
    """One event per (symbol, quarter published), using `anchor_field`'s
    own published_at values as the filing calendar (present in essentially
    every filing ingest_symbol() writes). Forward return is measured from
    the next available trading day's close, horizon_trading_days later --
    entering at the close nearest the filing rather than the filing's own
    instant, since that's the earliest a price series bar can represent it.
    """
    far_future = datetime.now(timezone.utc) + timedelta(days=3650)
    events: list[FilingEvent] = []

    for symbol in universe:
        rows = store.as_of([symbol], [anchor_field], far_future)
        published_dates = sorted({r["published_at"] for r in rows})
        if not published_dates:
            continue

        price_df = data_mod.load_price_history(symbol)
        if price_df is None or len(price_df) < 5:
            continue
        close = price_df["Close"]
        dates = price_df.index

        for pub_str in published_dates:
            published_at = datetime.fromisoformat(pub_str)
            pub_naive = published_at.replace(tzinfo=None)
            if pub_naive < dates[0]:
                continue  # filing predates our cached price history -- do
                          # NOT fall through to bar 0, that would silently
                          # price an old filing off today's earliest cached
                          # bar instead of skipping it
            pos = dates.searchsorted(pub_naive, side="right")
            if pos >= len(dates):
                continue  # filing is newer than our cached price history
            entry_price = float(close.iloc[pos])
            factor_values = fac.all_factors(store, symbol, published_at, price=entry_price)
            fwd = None
            fwd_pos = pos + horizon_trading_days
            if fwd_pos < len(dates):
                fwd = (float(close.iloc[fwd_pos]) / entry_price - 1) * 100
            events.append(FilingEvent(symbol=symbol, published_at=published_at,
                                       factor_values=factor_values, fwd_pct=fwd))

    events.sort(key=lambda e: e.published_at)
    return events


def _split_stat(prior_events, current_events, factor_name, target_pct=TARGET_PCT):
    prior_vals = [e.factor_values.get(factor_name) for e in prior_events
                  if e.factor_values.get(factor_name) is not None]
    if not prior_vals:
        return None
    median = float(np.median(prior_vals))

    hi = [e.fwd_pct for e in current_events
          if e.factor_values.get(factor_name) is not None
          and e.factor_values[factor_name] > median and e.fwd_pct is not None]
    lo = [e.fwd_pct for e in current_events
          if e.factor_values.get(factor_name) is not None
          and e.factor_values[factor_name] <= median and e.fwd_pct is not None]
    if not hi or not lo:
        return None
    return {
        "median": median, "n_hi": len(hi), "n_lo": len(lo),
        "hi_mean": float(np.mean(hi)), "lo_mean": float(np.mean(lo)),
        "diff": float(np.mean(hi) - np.mean(lo)),
    }


def run_fundamental_factor_audit(store: PointInTimeStore, universe: list[str],
                                  *, factor_names: Optional[list[str]] = None) -> dict:
    """Returns {"ok": False, "message": ...} when there isn't enough data
    for an honest split, matching backtest.py's sample-size-gate philosophy
    -- refuse rather than force a number out of a handful of quarters.
    """
    factor_names = factor_names or fac.FACTOR_NAMES
    events = collect_filing_events(store, universe)

    if len(events) < MIN_EVENTS_TOTAL:
        return {
            "ok": False,
            "message": (
                f"Only {len(events)} filing events available across "
                f"{len(universe)} symbols; need at least {MIN_EVENTS_TOTAL} for "
                f"even a single honest trailing/held-out split. Quarterly "
                f"filings accumulate slowly -- ingest more symbols and/or wait "
                f"for more quarters to pass rather than reporting a factor "
                f"audit off a handful of data points."
            ),
        }

    split_idx = int(len(events) * (1 - HELD_OUT_FRACTION))
    prior, current = events[:split_idx], events[split_idx:]
    if len(prior) < MIN_EVENTS_PER_SPLIT or len(current) < MIN_EVENTS_PER_SPLIT:
        return {
            "ok": False,
            "message": (
                f"{len(prior)} prior / {len(current)} held-out events after "
                f"splitting -- below the {MIN_EVENTS_PER_SPLIT}-per-side floor. "
                f"Same refusal as above, just discovered after the split."
            ),
        }

    results = {}
    for name in factor_names:
        stat = _split_stat(prior, current, name)
        if stat is not None:
            results[name] = stat

    return {
        "ok": True,
        "n_events_total": len(events),
        "n_prior": len(prior), "n_held_out": len(current),
        "window": (events[0].published_at.date().isoformat(),
                   events[-1].published_at.date().isoformat()),
        "held_out_window": (current[0].published_at.date().isoformat(),
                            current[-1].published_at.date().isoformat()),
        "factors": results,
    }
