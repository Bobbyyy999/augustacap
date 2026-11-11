"""Simulated market data for DATA_MODE=demo and for automated tests.

Everything here is made up. Prices are deterministic per symbol and drift smoothly with the
clock so screens and alarms behave as they would on a live feed. Never trade off demo mode.
"""
import calendar
import datetime as dt
import hashlib
import math
import random
import time
from functools import lru_cache

from . import greeks as G
from .universe import EARNINGS_FALLBACK, SCREENS

ANCHORS = {
    "^GSPC": 6900, "^DJI": 48500, "^IXIC": 23500, "^RUT": 2500, "^VIX": 17, "^GSPTSE": 30500,
    "^BVSP": 152000, "^MXX": 62500, "^STOXX50E": 5700, "^FTSE": 9700, "^GDAXI": 24200, "^FCHI": 8150,
    "^IBEX": 16200, "FTSEMIB.MI": 43500, "^SSMI": 12600, "^N225": 51000, "^HSI": 26200,
    "000001.SS": 3980, "^KS11": 4100, "^TWII": 27900, "^NSEI": 25800, "^BSESN": 84300, "^AXJO": 8900,
    "^STI": 4450, "^JKSE": 8300,
    "EURUSD=X": 1.158, "GBPUSD=X": 1.318, "USDJPY=X": 153.6, "USDCHF=X": 0.804, "AUDUSD=X": 0.652,
    "NZDUSD=X": 0.565, "USDCAD=X": 1.402, "USDSEK=X": 9.52, "USDNOK=X": 10.15, "EURGBP=X": 0.878,
    "EURJPY=X": 177.9, "DX-Y.NYB": 99.6, "USDCNY=X": 7.12, "USDINR=X": 88.6, "USDBRL=X": 5.34,
    "USDMXN=X": 18.4, "USDZAR=X": 17.2, "USDTRY=X": 42.2, "USDKRW=X": 1455, "USDIDR=X": 16700,
    "USDPLN=X": 3.66, "USDTHB=X": 32.4, "BTC-USD": 104000, "ETH-USD": 3500, "SOL-USD": 162,
    "ES=F": 6920, "NQ=F": 25600, "YM=F": 48600, "RTY=F": 2510, "ZT=F": 104.2, "ZF=F": 109.3,
    "ZN=F": 112.8, "ZB=F": 117.4, "6E=F": 1.16, "6J=F": 0.00652, "6B=F": 1.318, "6A=F": 0.652,
    "CL=F": 60.4, "BZ=F": 64.3, "NG=F": 4.35, "RB=F": 1.93, "HO=F": 2.48, "GC=F": 4120, "SI=F": 50.6,
    "PL=F": 1590, "PA=F": 1420, "HG=F": 5.05, "ALI=F": 2870, "ZC=F": 432, "ZW=F": 536, "ZS=F": 1125,
    "KC=F": 402, "SB=F": 14.3, "CT=F": 64.1, "CC=F": 6100, "LE=F": 228,
    "SHY": 82.9, "IEF": 96.8, "TLT": 90.1, "TIP": 110.9, "BWX": 23.1, "LQD": 111.4, "HYG": 80.6,
    "JNK": 97.2, "BKLN": 20.9, "EMB": 96.1, "EMLC": 25.6, "CEMB": 46.2, "HYEM": 20.1,
    "AAPL": 271, "MSFT": 506, "NVDA": 193, "SPY": 688, "GOOGL": 291, "AMZN": 248, "META": 628,
    "TSLA": 440, "JPM": 315, "QQQ": 620,
}

