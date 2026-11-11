"""Market data service: picks a provider per instrument, caches, and falls back when one fails."""
import asyncio
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

from . import greeks as G
from . import providers as P
from . import synthetic as S
from .config import settings
from .universe import EARNINGS_FALLBACK, MACRO_RELEASES, label_for

NY = ZoneInfo("America/New_York")

RANGES = {
    "1D": {"intraday": True, "step": 300, "days": 1, "yp": "1d", "yi": "5m", "tdi": "5min", "tdn": 120},
    "5D": {"intraday": True, "step": 900, "days": 5, "yp": "5d", "yi": "15m", "tdi": "15min", "tdn": 500},
    "1M": {"days": 22, "yp": "1mo"}, "3M": {"days": 66, "yp": "3mo"}, "6M": {"days": 130, "yp": "6mo"},
    "YTD": {"days": None, "yp": "ytd"}, "1Y": {"days": 252, "yp": "1y"},
    "5Y": {"days": 1260, "yp": "5y"}, "MAX": {"days": 0, "yp": "max"},
}

_cache = {}
_yahoo_gate = asyncio.Semaphore(3)
status = {}          # provider -> {"ok": bool, "t": epoch, "error": str}
_stream_prices = {}  # Twelve Data symbol -> (price, epoch)
_stream_wanted = {}  # Twelve Data symbol -> last time a screen asked for it


def _get(key):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _put(key, value, ttl):
    if len(_cache) > 5000:
        now = time.time()
        for k in [k for k, v in _cache.items() if v[0] < now]:
            _cache.pop(k, None)
    _cache[key] = (time.time() + ttl, value)
    return value


def _mark(provider, error=None):
    status[provider] = {"ok": error is None, "t": time.time(), "error": str(error)[:300] if error else ""}


async def _thread(fn, *args):
    async with _yahoo_gate:
        return await asyncio.to_thread(fn, *args)


def kind(symbol):
    if symbol.startswith("FRED:"):
        return "fred"
    if symbol.endswith("=X"):
        return "fx"
    if symbol.endswith("=F"):
        return "future"
    if symbol.startswith("^"):
        return "index"
    if re.fullmatch(r"[A-Z0-9]+-USD", symbol):
        return "crypto"
    if "." in symbol:
        return "intl"
    return "us_equity"


def td_symbol(symbol):
    """Twelve Data notation for symbols it serves in real time on entry plans, else None."""
    k = kind(symbol)
    if k == "us_equity":
        return symbol.replace("-", ".")
    if k == "fx":
        pair = symbol[:-2]
        if len(pair) == 3:
            pair = "USD" + pair
        return f"{pair[:3]}/{pair[3:]}" if len(pair) == 6 else None
    if k == "crypto":
        return symbol.replace("-", "/")
    return None


def valid_symbol(symbol):
    return bool(re.fullmatch(r"[A-Za-z0-9^=.\-:]{1,24}", symbol or ""))


# ----------------------------------------------------------------------------- quotes

async def _fred_quote(symbol):
    obs = await get_fred(symbol[5:])
    last, prev = obs[-1], (obs[-2] if len(obs) > 1 else None)
    return P.mkquote(symbol, last[1], prev[1] if prev else None, "demo" if settings.demo else "fred",
                     not settings.demo, session=last[0])


