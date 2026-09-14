"""NSE public API wrapper.

Two backends:
- requests/curl_cffi : fast, works when NSE doesn't block the network.
- playwright browser  : reliable fallback. NSE's Akamai Bot Manager runs a JS
  challenge and serves empty `{}` / blocks to scripted clients. A real Chrome
  browser (headful) passes the challenge, and the page's own XHR endpoints
  become callable from the page context.

Current NSE equity option-chain endpoint (2026):
    /api/option-chain-contract-info?symbol=X          -> expiry list
    /api/option-chain-v3?type=Equity&symbol=X&expiry=.. -> chain

Responses are cached on disk with a TTL to respect NSE rate limits.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

BASE = "https://www.nseindia.com"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
CACHE_DIR = os.path.abspath(CACHE_DIR)
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/option-chain",
}

MIN_REQUEST_GAP = 3.0  # seconds between API hits (NSE anti-bot)


class NSEUnavailable(RuntimeError):
    pass


class NSESession:
    """NSE API access with cookie handshake, rate limiting and disk cache.

    backend:
        'auto'    -> try requests first; switch to browser on detection of a block.
        'browser' -> always use headful Chrome via Playwright.
    """

    def __init__(self, backend="auto", cache_ttl_minutes=15, min_gap=MIN_REQUEST_GAP,
                 landing_path="/option-chain"):
        """landing_path: the page whose session/cookies this instance primes
        against. Akamai's bot check appears to tie session validity to the
        actual page context that requested it -- a session primed on
        /option-chain doesn't reliably unlock /companies-listing/* endpoints
        (corporate-announcements, corporate-actions, board-meetings) even
        though the same cookies are sent. Callers fetching those should pass
        the matching landing page rather than relying on the option-chain
        default.
        """
        self.backend = backend
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.min_gap = min_gap
        self.landing_path = landing_path
        self._session = None
        self._cookies_ready = False
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._page = None

    # ---- cache ---------------------------------------------------------------

    def _cache_path(self, url):
        key = url.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
        if len(key) > 180:
            key = key[:180]
        return os.path.join(CACHE_DIR, f"nse_{key}.json")

    def _read_cache(self, path):
        try:
            with open(path) as fh:
                payload = json.load(fh)
            stamp = datetime.fromisoformat(payload["_fetched_at"])
            if datetime.now() - stamp <= self.cache_ttl:
                return payload["data"]
        except (OSError, KeyError, ValueError):
            pass
        return None

    def _write_cache(self, path, data):
        try:
            with open(path, "w") as fh:
                json.dump({"_fetched_at": datetime.now().isoformat(), "data": data}, fh)
        except OSError:
            pass

    # ---- requests backend ----------------------------------------------------

    def _ensure_requests(self):
        if self._session is not None:
            return
        try:
            from curl_cffi import requests as cr
            self._session = cr.Session(impersonate="chrome")
            self._session.headers.update(DEFAULT_HEADERS)
        except ImportError:
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
        self._session.headers["Referer"] = BASE + self.landing_path
        self._session.get(BASE + "/", timeout=20)
        self._session.get(BASE + self.landing_path, timeout=20)
        self._cookies_ready = True

    def _requests_json(self, url):
        self._ensure_requests()
        resp = self._session.get(url, timeout=25)
        if resp.status_code != 200:
            raise NSEUnavailable(f"NSE {resp.status_code} for {url}")
        try:
            return resp.json()
        except ValueError:
            raise NSEUnavailable("NSE returned non-JSON (likely blocked)")

    # ---- browser backend -----------------------------------------------------

    def _ensure_browser(self):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise NSEUnavailable("playwright not installed (pip install playwright)")
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(
                channel="chrome", headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            self._pw.stop()
            self._pw = None
            raise NSEUnavailable(f"Cannot launch Chrome via Playwright: {exc}")
        self._page = self._browser.new_page(locale="en-US")
        self._page.goto(BASE + self.landing_path, timeout=90000, wait_until="commit")
        self._page.wait_for_timeout(9000)  # let the Akamai JS challenge settle

    def _browser_json(self, url):
        self._ensure_browser()
        out = self._page.evaluate(
            """({u, referer}) => fetch(u, {
                headers: {
                    'Referer': referer,
                    'Accept': 'application/json'
                }
            }).then(r => r.text()).then(t => {
                try { return JSON.parse(t); }
                catch (e) { return {__html__: t.slice(0, 120)}; }
            })""",
            {"u": url, "referer": BASE + self.landing_path},
        )
        if isinstance(out, dict) and "__html__" in out:
            raise NSEUnavailable(f"NSE blocked browser fetch: {out['__html__']}")
        return out

    # ---- public API ----------------------------------------------------------

    def _rate_limit(self):
        with self._lock:
            elapsed = time.time() - self._last_request
            if elapsed < self.min_gap:
                time.sleep(self.min_gap - elapsed)
            self._last_request = time.time()

    def get_json(self, path, params=None, use_cache=True):
        url = BASE + path
        if params:
            url = url + "?" + urlencode(params)
        cpath = self._cache_path(url)
        if use_cache:
            cached = self._read_cache(cpath)
            if cached is not None:
                return cached

        self._rate_limit()
        data = None
        last_err = None

        if self.backend in ("auto", "requests"):
            try:
                data = self._requests_json(url)
                if not self._looks_blocked(data):
                    self._write_cache(cpath, data)
                    return data
                last_err = "requests backend blocked"
            except (NSEUnavailable, requests.RequestException) as exc:
                last_err = str(exc)

        if self.backend in ("auto", "browser"):
            for attempt in range(2):
                try:
                    data = self._browser_json(url)
                    if not self._looks_blocked(data):
                        self._write_cache(cpath, data)
                        return data
                    last_err = "browser backend blocked"
                except NSEUnavailable as exc:
                    last_err = str(exc)
                # NSE rate-limits transiently: reload the challenge page and pause.
                time.sleep(6 * (attempt + 1))
                try:
                    self._page.goto(BASE + self.landing_path, timeout=60000, wait_until="commit")
                    self._page.wait_for_timeout(5000)
                except Exception:
                    pass

        raise NSEUnavailable(f"NSE data unavailable: {last_err} ({url})")

    @staticmethod
    def _looks_blocked(data):
        if not isinstance(data, dict):
            return True
        if data == {}:
            return True
        return False

    # ---- convenience endpoints -----------------------------------------------

    def option_chain_equity(self, symbol, expiry=None):
        """Full option chain for a stock symbol (nearest expiry by default)."""
        if expiry is None:
            ci = self.get_json("/api/option-chain-contract-info", params={"symbol": symbol})
            exps = (ci or {}).get("expiryDates") or []
            if not exps:
                raise NSEUnavailable(f"No expiry dates for {symbol}")
            expiry = exps[0]
        return self.get_json(
            "/api/option-chain-v3",
            params={"type": "Equity", "symbol": symbol, "expiry": expiry},
        )

    def option_chain_index(self, symbol="NIFTY"):
        ci = self.get_json("/api/option-chain-contract-info", params={"symbol": symbol})
        exps = (ci or {}).get("expiryDates") or []
        if not exps:
            raise NSEUnavailable(f"No expiry dates for {symbol}")
        return self.get_json(
            "/api/option-chain-v3",
            params={"type": "Indices", "symbol": symbol, "expiry": exps[0]},
        )

    def close(self):
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._page = self._browser = self._pw = None