FRED_ANCHORS = {
    "DGS1MO": 3.95, "DGS3MO": 3.88, "DGS6MO": 3.78, "DGS1": 3.66, "DGS2": 3.58, "DGS3": 3.6,
    "DGS5": 3.72, "DGS7": 3.9, "DGS10": 4.1, "DGS20": 4.66, "DGS30": 4.69,
    "IRLTLT01DEM156N": 2.66, "IRLTLT01GBM156N": 4.45, "IRLTLT01JPM156N": 1.68, "IRLTLT01FRM156N": 3.44,
    "IRLTLT01ITM156N": 3.4, "IRLTLT01CAM156N": 3.15, "IRLTLT01MXM156N": 8.7, "IRLTLT01ZAM156N": 8.9,
    "IRLTLT01KRM156N": 3.1, "INDIRLTLT01STM": 6.5, "IRLTLT01PLM156N": 5.3, "IRLTLT01HUM156N": 6.9,
    "IRLTLT01CLM156N": 5.5, "IRLTLT01COM156N": 11.8, "IRLTLT01ILM156N": 4.0,
    "DFEDTARU": 4.0, "SOFR": 3.92, "ECBDFR": 2.0, "DFF": 3.87,
    "BAMLC0A0CM": 0.82, "BAMLC0A1CAAA": 0.34, "BAMLC0A2CAA": 0.47, "BAMLC0A3CA": 0.68,
    "BAMLC0A4CBBB": 1.02, "BAMLH0A0HYM2": 2.95, "BAMLH0A1HYBB": 1.75, "BAMLH0A2HYB": 3.05,
    "BAMLH0A3HYC": 9.1, "BAMLHE00EHYIOAS": 2.85, "BAMLEMCBPIOAS": 1.8, "BAMLC0A0CMEY": 4.85,
    "BAMLH0A0HYM2EY": 6.85,
}


def _seed(key):
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def _vol(symbol):
    if symbol.endswith("=X") or symbol == "DX-Y.NYB":
        return 0.005
    if symbol in ("^VIX",):
        return 0.05
    if symbol.endswith("-USD"):
        return 0.03
    if symbol.endswith("=F"):
        return 0.012
    if symbol.startswith("^"):
        return 0.009
    return 0.016


def base(symbol):
    if symbol in ANCHORS:
        return float(ANCHORS[symbol])
    return 20 + (_seed(symbol) % 3800) / 10.0


def _business_days_before(today, n):
    days, d = [], today
    while len(days) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    return days[::-1]


@lru_cache(maxsize=512)
def _daily(symbol, today_ord, n=1300):
    """Daily bars for the n business days before `today`; the final close equals the anchor."""
    today = dt.date.fromordinal(today_ord)
    rng = random.Random(_seed(symbol))
    vol = _vol(symbol)
    days = _business_days_before(today, n)
    rets = [rng.gauss(0.0003, vol) for _ in days]
    closes = [0.0] * n
    closes[-1] = base(symbol)
    for i in range(n - 2, -1, -1):
        closes[i] = closes[i + 1] / math.exp(rets[i + 1])
    avg_volume = 2e5 + _seed(symbol + "v") % 40_000_000
    bars = []
    for i, d in enumerate(days):
        c = closes[i]
        o = closes[i - 1] * (1 + rng.gauss(0, vol / 4)) if i else c
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append({"t": calendar.timegm(d.timetuple()), "o": o, "h": hi, "l": lo, "c": c,
                     "v": int(avg_volume * (0.6 + rng.random() * 0.9))})
    return bars