async def get_quotes(symbols):
    symbols = [s for s in dict.fromkeys(symbols) if valid_symbol(s)]
    out, missing = {}, []
    for s in symbols:
        hit = _get(("q", s))
        if hit:
            out[s] = hit
        else:
            missing.append(s)
    fred = [s for s in missing if kind(s) == "fred"]
    rest = [s for s in missing if kind(s) != "fred"]
    for s in fred:
        try:
            out[s] = _put(("q", s), await _fred_quote(s), 60 if settings.demo else 900)
        except Exception as e:
            _mark("fred", e)
    if rest and settings.demo:
        for s in rest:
            out[s] = _put(("q", s), S.quote(s), 1)
        rest = []
    if rest and settings.twelvedata_key:
        symmap = {s: td_symbol(s) for s in rest if td_symbol(s)}
        got = {}
        for i in range(0, len(symmap), 60):
            chunk = dict(list(symmap.items())[i:i + 60])
            try:
                got.update(await P.td_quotes(chunk))
                _mark("twelvedata")
            except Exception as e:
                _mark("twelvedata", e)
        now = time.time()
        for s, quote in got.items():
            _stream_wanted[symmap[s]] = now
            out[s] = _put(("q", s), quote, settings.quote_ttl)
        rest = [s for s in rest if s not in got]
    if rest:
        try:
            got = await _thread(P.yahoo_quotes_sync, rest)
            _mark("yahoo")
        except Exception as e:
            got = {}
            _mark("yahoo", e)
        for s, quote in got.items():
            out[s] = _put(("q", s), quote, max(settings.quote_ttl, 15))
    # Overlay fresher streamed ticks.
    now = time.time()
    for s, quote in list(out.items()):
        tds = td_symbol(s) if quote.get("source") == "twelvedata" else None
        tick = _stream_prices.get(tds) if tds else None
        if tick and now - tick[1] < 120 and tick[1] >= quote["t"]:
            quote = dict(quote, price=tick[0], t=tick[1], source="twelvedata-stream")
            if quote.get("prev_close"):
                quote["change"] = tick[0] - quote["prev_close"]
                quote["pct"] = (tick[0] / quote["prev_close"] - 1) * 100
            out[s] = quote
    for s, quote in out.items():
        if not quote.get("name"):
            name = label_for(s)
            if name:
                quote["name"] = name
    return out


async def get_quote(symbol):
    return (await get_quotes([symbol])).get(symbol)


async def get_profile(symbol):
    if kind(symbol) == "fred":
        return {"name": f"FRED series {symbol[5:]}", "exchange": "FRED", "currency": "%"}
    hit = _get(("p", symbol))
    if hit:
        return hit
    if settings.demo:
        return _put(("p", symbol), S.profile(symbol), 300)
    try:
        return _put(("p", symbol), await _thread(P.yahoo_profile_sync, symbol), 300)
    except Exception as e:
        _mark("yahoo", e)
        return {"name": label_for(symbol) or symbol}


# ----------------------------------------------------------------------------- history

async def get_history(symbol, rng="1Y"):
    """Returns {"bars": [...], "intraday": bool, "source": str}."""
    spec = RANGES.get(rng) or RANGES["1Y"]
    intraday = bool(spec.get("intraday"))
    key = ("h", symbol, rng)
    hit = _get(key)
    if hit:
        return hit
    if kind(symbol) == "fred":
        obs = await get_fred(symbol[5:])
        days = spec.get("days")
        if rng == "YTD":
            obs = [o for o in obs if o[0] >= f"{dt.date.today().year}-01-01"]
        elif days and not intraday:
            cutoff = (dt.date.today() - dt.timedelta(days=int(days * 1.46))).isoformat()
            obs = [o for o in obs if o[0] >= cutoff]
        bars = [{"t": int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()),
                 "o": v, "h": v, "l": v, "c": v, "v": 0} for d, v in obs]
        return _put(key, {"bars": bars, "intraday": False, "source": "demo" if settings.demo else "fred"}, 600)
    if settings.demo:
        days = spec.get("days")
        if rng == "YTD":
            today = dt.date.today()
            days = max(1, sum(1 for i in range((today - dt.date(today.year, 1, 1)).days)
                              if (dt.date(today.year, 1, 1) + dt.timedelta(days=i)).weekday() < 5))
        bars = S.history(symbol, intraday, spec.get("step", 0), days)
        return _put(key, {"bars": bars, "intraday": intraday, "source": "demo"}, 2 if intraday else 30)
    tds = td_symbol(symbol)
    if intraday and settings.twelvedata_key and tds:
        try:
            bars = await P.td_history(tds, spec["tdi"], spec["tdn"])
            _mark("twelvedata")
            return _put(key, {"bars": bars, "intraday": True, "source": "twelvedata"}, 20)
        except Exception as e:
            _mark("twelvedata", e)
    try:
        bars = await _thread(P.yahoo_history_sync, symbol, spec["yp"], spec.get("yi", "1d"))
        _mark("yahoo")
    except Exception as e:
        _mark("yahoo", e)
        raise
    return _put(key, {"bars": bars, "intraday": intraday, "source": "yahoo"}, 30 if intraday else 600)


async def session_vwap(symbol):
    """Volume-weighted average price of the latest session, from 5-minute bars."""
    bars = (await get_history(symbol, "1D"))["bars"]
    if not bars:
        return None
    day = bars[-1]["t"] // 86400
    pv = vol = 0.0
    for b in bars:
        if b["t"] // 86400 == day and b["v"]:
            pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
            vol += b["v"]
    return pv / vol if vol else None


