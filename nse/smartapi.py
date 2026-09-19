"""Angel One SmartAPI data provider.

Replaces direct NSE fetching (nse_api) and yfinance (data.py) with Angel One's
free SmartAPI. Requires a free Angel One account:

  1. Enable TOTP (Google Authenticator) in the Angel One app.
  2. Create an API key at https://smartapi.angelbroking.com
     (developer dashboard -> My Apps).
  3. Put the credentials in secrets.yaml (see secrets.yaml.example):
       smartapi:
         api_key: <your Angel One API key>
         client_id: <your Angel One client/trading ID>
         pin: <your 4-digit trading PIN>
         totp_secret: <base32 secret from the TOTP QR code>
  4. pip install smartapi-python pyotp

Capabilities:
  - option_chain(symbol): SmartAPI has NO REST option-chain endpoint, so the
    chain is reconstructed from the OpenAPI Scrip Master (token/strike/lotsize
    per contract) + getMarketData(FULL) for OI/volume/LTP + optionGreek for IV.
    The result is shaped exactly like the NSE v3 payload so options.analyze_
    option_chain() works unchanged. NOTE: REST quotes expose OI as "opnInterest"
    but NO change-in-OI, so changeinOpenInterest is always 0 (PCR, max pain, IV
    rank, momentum trend still work; the OI-build-up signal is dropped).
  - lot_sizes(): contract lot sizes straight from the Scrip Master.
  - candles(symbol): daily OHLCV via getCandleData (replaces yfinance).

Every failure raises SmartAPIUnavailable so callers keep their existing
NSE/yfinance fallbacks. The session logs in lazily on first use.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from nse.quality.corporate_actions import detect_unadjusted
from nse.quality.events import log_event

try:
    from SmartApi.smartConnect import SmartConnect
except ImportError:  # pragma: no cover - not installed
    SmartConnect = None

try:
    import pyotp
except ImportError:  # pragma: no cover - not installed
    pyotp = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

SCRIP_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_TTL = timedelta(days=1)
SCRIP_URLS = [
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
]

NIFTY_INDEX_TOKEN = "99926000"  # NIFTY 50 spot index
BATCH_SIZE = 50                 # getMarketData max tokens per request
REQUEST_GAP = 1.1               # seconds between authenticated calls (rate limit ~1/s)

_SESSION = None
_SESSION_LOCK = threading.Lock()


class SmartAPIUnavailable(RuntimeError):
    pass


def _load_credentials():
    """Read the `smartapi` block from secrets.yaml (empty dict if missing).

    Environment variables take precedence (used by the GitHub Actions
    nightly job): SMARTAPI_API_KEY, SMARTAPI_CLIENT_ID, SMARTAPI_PIN,
    SMARTAPI_TOTP_SECRET.
    """
    import yaml
    creds = {}
    for key, env in (("api_key", "SMARTAPI_API_KEY"),
                     ("client_id", "SMARTAPI_CLIENT_ID"),
                     ("pin", "SMARTAPI_PIN"),
                     ("totp_secret", "SMARTAPI_TOTP_SECRET")):
        if os.environ.get(env):
            creds[key] = os.environ[env]
    path = os.path.join(ROOT, "secrets.yaml")
    if os.path.exists(path):
        try:
            with open(path) as fh:
                cfg = yaml.safe_load(fh) or {}
            file_creds = cfg.get("smartapi") or {}
            for k, v in file_creds.items():
                creds.setdefault(k, v)
        except (OSError, ValueError):
            pass
    return creds


def provider_enabled():
    """True when config.yaml -> data.provider is 'smartapi'."""
    import yaml
    try:
        with open(os.path.join(ROOT, "config.yaml")) as fh:
            cfg = yaml.safe_load(fh) or {}
        return (cfg.get("data") or {}).get("provider", "nse") == "smartapi"
    except (OSError, ValueError):
        return False


def _fmt_nse_expiry(expiry):
    """'25AUG2026' -> '25-Aug-2026' (NSE v3 date format)."""
    try:
        return datetime.strptime(expiry, "%d%b%Y").strftime("%d-%b-%Y")
    except (TypeError, ValueError):
        return expiry


def _is_rate_limit(exc):
    """True when an exception looks like Angel's rate-limit rejection."""
    msg = str(exc).lower()
    return ("access rate" in msg or "too many requests" in msg
            or "ab1021" in msg or "rate limit" in msg)