def _intraday_factor(symbol, t):
    s = _seed(symbol)
    p1, p2, p3 = (s % 628) / 100.0, (s // 7 % 628) / 100.0, (s // 53 % 628) / 100.0
    v = _vol(symbol) / 0.016
    return v * (0.010 * math.sin(2 * math.pi * t / 43200 + p1)
                + 0.003 * math.sin(t / 300.0 + p2) + 0.0006 * math.sin(t / 11.0 + p3))


def _day_start(t):
    return t - (t % 86400)


def quote(symbol, now=None):
    now = now or time.time()
    daily = _daily(symbol, dt.datetime.fromtimestamp(now, dt.timezone.utc).date().toordinal())
    prev = daily[-1]["c"]
    start = _day_start(now)
    price = prev * (1 + _intraday_factor(symbol, now))
    samples = [prev * (1 + _intraday_factor(symbol, ts)) for ts in range(int(start), int(now) + 1, 300)] or [price]
    samples.append(price)
    frac = (now - start) / 86400.0
    avg_volume = sum(b["v"] for b in daily[-20:]) / 20
    no_volume = symbol.endswith("=X") or symbol.startswith("^")
    return {
        "symbol": symbol, "price": price, "prev_close": prev, "change": price - prev,
        "pct": (price / prev - 1) * 100, "open": samples[0], "high": max(samples), "low": min(samples),
        "volume": None if no_volume else int(avg_volume * frac * (1 + 0.6 * math.sin(_seed(symbol) % 7))),
        "bid": price * 0.9999, "ask": price * 1.0001,
        "t": now, "source": "demo", "delayed": False,
    }


def history(symbol, intraday, step, days):
    """intraday=True: `step`-second bars covering `days` days. Otherwise daily bars."""
    now = time.time()
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    daily = _daily(symbol, today.toordinal())
    if not intraday:
        bars = [dict(b) for b in daily[-days:]] if days else [dict(b) for b in daily]
        qt = quote(symbol, now)
        bars.append({"t": calendar.timegm(today.timetuple()), "o": qt["open"], "h": qt["high"],
                     "l": qt["low"], "c": qt["price"], "v": qt["volume"] or 0})
        return bars
    bars = []
    rng = random.Random(_seed(symbol + "iv"))
    per_bar = sum(b["v"] for b in daily[-20:]) / 20 * step / 86400.0
    t = int(now - days * 86400)
    t -= t % step
    while t <= now:
        day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date()
        prev = next((b["c"] for b in reversed(daily) if b["t"] < calendar.timegm(day.timetuple())), daily[-1]["c"])
        o = prev * (1 + _intraday_factor(symbol, t))
        c = prev * (1 + _intraday_factor(symbol, min(t + step, now)))
        wiggle = abs(o - c) * 0.3 + prev * 0.0002
        bars.append({"t": t, "o": o, "h": max(o, c) + wiggle, "l": min(o, c) - wiggle, "c": c,
                     "v": int(per_bar * (0.4 + rng.random() * 1.2))})
        t += step
    return bars


def profile(symbol):
    return {"name": f"{symbol} (simulated)", "currency": "USD", "exchange": "DEMO",
            "sector": None, "industry": None, "summary": "Simulated instrument for demo mode.",
            "market_cap": None, "pe": None, "forward_pe": None, "dividend_yield": 0.0, "beta": None,
            "year_high": max(b["h"] for b in history(symbol, False, 0, 252)),
            "year_low": min(b["l"] for b in history(symbol, False, 0, 252))}


def expirations(symbol):
    today = dt.date.today()
    out, d = [], today + dt.timedelta(days=1)
    while len(out) < 6:
        if d.weekday() == 4:
            out.append(d)
        d += dt.timedelta(days=1)
    month = today.replace(day=1)
    for _ in range(8):
        month = (month + dt.timedelta(days=32)).replace(day=1)
        fridays = [month + dt.timedelta(days=i) for i in range(31)
                   if (month + dt.timedelta(days=i)).month == month.month
                   and (month + dt.timedelta(days=i)).weekday() == 4]
        if fridays[2] not in out and fridays[2] > out[-1]:
            out.append(fridays[2])
        if len(out) >= 10:
            break
    return [d.isoformat() for d in sorted(out)]


def chain(symbol, expiry):
    """Raw rows (no greeks); market.py adds greeks the same way it does for live chains."""
    S = quote(symbol)["price"]
    step = next(s for s in (0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 500, 1000) if S / s <= 120)
    centre = round(S / step) * step
    strikes = [centre + i * step for i in range(-12, 13) if centre + i * step > 0]
    exp = dt.date.fromisoformat(expiry)
    T = max((exp - dt.date.today()).days + 0.6, 0.6) / 365.0
    rng = random.Random(_seed(symbol + expiry))
    base_iv = 0.18 + (_seed(symbol) % 30) / 100.0
    rows = []
    for K in strikes:
        m = math.log(K / S)
        iv = base_iv + 0.9 * m * m - 0.25 * m
        for cp in ("C", "P"):
            mid = G.bs_price(S, K, T, 0.039, 0.005, iv, cp)
            spread = max(0.01, mid * 0.02)
            rows.append({"contract": f"{symbol}{exp:%y%m%d}{cp}{int(round(K * 1000)):08d}", "type": cp,
                         "strike": K, "last": round(mid, 2), "bid": round(max(mid - spread / 2, 0), 2),
                         "ask": round(mid + spread / 2, 2), "volume": int(rng.random() ** 3 * 9000),
                         "oi": int(rng.random() ** 2 * 40000), "iv": None})
    return rows


def _is_monthly(series):
    return series.endswith("M156N") or series.endswith("STM")


@lru_cache(maxsize=256)
def _fred(series, today_ord):
    today = dt.date.fromordinal(today_ord)
    level = FRED_ANCHORS.get(series, 2 + (_seed(series) % 400) / 100.0)
    # Treasury tenors share one random path so the simulated curve moves roughly in parallel.
    rng = random.Random(_seed("DGS" if series.startswith("DGS") else series))
    if _is_monthly(series):
        dates, d = [], today.replace(day=1)
        for _ in range(120):
            d = (d - dt.timedelta(days=1)).replace(day=1)
            dates.append(d)
        dates = dates[::-1]
        sd = 0.09
    else:
        dates = _business_days_before(today, 1300)
        sd = 0.035
    vals = [0.0] * len(dates)
    vals[-1] = level
    for i in range(len(dates) - 2, -1, -1):
        vals[i] = max(0.02, vals[i + 1] - rng.gauss(0, sd) * max(0.3, level / 4))
    return [(d.isoformat(), round(v, 2)) for d, v in zip(dates, vals)]


def fred(series):
    return list(_fred(series, dt.date.today().toordinal()))


def news(symbol=None):
    now = time.time()
    topics = ["Treasury yields drift as traders weigh the next policy meeting",
              "Dollar steadies against majors ahead of inflation data",
              "Oil holds range while inventories build", "Gold consolidates near recent highs",
              "Credit spreads little changed in quiet session", "Chipmakers lead equity gains",
              "Emerging-market currencies firm on carry demand", "Earnings season enters final stretch"]
    out = []
    for i, title in enumerate(topics):
        head = f"{symbol}: {title}" if symbol else title
        out.append({"title": "[DEMO] " + head, "url": "", "source": "Simulated wire",
                    "t": now - i * 1500 - 120, "symbol": symbol})
    return out


def earnings(start, end):
    out = []
    span = max((end - start).days, 1)
    for sym in EARNINGS_FALLBACK:
        d = start + dt.timedelta(days=_seed(sym + "e") % span)
        if d.weekday() >= 5:
            d += dt.timedelta(days=2)
        if d <= end:
            out.append({"date": d.isoformat(), "symbol": sym, "when": "amc" if _seed(sym) % 2 else "bmo",
                        "eps_est": round(0.5 + (_seed(sym) % 500) / 100, 2)})
    return out


def search(query):
    q = query.upper()
    out = []
    for scr in SCREENS.values():
        for _, rows in scr["groups"]:
            for sym, name in rows:
                if q in sym.upper() or q in name.upper():
                    out.append({"symbol": sym, "name": name, "type": "demo"})
    for sym in ANCHORS:
        if q in sym and not any(o["symbol"] == sym for o in out):
            out.append({"symbol": sym, "name": sym, "type": "demo"})
    return out[:10]
