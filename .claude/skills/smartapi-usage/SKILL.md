---
name: smartapi-usage
description: Angel One SmartAPI integration rules - session/TOTP flow, websocket lifecycle, rate limits, scrip master caching and the known d_oi gap.
---

# SmartAPI usage

## Credentials

Environment variables only. Never a repo file, never a log line, never an error
message, never a response body, never a command-line argument. The browser never
talks to SmartAPI — all broker traffic goes through the backend service.

Session flow: API key + client code + MPIN + TOTP (generated from the stored seed)
-> JWT. Cache the JWT for its lifetime; do not re-authenticate per request.

## Rate limits and etiquette

- REST: roughly 1 request per second sustained. Batch quote requests (up to ~50
  tokens per call).
- Back off exponentially with jitter on 403 and 429. Aggressive retry loops get
  the account throttled.
- Cache the scrip master daily. It is large and static within a session; fetching
  it repeatedly is the most common self-inflicted rate-limit problem.

## SmartWebSocketV2

- Use for live ticks. REST polling is not a live feed.
- Respect per-connection subscription limits; shard across connections if the
  universe exceeds them.
- Heartbeat monitoring: a socket can report connected while delivering nothing.
  Treat N seconds of silence during market hours as a dead connection.
- Auto-reconnect with backoff, plus a REST fallback poller while down.
- Surface connection state and last-tick age so the UI can degrade visibly.

## Known gaps to work around

- **`d_oi` is always 0** in REST quote responses. Cross-source open-interest change
  from the NSE option chain feed.
- Historical data has depth and rate limits; cache aggressively rather than
  re-fetching on every run.
- Scrip master token lookups must handle symbol changes and lot size revisions.

## Read-only boundary

No order placement, modification or cancellation. Ever. Keep a test asserting no
order endpoint is reachable from the service.