class SmartAPISession:
    """Authenticated SmartAPI accessor with lazy login + cached scrip master."""

    def __init__(self, api_key=None, client_id=None, pin=None, totp_secret=None):
        self._api_key = api_key
        self._client_id = client_id
        self._pin = pin
        self._totp = totp_secret
        self._obj = None
        self._scrip = None
        self._scrip_ts = None
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._shared = False

    # ---- auth ---------------------------------------------------------------

    def _ensure_creds(self):
        if all((self._api_key, self._client_id, self._pin, self._totp)):
            return
        creds = _load_credentials()
        self._api_key = self._api_key or creds.get("api_key")
        self._client_id = self._client_id or creds.get("client_id")
        self._pin = self._pin or creds.get("pin")
        self._totp = self._totp or creds.get("totp_secret")
        if not all((self._api_key, self._client_id, self._pin, self._totp)):
            raise SmartAPIUnavailable(
                "SmartAPI credentials missing: add a `smartapi:` block to "
                "secrets.yaml (see secrets.yaml.example).")

    def _ensure_login(self):
        if self._obj is not None:
            return
        self._ensure_creds()
        if SmartConnect is None or pyotp is None:
            raise SmartAPIUnavailable("pip install smartapi-python pyotp")
        try:
            obj = SmartConnect(api_key=self._api_key)
            totp = pyotp.TOTP(self._totp).now()
            res = obj.generateSession(self._client_id, self._pin, totp)
        except Exception as exc:
            raise SmartAPIUnavailable(f"SmartAPI login failed: {exc}")
        if not (isinstance(res, dict) and res.get("status")):
            raise SmartAPIUnavailable(
                f"SmartAPI login rejected: {(res or {}).get('message')}")
        self._obj = obj

    def _throttle(self):
        with self._lock:
            wait = REQUEST_GAP - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _retry_call(self, fn, attempts=3, base_wait=5):
        """Call fn; retry on Angel's flaky 403 rate-limit errors, else fail fast."""
        last = None
        for i in range(attempts):
            self._throttle()
            try:
                return fn()
            except Exception as exc:
                last = exc
                if not _is_rate_limit(exc):
                    raise SmartAPIUnavailable(f"smartapi call failed: {exc}")
                time.sleep(base_wait * (i + 1))
        raise SmartAPIUnavailable(
            f"smartapi rate-limited after {attempts} tries: {last}")

    def close(self):
        if self._shared:
            return  # ownership is process-wide; see close_shared_session()
        if self._obj is None:
            return
        try:
            self._obj.terminateSession(self._client_id)
        except Exception:
            pass
        self._obj = None

    # ---- scrip master -------------------------------------------------------

    def scrip_master(self, force=False):
        """Full OpenAPI Scrip Master (cached daily)."""
        if self._scrip is not None and not force:
            if self._scrip_ts and datetime.now() - self._scrip_ts <= SCRIP_TTL:
                return self._scrip
        if not force:
            try:
                with open(SCRIP_CACHE) as fh:
                    payload = json.load(fh)
                ts = datetime.fromisoformat(payload["_fetched_at"])
                if datetime.now() - ts <= SCRIP_TTL and payload.get("data"):
                    self._scrip, self._scrip_ts = payload["data"], ts
                    return self._scrip
            except (OSError, KeyError, ValueError):
                pass
        import requests
        last_err = None
        for url in SCRIP_URLS:
            try:
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
                continue
            if isinstance(data, list) and data:
                try:
                    with open(SCRIP_CACHE, "w") as fh:
                        json.dump({"_fetched_at": datetime.now().isoformat(),
                                   "data": data}, fh)
                except OSError:
                    pass
                self._scrip, self._scrip_ts = data, datetime.now()
                return data
        if self._scrip is not None:
            return self._scrip
        raise SmartAPIUnavailable(f"Scrip master unavailable: {last_err}")

    def _find(self, exch_seg, instrumenttype=None, name=None):
        for c in self.scrip_master():
            if c.get("exch_seg") != exch_seg:
                continue
            if instrumenttype and c.get("instrumenttype") != instrumenttype:
                continue
            if name and c.get("name") != name:
                continue
            yield c

    def _equity_contract(self, symbol):
        for c in self._find("NSE", name=symbol):
            return c
        return None

    def _index_contract(self):
        for c in self._find("NSE", instrumenttype="AMXIDX", name="NIFTY"):
            return c
        return {"token": NIFTY_INDEX_TOKEN}

    def _nfo_contracts(self, symbol, instrumenttype="OPTSTK"):
        out = []
        for c in self._find("NFO", instrumenttype=instrumenttype, name=symbol):
            strike = self._to_float(c.get("strike"))
            expiry = c.get("expiry") or ""
            side = (c.get("symbol") or "")[-2:].upper()
            try:
                expiry_dt = datetime.strptime(expiry, "%d%b%Y")
            except ValueError:
                continue
            if strike is None or side not in ("CE", "PE"):
                continue
            out.append({
                "token": str(c.get("token")),
                "strike_raw": strike,
                "optiontype": side,
                "expiry_dt": expiry_dt,
                "expiry_orig": expiry,
                "expiry_nse": _fmt_nse_expiry(expiry),
                "lotsize": c.get("lotsize"),
            })
        return out

    @staticmethod
    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ---- market data --------------------------------------------------------

    def _market_data(self, exchange, tokens):
        """FULL-mode quotes keyed by token, batched at 50 with rate limiting."""
        self._ensure_login()
        quotes = {}
        for i in range(0, len(tokens), BATCH_SIZE):
            batch = tokens[i:i + BATCH_SIZE]
            res = self._retry_call(lambda: self._obj.getMarketData("FULL",
                                      {exchange: batch}))
            data = (res or {}).get("data") or {}
            for q in data.get("fetched") or []:
                tok = str(q.get("symbolToken") or q.get("token") or "")
                if tok:
                    quotes[tok] = q
        return quotes

    @staticmethod
    def _qval(quote, *keys):
        for k in keys:
            v = quote.get(k)
            if v not in (None, "", "-"):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _q_oi(self, q):
        # Live getMarketData(FULL) names the field "opnInterest" (Angel's
        # spelling). Change-in-OI is NOT present in REST quotes, so _q_doi
        # always yields 0; PCR / max pain / IV / trend signals still work.
        return self._qval(q, "opnInterest", "open_interest", "openInterest",
                          "openinterst", "oi")

    def _q_doi(self, q):
        return self._qval(q, "changeinopeninterest", "changeinOpenInterest",
                          "changeinopeninterst")

    def _q_vol(self, q):
        return self._qval(q, "totalTradedVolume", "tradeVolume", "volume")

    def _q_ltp(self, q):
        return self._qval(q, "ltp", "lastPrice", "LTP")

    def _quote_ltp(self, exchange, token):
        quotes = self._market_data(exchange, [str(token)])
        q = quotes.get(str(token))
        return self._q_ltp(q) if q else 0.0

    # ---- public: current spot quotes (intraday monitoring, nse/intraday.py)

    def ltp_batch(self, symbols):
        """Current LTP for a list of NSE equity symbols, one batched call.
        {symbol: ltp}. A symbol with no scrip-master match or an empty
        quote is simply absent from the result, not raised -- a monitoring
        job checking several positions should degrade per-symbol, not
        abort entirely because one lookup failed."""
        self._ensure_login()
        tokens_by_symbol = {}
        for sym in symbols:
            c = self._equity_contract(sym)
            if c and c.get("token"):
                tokens_by_symbol[sym] = str(c["token"])
        if not tokens_by_symbol:
            return {}
        quotes = self._market_data("NSE", list(tokens_by_symbol.values()))
        out = {}
        for sym, tok in tokens_by_symbol.items():
            q = quotes.get(tok)
            ltp = self._q_ltp(q) if q else 0.0
            if ltp:
                out[sym] = ltp
        return out

    def index_ltp(self, name="NIFTY"):
        """Current LTP for an NSE index. None (never raises) if the index
        isn't in the scrip master or its quote comes back empty -- callers
        treat a missing read as 'this signal is unavailable this cycle',
        not a hard failure."""
        self._ensure_login()
        contract = next(self._find("NSE", instrumenttype="AMXIDX", name=name), None)
        if contract is None and name == "NIFTY":
            contract = {"token": NIFTY_INDEX_TOKEN}
        if contract is None or not contract.get("token"):
            return None
        ltp = self._quote_ltp("NSE", contract["token"])
        return ltp or None

    def _option_greeks(self, symbol, expiry_orig, scale):
        """{(strike, optiontype): iv} for one expiry. Best-effort; {} if it fails."""
        self._ensure_login()
        try:
            res = self._retry_call(lambda: self._obj.optionGreek(
                {"name": symbol, "expirydate": expiry_orig}))
        except SmartAPIUnavailable:
            return {}
        out = {}
        for r in (res or {}).get("data") or []:
            side = str(r.get("optiontype") or r.get("optionType") or "").upper()
            strike = self._to_float(r.get("strikeprice") or r.get("strikePrice"))
            iv = self._to_float(r.get("impliedvolatility") or r.get("impliedVolatility"))
            if side not in ("CE", "PE") or strike is None:
                continue
            # greek strikes may come in rupees or paise; index both so either
            # representation matches the (scale-normalised) contract strikes.
            out[(round(strike * scale, 2), side)] = iv or 0.0
            out[(round(strike * scale * 100, 2), side)] = iv or 0.0
        return out

    # ---- public API ---------------------------------------------------------

    def option_chain(self, symbol):
        """Reconstruct an NSE-v3-shaped option chain for the nearest expiry."""
        contracts = self._nfo_contracts(symbol, "OPTSTK")
        if not contracts:
            raise SmartAPIUnavailable(
                f"{symbol}: no F&O option contracts (delisted / not in derivatives)")

        eq = self._equity_contract(symbol)
        spot = self._quote_ltp("NSE", eq["token"]) if eq else 0.0
        if not spot:
            spot = self._futures_spot(symbol)
        if not spot:
            raise SmartAPIUnavailable(f"{symbol}: no spot price available")

        by_expiry = {}
        for c in contracts:
            by_expiry.setdefault(c["expiry_dt"], []).append(c)
        expiries = sorted(by_expiry)
        nearest = expiries[0]
        chosen = by_expiry[nearest]
        all_expiries = [_fmt_nse_expiry(d.strftime("%d%b%Y")) for d in expiries]

        raw_strikes = sorted(c["strike_raw"] for c in chosen)
        scale = 0.01 if raw_strikes[len(raw_strikes) // 2] > spot * 10 else 1.0

        quotes = self._market_data("NFO", [c["token"] for c in chosen])
        ivs = self._option_greeks(symbol, chosen[0]["expiry_orig"], scale)

        by_strike = {}
        for c in chosen:
            st = round(c["strike_raw"] * scale, 2)
            side = c["optiontype"]
            q = quotes.get(c["token"], {})
            item = by_strike.setdefault(st, {
                "strikePrice": st,
                "expiryDates": all_expiries,
                "CE": None,
                "PE": None,
            })
            item[side] = {
                "openInterest": self._q_oi(q),
                "changeinOpenInterest": self._q_doi(q),
                "totalTradedVolume": self._q_vol(q),
                "impliedVolatility": ivs.get((st, side), 0.0),
                "lastPrice": self._q_ltp(q),
            }

        empty_leg = {"openInterest": 0, "changeinOpenInterest": 0,
                     "totalTradedVolume": 0, "impliedVolatility": 0,
                     "lastPrice": 0}
        data = []
        for st in sorted(by_strike):
            item = by_strike[st]
            item["CE"] = item["CE"] or dict(empty_leg)
            item["PE"] = item["PE"] or dict(empty_leg)
            data.append(item)

        return {
            "records": {
                "data": data,
                "timestamp": datetime.now().isoformat(),
                "underlyingValue": round(spot, 2),
                "expiryDates": all_expiries,
                "strikePrices": [i["strikePrice"] for i in data],
            },
            "filtered": {"data": [], "timestamp": datetime.now().isoformat()},
        }

    def option_chain_equity(self, symbol, expiry=None):
        """Alias matching NSESession's interface (expiry ignored: nearest used)."""
        return self.option_chain(symbol)

    def _futures_spot(self, symbol):
        futs = self._nfo_contracts(symbol, "FUTSTK")
        if not futs:
            return 0.0
        futs = sorted(futs, key=lambda c: c["expiry_dt"])
        nearest = [c for c in futs if c["expiry_dt"] == futs[0]["expiry_dt"]]
        quotes = self._market_data("NFO", [c["token"] for c in nearest])
        for c in nearest:
            q = quotes.get(c["token"])
            if q and self._q_ltp(q):
                return self._q_ltp(q)
        return 0.0

    def lot_sizes(self):
        """{symbol: lot size} for every F&O option underlying (nearest expiry)."""
        best = {}
        for itype in ("OPTSTK", "OPTIDX"):
            self._collect_lots(best, itype)
        return {name: lots for name, (_, lots) in best.items()}

    def _collect_lots(self, best, itype):
        for c in self._find("NFO", instrumenttype=itype):
            name = c.get("name")
            if not name:
                continue
            try:
                expiry_dt = datetime.strptime(c.get("expiry") or "", "%d%b%Y")
                lotsize = int(c.get("lotsize"))
            except (ValueError, TypeError):
                continue
            if expiry_dt < datetime.now():
                continue
            if name not in best or expiry_dt < best[name][0]:
                best[name] = (expiry_dt, lotsize)

    def candles(self, symbol, fromdate, todate, interval="ONE_DAY"):
        """Daily OHLCV DataFrame (Open/High/Low/Close/Volume) via getCandleData."""
        self._ensure_login()
        if symbol == "^NSEI":
            contract = self._index_contract()
        else:
            contract = self._equity_contract(symbol)
        if contract is None:
            raise SmartAPIUnavailable(f"{symbol}: not found in scrip master")
        token = contract["token"]

        d0 = pd.Timestamp(fromdate).normalize()
        d1 = pd.Timestamp(todate).normalize()
        if d0 > d1:
            d0, d1 = d1, d0

        rows = []
        chunk = 300  # calendar days per request (well under the 500-candle cap)
        cursor = d0
        while cursor <= d1:
            end = min(cursor + pd.Timedelta(days=chunk), d1)
            req = {
                "exchange": "NSE",
                "symboltoken": token,
                "interval": interval,
                "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            }
            res = self._retry_call(lambda: self._obj.getCandleData(dict(req)))
            data = (res or {}).get("data") or []
            for row in data:
                if isinstance(row, dict):
                    rows.append((row.get("timestamp"), row.get("open"),
                                 row.get("high"), row.get("low"),
                                 row.get("close"), row.get("volume")))
                elif len(row) >= 6:
                    rows.append(tuple(row[:6]))
            cursor = end + pd.Timedelta(days=1)

        if not rows:
            raise SmartAPIUnavailable(f"no candles returned for {symbol}")
        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low",
                                         "Close", "Volume"])
        df["Date"] = (pd.to_datetime(df["Date"], errors="coerce", utc=False)
                      .dt.tz_localize(None).dt.normalize())
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df = df[~df.index.duplicated(keep="last")]

        # getCandleData returns raw exchange prints: no split/bonus/dividend
        # adjustment, unlike the yfinance fallback (auto_adjust=True). We have
        # no corporate-actions feed yet (that's Phase 4) to adjust this series
        # properly, so we can't correct it here -- but we can refuse to trust
        # it. A split/bonus shows up as a single-bar move landing on a clean
        # fraction (0.5, 1/3, 0.25, 0.2...); that's exactly what
        # detect_unadjusted's suspicious_ratio flags. Restrict to that flag
        # (not every >20% move) so a genuine large news-driven day doesn't
        # needlessly punt a whole symbol to the fallback. Raising
        # SmartAPIUnavailable here is not a new contract: every other failure
        # path in this class already does it so callers fall back to
        # yfinance, which will return a correctly split/dividend-adjusted
        # series for the same symbol -- a correct series beats a fast wrong
        # one.
        flagged = detect_unadjusted(df.rename(columns=str.lower))
        suspicious = flagged[flagged["suspicious_ratio"]] if len(flagged) else flagged
        if len(suspicious):
            detail = suspicious[["gap", "ratio"]].round(3).to_dict("records")
            message = (
                f"{len(suspicious)} unadjusted-looking price jump(s) in "
                f"SmartAPI candles (likely an un-adjusted split/bonus): {detail}"
            )
            log_event(symbol, "unadjusted_split_rejected", message)
            raise SmartAPIUnavailable(f"{symbol}: {message}")
        return df


def get_shared_session():
    """Process-wide singleton session: one login + one scrip-master download
    shared by the price cache and the option-chain scan."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            s = SmartAPISession()
            s._shared = True
            _SESSION = s
        return _SESSION


def close_shared_session():
    """Release the shared session (only when no further calls are needed)."""
    global _SESSION
    with _SESSION_LOCK:
        s = _SESSION
        _SESSION = None
    if s is not None:
        try:
            s._shared = False
            s.close()
        except Exception:
            pass
