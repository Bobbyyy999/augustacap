"""Live data providers.

  Yahoo Finance (yfinance, unofficial, no key)  global equities, indices, FX, futures, options, history
  Twelve Data (key)                              real-time US equities, FX, crypto + WebSocket stream
  Massive, formerly Polygon.io (key)             US option chains with vendor greeks
  Finnhub (free key)                             market news, earnings calendar
  FRED (key optional)                            yields, credit spreads, macro series, release dates
  RSS feeds (data/news_feeds.json)               headlines when no news key is set

Every function returns plain dicts/lists in the shapes market.py expects and raises on failure;
market.py decides on fallbacks and caching.
"""
import asyncio
import calendar
import csv
import datetime as dt
import email.utils
import io
import json
import math
import time
import xml.etree.ElementTree as ET

import httpx

from .config import BASE, settings

_client = None


def client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15, follow_redirects=True,
                                    headers={"User-Agent": "AugustaTerminal/1.0 (internal research tool)"})
    return _client


def num(v):
    """float or None (NaN, blanks and junk become None)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def mkquote(symbol, price, prev, source, delayed, **kw):
    q = {"symbol": symbol, "price": price, "prev_close": prev,
         "change": (price - prev) if prev else None,
         "pct": ((price / prev - 1) * 100) if prev else None,
         "open": None, "high": None, "low": None, "volume": None, "bid": None, "ask": None,
         "t": time.time(), "source": source, "delayed": delayed}
    q.update({k: v for k, v in kw.items() if v is not None})
    return q


# ----------------------------------------------------------------------------- Yahoo (blocking)

def _yf():
    import yfinance as yf
    return yf


def yahoo_quotes_sync(symbols):
    import pandas as pd
    df = _yf().download(symbols, period="5d", interval="1d", group_by="ticker", progress=False,
                        threads=True, auto_adjust=False)
    out = {}
    for s in symbols:
        try:
            sub = df[s] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            if sub.empty:
                continue
            last = sub.iloc[-1]
            prev = num(sub.iloc[-2]["Close"]) if len(sub) > 1 else None
            out[s] = mkquote(s, float(last["Close"]), prev, "yahoo", True, open=num(last.get("Open")),
                             high=num(last.get("High")), low=num(last.get("Low")),
                             volume=num(last.get("Volume")), session=str(sub.index[-1].date()))
        except Exception:
            continue
    return out


def yahoo_profile_sync(symbol):
    info = _yf().Ticker(symbol).info or {}
    g = info.get
    return {"name": g("longName") or g("shortName") or symbol, "currency": g("currency"),
            "exchange": g("fullExchangeName") or g("exchange"), "sector": g("sector"),
            "industry": g("industry"), "summary": g("longBusinessSummary"),
            "market_cap": num(g("marketCap")), "pe": num(g("trailingPE")), "forward_pe": num(g("forwardPE")),
            "dividend_yield": num(g("trailingAnnualDividendYield")) or num(g("dividendYield")), "beta": num(g("beta")),
            "year_high": num(g("fiftyTwoWeekHigh")), "year_low": num(g("fiftyTwoWeekLow")),
            "bid": num(g("bid")), "ask": num(g("ask")), "avg_volume": num(g("averageVolume")),
            "eps": num(g("trailingEps")), "shares_out": num(g("sharesOutstanding"))}


def yahoo_history_sync(symbol, period, interval):
    df = _yf().Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    daily = interval in ("1d", "1wk", "1mo")
    bars = []
    for idx, row in df.iterrows():
        c = num(row.get("Close"))
        if c is None:
            continue
        t = calendar.timegm(idx.date().timetuple()) if daily else int(idx.timestamp())
        bars.append({"t": t, "o": num(row.get("Open")) or c, "h": num(row.get("High")) or c,
                     "l": num(row.get("Low")) or c, "c": c, "v": int(num(row.get("Volume")) or 0)})
    return bars


def yahoo_expirations_sync(symbol):
    return list(_yf().Ticker(symbol).options or [])


def yahoo_chain_sync(symbol, expiry):
    ch = _yf().Ticker(symbol).option_chain(expiry)
    rows = []
    for cp, df in (("C", ch.calls), ("P", ch.puts)):
        for _, r in df.iterrows():
            vol, oi = num(r.get("volume")), num(r.get("openInterest"))
            rows.append({"contract": r.get("contractSymbol"), "type": cp, "strike": num(r.get("strike")),
                         "last": num(r.get("lastPrice")), "bid": num(r.get("bid")), "ask": num(r.get("ask")),
                         "volume": int(vol) if vol is not None else None,
                         "oi": int(oi) if oi is not None else None,
                         "iv": num(r.get("impliedVolatility")), "iv_source": "yahoo"})
    return rows


def _iso_to_epoch(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def yahoo_news_sync(symbol):
    out = []
    for it in _yf().Ticker(symbol).news or []:
        c = it.get("content") or it
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link"))
        t = _iso_to_epoch(c.get("pubDate")) if c.get("pubDate") else num(it.get("providerPublishTime"))
        title = c.get("title")
        if title:
            out.append({"title": title, "url": url or "", "t": t or time.time(), "symbol": symbol,
                        "source": (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo"})
    return out


def yahoo_earnings_date_sync(symbol):
    cal = _yf().Ticker(symbol).calendar or {}
    dates = cal.get("Earnings Date") or []
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    return {"symbol": symbol, "date": str(dates[0])[:10] if dates else None,
            "eps_est": num(cal.get("Earnings Average")), "when": ""}


def yahoo_search_sync(query):
    res = _yf().Search(query, max_results=10).quotes or []
    return [{"symbol": r.get("symbol"), "name": r.get("shortname") or r.get("longname") or "",
             "type": r.get("quoteType") or "", "exchange": r.get("exchange") or ""}
            for r in res if r.get("symbol")]


# ----------------------------------------------------------------------------- Twelve Data

TD = "https://api.twelvedata.com"


async def td_quotes(symmap):
    """symmap: {canonical symbol: Twelve Data symbol}."""
    r = await client().get(f"{TD}/quote", params={"symbol": ",".join(symmap.values()),
                                                 "apikey": settings.twelvedata_key})
    j = r.json()
    if len(symmap) == 1:
        j = {next(iter(symmap.values())): j}
    out = {}
    for canon, tds in symmap.items():
        d = j.get(tds) or {}
        price, prev = num(d.get("close")), num(d.get("previous_close"))
        if price is None:
            continue
        out[canon] = mkquote(canon, price, prev, "twelvedata", False, open=num(d.get("open")),
                             high=num(d.get("high")), low=num(d.get("low")), volume=num(d.get("volume")),
                             name=d.get("name"), currency=d.get("currency"),
                             t=num(d.get("last_quote_at")) or time.time())
    return out


async def td_history(tdsym, interval, outputsize):
    r = await client().get(f"{TD}/time_series", params={
        "symbol": tdsym, "interval": interval, "outputsize": outputsize, "order": "ASC",
        "timezone": "UTC", "apikey": settings.twelvedata_key})
    j = r.json()
    if j.get("status") == "error" or "values" not in j:
        raise RuntimeError(j.get("message") or "Twelve Data returned no values")
    bars = []
    for v in j["values"]:
        s = v["datetime"]
        fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
        t = calendar.timegm(dt.datetime.strptime(s, fmt).timetuple())
        c = num(v.get("close"))
        if c is not None:
            bars.append({"t": t, "o": num(v.get("open")) or c, "h": num(v.get("high")) or c,
                         "l": num(v.get("low")) or c, "c": c, "v": int(num(v.get("volume")) or 0)})
    return bars


async def td_stream(get_symbols, on_price, stop):
    """Keeps a WebSocket open and calls on_price(td_symbol, price, epoch) for each tick."""
    import websockets
    url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={settings.twelvedata_key}"
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                backoff = 2
                subscribed = set()
                while not stop.is_set():
                    want = set(get_symbols())
                    new = want - subscribed
                    if new:
                        await ws.send(json.dumps({"action": "subscribe",
                                                  "params": {"symbols": ",".join(sorted(new))}}))
                        subscribed |= new
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"action": "heartbeat"}))
                        continue
                    if msg.get("event") == "price" and num(msg.get("price")) is not None:
                        on_price(msg.get("symbol"), float(msg["price"]), num(msg.get("timestamp")) or time.time())
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ----------------------------------------------------------------------------- Massive (ex-Polygon)

async def massive_chain(symbol, expiry):
    url = f"https://api.massive.com/v3/snapshot/options/{symbol}"
    params = {"expiration_date": expiry, "limit": 250, "apiKey": settings.massive_key}
    rows, spot = [], None
    for _ in range(8):
        r = await client().get(url, params=params)
        j = r.json()
        if r.status_code != 200:
            raise RuntimeError(j.get("error") or j.get("message") or f"Massive HTTP {r.status_code}")
        for it in j.get("results") or []:
            d, g, day = it.get("details") or {}, it.get("greeks") or {}, it.get("day") or {}
            lq, lt = it.get("last_quote") or {}, it.get("last_trade") or {}
            spot = spot or num((it.get("underlying_asset") or {}).get("price"))
            rows.append({"contract": d.get("ticker"), "type": "C" if d.get("contract_type") == "call" else "P",
                         "strike": num(d.get("strike_price")), "last": num(lt.get("price")) or num(day.get("close")),
                         "bid": num(lq.get("bid")), "ask": num(lq.get("ask")),
                         "volume": int(num(day.get("volume")) or 0), "oi": int(num(it.get("open_interest")) or 0),
                         "iv": num(it.get("implied_volatility")), "iv_source": "massive",
                         "delta": num(g.get("delta")), "gamma": num(g.get("gamma")),
                         "theta": num(g.get("theta")), "vega": num(g.get("vega")),
                         "greeks_source": "massive" if g else None})
        nxt = j.get("next_url")
        if not nxt:
            break
        url, params = nxt, {"apiKey": settings.massive_key}
    return rows, spot


# ----------------------------------------------------------------------------- Finnhub

FH = "https://finnhub.io/api/v1"


async def finnhub_news(symbol=None):
    if symbol:
        today = dt.date.today()
        r = await client().get(f"{FH}/company-news", params={
            "symbol": symbol, "from": (today - dt.timedelta(days=10)).isoformat(),
            "to": today.isoformat(), "token": settings.finnhub_key})
    else:
        r = await client().get(f"{FH}/news", params={"category": "general", "token": settings.finnhub_key})
    j = r.json()
    if not isinstance(j, list):
        raise RuntimeError(str(j)[:200])
    return [{"title": it.get("headline"), "url": it.get("url") or "", "source": it.get("source") or "Finnhub",
             "t": num(it.get("datetime")) or time.time(), "symbol": symbol}
            for it in j[:80] if it.get("headline")]


async def finnhub_earnings(start, end):
    r = await client().get(f"{FH}/calendar/earnings", params={
        "from": start.isoformat(), "to": end.isoformat(), "token": settings.finnhub_key})
    j = r.json()
    if "earningsCalendar" not in j:
        raise RuntimeError(str(j)[:200])
    return [{"date": it.get("date"), "symbol": it.get("symbol"), "when": it.get("hour") or "",
             "eps_est": num(it.get("epsEstimate")), "eps_actual": num(it.get("epsActual")),
             "rev_est": num(it.get("revenueEstimate"))}
            for it in j["earningsCalendar"] if it.get("symbol") and it.get("date")]


# ----------------------------------------------------------------------------- FRED

async def fred_series(series, start="2000-01-01"):
    """Returns [(YYYY-MM-DD, value), ...] oldest first."""
    if settings.fred_key:
        r = await client().get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": series, "api_key": settings.fred_key, "file_type": "json",
            "observation_start": start})
        j = r.json()
        if "observations" not in j:
            raise RuntimeError(j.get("error_message") or f"FRED HTTP {r.status_code}")
        obs = [(o["date"], num(o["value"])) for o in j["observations"]]
    else:
        r = await client().get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                               params={"id": series, "cosd": start})
        if r.status_code != 200 or "," not in r.text[:200]:
            raise RuntimeError(f"FRED series {series} not available (HTTP {r.status_code})")
        rows = list(csv.reader(io.StringIO(r.text)))[1:]
        obs = [(row[0], num(row[1])) for row in rows if len(row) >= 2]
    obs = [(d, v) for d, v in obs if v is not None]
    if not obs:
        raise RuntimeError(f"FRED series {series} has no observations")
    return obs


async def fred_release_dates(start, end, names):
    r = await client().get("https://api.stlouisfed.org/fred/releases/dates", params={
        "api_key": settings.fred_key, "file_type": "json", "realtime_start": start.isoformat(),
        "realtime_end": end.isoformat(), "include_release_dates_with_no_data": "true",
        "sort_order": "asc", "limit": 1000})
    j = r.json()
    wanted = {n.lower() for n in names}
    return [{"date": it["date"], "title": it["release_name"]}
            for it in j.get("release_dates", []) if it.get("release_name", "").lower() in wanted]


# ----------------------------------------------------------------------------- RSS headlines

def load_feeds():
    try:
        return json.loads((BASE / "data" / "news_feeds.json").read_text())
    except Exception:
        return []


def parse_feed(xml_bytes, source):
    if b"<!ENTITY" in xml_bytes or len(xml_bytes) > 3_000_000:
        return []
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = None
        for ch in el:
            ct = ch.tag.split("}")[-1]
            if ct == "title":
                title = (ch.text or "").strip()
            elif ct == "link":
                link = (ch.text or "").strip() or ch.attrib.get("href") or link
            elif ct in ("pubDate", "published", "updated", "date"):
                date = (ch.text or "").strip()
        t = None
        if date:
            try:
                t = email.utils.parsedate_to_datetime(date).timestamp()
            except (TypeError, ValueError):
                t = _iso_to_epoch(date)
        if title:
            out.append({"title": title, "url": link or "", "source": source, "t": t or time.time(),
                        "symbol": None})
    return out


async def rss_news():
    async def one(feed):
        try:
            r = await client().get(feed["url"])
            return parse_feed(r.content, feed.get("name") or "RSS")[:25]
        except Exception:
            return []
    results = await asyncio.gather(*(one(f) for f in load_feeds()))
    return [item for sub in results for item in sub]
