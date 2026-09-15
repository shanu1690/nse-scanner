---
name: nse-data-sources
description: Non-broker data sources - NSE/BSE endpoints, news feeds, fundamentals providers, plus fetching etiquette and terms-of-service notes.
---

# NSE and external data sources

## Fetching etiquette

NSE endpoints require browser-like headers and cookie priming (hit the homepage
first to obtain cookies, then call the API path). Cache aggressively, rate-limit
yourself, and back off on failure. These are public convenience endpoints, not a
contracted API — treat them as fragile and be a polite client.

## Sources

| Layer | Source | Notes |
|---|---|---|
| OI change | NSE option chain | fills the SmartAPI `d_oi` gap |
| Corporate announcements | NSE + BSE announcement feeds | primary event source |
| Corporate actions | NSE corporate actions | splits/bonus/dividend — must drive price adjustment |
| Results calendar | NSE/BSE board meetings | drives earnings blackout |
| Bulk/block deals | NSE daily reports | cheap, underused signal |
| FII/DII flows | NSE daily reports | regime input |
| India VIX | NSE | regime input |
| Fundamentals | Screener.in / Tickertape / paid API | **check ToS before scraping**; prefer a paid feed for daily automated use |
| Filings | BSE XBRL, annual reports | slow but authoritative; quarterly refresh job |
| News | RSS: Moneycontrol, ET Markets, Business Standard, LiveMint | RSS is the intended interface — use it rather than scraping article pages |

## Storage contract

Every record stores `source`, `fetched_at`, `published_at`. The backtest sees only
records with `published_at` before the decision bar.

## Copyright boundary

For news: store the URL, headline, timestamp and **your own short summary**. Never
store or republish full article text. This applies to the database, the data
bundle and the UI equally.

## Before adding any scraped source

Read its terms of service and robots.txt, and report what you found. If a source
prohibits automated access, say so and propose an alternative rather than
proceeding quietly.