# ----------------------------------------------------------------------------- FRED

async def get_fred(series):
    series = series.upper()
    if not re.fullmatch(r"[A-Z0-9_]{1,40}", series):
        raise ValueError("Bad FRED series id")
    key = ("f", series)
    hit = _get(key)
    if hit:
        return hit
    if settings.demo:
        return _put(key, S.fred(series), 300)
    try:
        obs = await P.fred_series(series)
        _mark("fred")
    except Exception as e:
        _mark("fred", e)
        raise
    return _put(key, obs, 1800)


async def fred_snapshot(rows):
    """rows: [(label, series, ...)] -> list with last value and changes; failures are kept as errors."""
    async def one(row):
        label, series = row[0], row[1]
        base = {"label": label, "series": series, "freq": row[2] if len(row) > 2 else "daily"}
        try:
            obs = await get_fred(series)
        except Exception as e:
            return dict(base, error=str(e)[:120])
        last_d, last = obs[-1]

        def ago(days):
            cutoff = (dt.date.fromisoformat(last_d) - dt.timedelta(days=days)).isoformat()
            prior = [v for d, v in obs if d <= cutoff]
            return prior[-1] if prior else None
        year = [v for d, v in obs[-260:]] if base["freq"] == "daily" else [v for d, v in obs[-12:]]
        return dict(base, date=last_d, value=last, prev=obs[-2][1] if len(obs) > 1 else None,
                    m1=ago(30), y1=ago(365), hi=max(year), lo=min(year))
    return await asyncio.gather(*(one(r) for r in rows))


# ----------------------------------------------------------------------------- options

def _years_to_expiry(expiry):
    exp = dt.datetime.combine(dt.date.fromisoformat(expiry), dt.time(16, 0), tzinfo=NY)
    secs = (exp - dt.datetime.now(NY)).total_seconds()
    return max(secs, 3600) / (365.0 * 86400)


async def risk_free():
    try:
        return (await get_fred("DGS3MO"))[-1][1] / 100.0
    except Exception:
        return 0.04


async def get_expirations(symbol):
    key = ("oe", symbol)
    hit = _get(key)
    if hit:
        return hit
    if settings.demo:
        return _put(key, S.expirations(symbol), 600)
    try:
        exps = await _thread(P.yahoo_expirations_sync, symbol)
        _mark("yahoo")
    except Exception as e:
        _mark("yahoo", e)
        raise
    return _put(key, exps, 1800)


async def get_chain(symbol, expiry):
    dt.date.fromisoformat(expiry)
    key = ("oc", symbol, expiry)
    hit = _get(key)
    if hit:
        return hit
    quote = await get_quote(symbol)
    spot = quote["price"] if quote else None
    source = "demo"
    if settings.demo:
        rows = S.chain(symbol, expiry)
    else:
        rows = None
        if settings.massive_key:
            try:
                rows, vendor_spot = await P.massive_chain(symbol, expiry)
                spot = spot or vendor_spot
                source = "massive"
                _mark("massive")
            except Exception as e:
                _mark("massive", e)
                rows = None
        if not rows:
            rows = await _thread(P.yahoo_chain_sync, symbol, expiry)
            source = "yahoo"
            _mark("yahoo")
    if not spot:
        raise RuntimeError("No underlying price available")
    profile = await get_profile(symbol)
    q = profile.get("dividend_yield") or 0.0
    if q > 0.2:      # some feeds report percent rather than a fraction
        q /= 100.0
    r = await risk_free()
    T = _years_to_expiry(expiry)
    for row in rows:
        bid, ask, last = row.get("bid"), row.get("ask"), row.get("last")
        row["mid"] = (bid + ask) / 2 if bid and ask and ask >= bid else last
        iv = row.get("iv")
        if (iv is None or iv < 0.01) and row["mid"] and row.get("strike"):
            iv = G.implied_vol(row["mid"], spot, row["strike"], T, r, q, row["type"])
            row["iv_source"] = "model"
        row["iv"] = iv
        if row.get("delta") is None and iv and row.get("strike"):
            row.update(G.greeks(spot, row["strike"], T, r, q, iv, row["type"]))
            row["greeks_source"] = "model"
        for name in ("delta", "gamma", "theta", "vega"):
            row.setdefault(name, None)      # no solvable IV (e.g. zero bid): leave greeks blank
    rows.sort(key=lambda x: (x.get("strike") or 0, x["type"]))
    data = {"symbol": symbol, "expiry": expiry, "spot": spot, "rate": r, "div_yield": q,
            "years": T, "source": source, "rows": rows, "t": time.time()}
    return _put(key, data, 3 if settings.demo else 20)


# ----------------------------------------------------------------------------- news, calendar, search

async def get_news(symbol=None):
    key = ("n", symbol or "")
    hit = _get(key)
    if hit is not None:
        return hit
    if settings.demo:
        return _put(key, S.news(symbol), 60)
    items = []
    if settings.finnhub_key and (not symbol or kind(symbol) == "us_equity"):
        try:
            items = await P.finnhub_news(symbol)
            _mark("finnhub")
        except Exception as e:
            _mark("finnhub", e)
    if not items and symbol:
        try:
            items = await _thread(P.yahoo_news_sync, symbol)
        except Exception as e:
            _mark("yahoo", e)
    if not symbol:
        try:
            items = items + await P.rss_news()
            _mark("rss")
        except Exception as e:
            _mark("rss", e)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -(x.get("t") or 0)):
        k = (it.get("title") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return _put(key, out[:120], 120)


async def get_earnings(start, end, extra_symbols=()):
    key = ("e", start.isoformat(), end.isoformat(), tuple(sorted(extra_symbols)))
    hit = _get(key)
    if hit is not None:
        return hit
    if settings.demo:
        return _put(key, S.earnings(start, end), 600)
    if settings.finnhub_key:
        try:
            rows = await P.finnhub_earnings(start, end)
            _mark("finnhub")
            return _put(key, rows, 3600)
        except Exception as e:
            _mark("finnhub", e)
    symbols = [s for s in dict.fromkeys(list(extra_symbols) + EARNINGS_FALLBACK) if kind(s) == "us_equity"][:40]

    async def one(sym):
        try:
            return await _thread(P.yahoo_earnings_date_sync, sym)
        except Exception:
            return None
    rows = [r for r in await asyncio.gather(*(one(s) for s in symbols))
            if r and r.get("date") and start.isoformat() <= r["date"] <= end.isoformat()]
    return _put(key, rows, 3600)


async def get_macro_releases(start, end):
    if settings.demo or not settings.fred_key:
        return []
    key = ("m", start.isoformat(), end.isoformat())
    hit = _get(key)
    if hit is not None:
        return hit
    try:
        return _put(key, await P.fred_release_dates(start, end, MACRO_RELEASES), 6 * 3600)
    except Exception as e:
        _mark("fred", e)
        return []


async def search(query):
    query = (query or "").strip()[:40]
    if not query:
        return []
    key = ("s", query.lower())
    hit = _get(key)
    if hit is not None:
        return hit
    if settings.demo:
        return _put(key, S.search(query), 300)
    try:
        return _put(key, await _thread(P.yahoo_search_sync, query), 600)
    except Exception as e:
        _mark("yahoo", e)
        return []


# ----------------------------------------------------------------------------- stream + status

def stream_symbols():
    cutoff = time.time() - 600
    live = sorted((t, s) for s, t in _stream_wanted.items() if t > cutoff)
    return [s for _, s in live[-100:]]


def on_stream_price(td_sym, price, t):
    _stream_prices[td_sym] = (price, t)


def provider_summary():
    d = settings.demo
    return {
        "mode": settings.data_mode,
        "providers": [
            {"name": "Twelve Data", "configured": bool(settings.twelvedata_key), "used_for":
             "Real-time US equities, FX and crypto quotes; intraday bars", **status.get("twelvedata", {})},
            {"name": "Twelve Data stream", "configured": settings.twelvedata_stream and bool(settings.twelvedata_key),
             "used_for": "Tick-by-tick price updates over WebSocket"},
            {"name": "Massive (ex-Polygon)", "configured": bool(settings.massive_key), "used_for":
             "Option chains with vendor greeks", **status.get("massive", {})},
            {"name": "Finnhub", "configured": bool(settings.finnhub_key), "used_for":
             "Market news, earnings calendar", **status.get("finnhub", {})},
            {"name": "FRED", "configured": True, "keyed": bool(settings.fred_key), "used_for":
             "Government yields, credit spreads, macro series", **status.get("fred", {})},
            {"name": "Yahoo Finance", "configured": not d, "used_for":
             "Fallback for everything else (delayed): global equities, indices, futures, options, history",
             **status.get("yahoo", {})},
        ],
    }
