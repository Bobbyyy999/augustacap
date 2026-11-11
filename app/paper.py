"""Paper trading: every account starts with $1,000,000 of pretend capital.

Accounting model
  Cash products (stocks, ETFs, crypto, options, bonds): buying spends cash, selling raises it.
      Selling more than you hold opens a short; the proceeds sit in cash and the position carries
      a negative market value.
  Margin products (futures, spot FX): no cash moves when a position opens. Profit and loss is
      marked to market in the account's equity and moves into cash when the position closes.
  Equity (NAV) = cash + market value of cash products + open P&L of margin products.

Everything is valued in US dollars. Instruments quoted in another currency are converted at the
current FX rate. Dividends, coupons paid, stock splits, borrow fees and interest on cash are not
modelled.
"""
import asyncio
import datetime as dt
import json
import logging
import math
import statistics
import time
from zoneinfo import ZoneInfo

from . import db, market
from .config import env, settings
from .universe import US_CURVE

log = logging.getLogger("augusta.paper")
NY = ZoneInfo("America/New_York")


def _f(key, default):
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


START_CASH = _f("PAPER_START_CASH", 1_000_000)
MAX_LEVERAGE = _f("PAPER_MAX_LEVERAGE", 5)       # gross exposure / equity
COMMISSION_BPS = _f("PAPER_COMMISSION_BPS", 1)   # stocks, ETFs, crypto, bonds
OPTION_FEE = 0.65                                # per contract
FUTURE_FEE = 2.50                                # per contract
SNAPSHOT_SECONDS = 15 * 60

# root -> (dollars per 1.00 of quoted price, asset class). Front-month contracts, Yahoo notation ROOT=F.
FUTURES = {
    "ES": (50, "Equity index"), "NQ": (20, "Equity index"), "YM": (5, "Equity index"), "RTY": (50, "Equity index"),
    "ZT": (2000, "Rates"), "ZF": (1000, "Rates"), "ZN": (1000, "Rates"), "ZB": (1000, "Rates"),
    "6E": (125000, "FX"), "6J": (12500000, "FX"), "6B": (62500, "FX"), "6A": (100000, "FX"),
    "CL": (1000, "Energy"), "BZ": (1000, "Energy"), "NG": (10000, "Energy"), "RB": (42000, "Energy"),
    "HO": (42000, "Energy"),
    "GC": (100, "Metals"), "SI": (5000, "Metals"), "PL": (50, "Metals"), "PA": (100, "Metals"),
    "HG": (25000, "Metals"), "ALI": (25, "Metals"),
    "ZC": (50, "Agriculture"), "ZW": (50, "Agriculture"), "ZS": (50, "Agriculture"), "KC": (375, "Agriculture"),
    "SB": (1120, "Agriculture"), "CT": (500, "Agriculture"), "CC": (10, "Agriculture"), "LE": (400, "Agriculture"),
}

# Treasury bonds are simulated: bought at par with a coupon equal to that day's constant-maturity
# yield, then repriced each day off the FRED curve at the bond's remaining maturity.
UST_TENORS = {"1Y": 1, "2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7, "10Y": 10, "20Y": 20, "30Y": 30}
CURVE_YEARS = {"1M": 1 / 12, "3M": 0.25, "6M": 0.5, "1Y": 1, "2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7,
               "10Y": 10, "20Y": 20, "30Y": 30}
USD_BASE = ("EUR", "GBP", "AUD", "NZD")     # quoted as CCYUSD; everything else as USDCCY


class TradeError(Exception):
    """A reason an order cannot be taken, safe to show to the analyst."""


# ----------------------------------------------------------------------------- pure maths

def bond_price(coupon, yld, years):
    """Clean price per 100 face of a semi-annual bond. coupon and yld in percent."""
    if years <= 0:
        return 100.0
    r = yld / 100.0
    if abs(r) < 1e-9:
        return 100.0 + coupon * years
    d = (1 + r / 2) ** (-2 * years)
    return coupon / r * (1 - d) + 100 * d


def interp(points, x):
    """Linear interpolation on sorted (x, y) points, flat beyond the ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def fill_math(q0, a0, dq, price):
    """Net a trade of signed size dq into a position (q0 @ a0).
    Returns (new quantity, new average price, realized P&L per unit of multiplier)."""
    if abs(q0) < 1e-12 or (q0 > 0) == (dq > 0):
        q1 = q0 + dq
        return q1, (abs(q0) * a0 + abs(dq) * price) / abs(q1), 0.0
    closing = min(abs(dq), abs(q0))
    realized = closing * (price - a0) * (1 if q0 > 0 else -1)
    q1 = q0 + dq
    if abs(q1) < 1e-9:
        return 0.0, 0.0, realized
    if (q1 > 0) == (q0 > 0):
        return q1, a0, realized
    return q1, price, realized


def calc_fee(inst, qty, notional_usd):
    if inst["kind"] == "option":
        return OPTION_FEE * abs(qty)
    if inst["kind"] == "future":
        return FUTURE_FEE * abs(qty)
    if inst["kind"] == "fx":
        return 0.0
    return notional_usd * COMMISSION_BPS / 10000.0


def max_drawdown(values):
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst * 100


# ----------------------------------------------------------------------------- accounts

def ensure_account(user_id):
    acct = db.one("SELECT * FROM paper_accounts WHERE user_id=?", (user_id,))
    if acct:
        return acct
    now = time.time()
    db.ins("INSERT OR IGNORE INTO paper_accounts(user_id,cash,starting,created) VALUES(?,?,?,?)",
           (user_id, START_CASH, START_CASH, now))
    db.ins("INSERT INTO paper_equity(user_id,ts,equity) VALUES(?,?,?)", (user_id, now, START_CASH))
    return db.one("SELECT * FROM paper_accounts WHERE user_id=?", (user_id,))


def ensure_all_accounts():
    for u in db.q("SELECT id FROM users"):
        ensure_account(u["id"])


def reset_account(user_id):
    for table in ("paper_positions", "paper_orders", "paper_equity", "paper_accounts"):
        db.x(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    return ensure_account(user_id)


# ----------------------------------------------------------------------------- instruments

async def resolve(symbol, contract=None, meta=None):
    """Describe a tradable instrument, or raise TradeError explaining why it cannot be traded."""
    symbol = (symbol or "").strip().upper()
    if symbol.startswith("UST:"):
        tenor = symbol[4:]
        if tenor not in UST_TENORS:
            raise TradeError("Treasury tenors: " + ", ".join(UST_TENORS) + ". Example: UST:10Y")
        if not meta:
            curve = await curve_points()
            today = dt.datetime.now(NY).date()
            years = UST_TENORS[tenor]
            meta = {"coupon": round(interp(curve, years), 2), "issued": today.isoformat(),
                    "maturity": today.replace(year=today.year + years).isoformat() if (today.month, today.day) != (2, 29)
                    else (today + dt.timedelta(days=365 * years)).isoformat()}
        return {"key": f"{symbol}|{meta['coupon']:.2f}|{meta['maturity']}", "symbol": symbol, "kind": "bond",
                "asset_class": "Rates", "mult": 0.01, "ccy": "USD", "margin": False, "unit": "USD face value",
                "meta": meta, "desc": f"US Treasury {meta['coupon']:.2f}% {meta['maturity']}"}
    if not market.valid_symbol(symbol):
        raise TradeError("That symbol is not valid.")
    kind = market.kind(symbol)
    if kind == "fred":
        raise TradeError("Economic series cannot be traded. For rates, trade UST:10Y, ZN=F or a bond ETF.")
    if contract:
        try:
            expiry = dt.date.fromisoformat(contract["expiry"]).isoformat()
            strike = float(contract["strike"])
            cp = contract["type"]
            assert cp in ("C", "P") and strike > 0
        except Exception:
            raise TradeError("Option contracts need an expiry (YYYY-MM-DD), a strike and C or P.")
        return {"key": f"{symbol}|{expiry}|{strike:g}|{cp}", "symbol": symbol, "kind": "option",
                "asset_class": "Options", "mult": 100, "ccy": "USD", "margin": False, "unit": "contracts (x100)",
                "meta": {"expiry": expiry, "strike": strike, "type": cp},
                "desc": f"{symbol} {expiry} {strike:g} {'call' if cp == 'C' else 'put'}"}
    if kind == "index" or symbol == "DX-Y.NYB":
        raise TradeError("An index is not tradable itself. Trade its future (ES=F), an ETF (SPY) or options.")
    base = {"key": symbol, "symbol": symbol, "meta": {}, "margin": False, "mult": 1, "ccy": "USD"}
    if kind == "fx":
        pair = symbol[:-2]
        if len(pair) == 3:
            pair = "USD" + pair
        if len(pair) != 6:
            raise TradeError("FX pairs look like EURUSD=X.")
        return dict(base, kind="fx", asset_class="FX", ccy=pair[3:], margin=True, unit=f"{pair[:3]} (base currency units)",
                    meta={"base": pair[:3], "quote": pair[3:]}, desc=f"{pair[:3]}/{pair[3:]} spot")
    if kind == "future":
        root = symbol[:-2]
        if root not in FUTURES:
            raise TradeError(f"No contract size on file for {symbol}. Add it to FUTURES in app/paper.py.")
        mult, cls = FUTURES[root]
        return dict(base, kind="future", asset_class=cls, mult=mult, margin=True, unit=f"contracts (x{mult:,g})",
                    desc=f"{market.label_for(symbol) or root} future ({symbol})")
    if kind == "crypto":
        return dict(base, kind="crypto", asset_class="Crypto", unit="coins", desc=symbol)
    ccy = "USD"
    if kind == "intl":
        ccy = ((await market.get_profile(symbol)) or {}).get("currency")
        if not ccy:
            raise TradeError(f"Could not determine the trading currency of {symbol}.")
    label = market.label_for(symbol)
    return dict(base, kind="equity", asset_class="Equity", ccy=ccy, unit="shares",
                desc=f"{symbol} · {label}" if label else symbol)


async def curve_points():
    async def one(label, series):
        try:
            return CURVE_YEARS[label], (await market.get_fred(series))[-1][1]
        except Exception:
            return None
    pts = sorted(p for p in await asyncio.gather(*(one(l, s) for l, s in US_CURVE)) if p)
    if len(pts) < 2:
        raise TradeError("The Treasury curve is not available right now.")
    return pts


async def usd_per(ccy):
    """US dollars per one unit of ccy."""
    if ccy == "USD":
        return 1.0
    minor = ccy in ("GBp", "GBX", "ZAc", "ILA")       # prices quoted in pence / cents / agorot
    code = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}.get(ccy, ccy).upper()
    if code == "USD":
        return 1.0
    if code in USD_BASE:
        q = await market.get_quote(f"{code}USD=X")
        rate = q["price"] if q else None
    else:
        q = await market.get_quote(f"USD{code}=X")
        rate = 1.0 / q["price"] if q and q["price"] else None
    if not rate:
        raise TradeError(f"No FX rate available for {code}.")
    return rate / 100.0 if minor else rate


async def mark(inst):
    """Current valuation of one unit: {price, bid, ask, pct, source, delayed, extra}."""
    kind = inst["kind"]
    if kind == "bond":
        m = inst["meta"]
        today = dt.datetime.now(NY).date()
        years = (dt.date.fromisoformat(m["maturity"]) - today).days / 365.25
        curve = await curve_points()
        yld = interp(curve, max(years, 0))
        clean = bond_price(m["coupon"], yld, years)
        accrued = m["coupon"] * max((today - dt.date.fromisoformat(m["issued"])).days, 0) / 365.0
        dv01 = (bond_price(m["coupon"], yld - 0.01, years) - bond_price(m["coupon"], yld + 0.01, years)) / 2
        price = clean + accrued
        return {"price": price, "bid": price, "ask": price, "pct": None, "source": "demo" if settings.demo else "fred",
                "delayed": not settings.demo, "extra": {"yield": yld, "clean": clean, "accrued": accrued,
                                                        "dv01": dv01, "years": years}}
    if kind == "option":
        m = inst["meta"]
        chain = await market.get_chain(inst["symbol"], m["expiry"])
        row = next((r for r in chain["rows"] if r["type"] == m["type"] and r.get("strike") is not None
                    and abs(r["strike"] - m["strike"]) < 1e-6), None)
        if not row:
            raise TradeError("That option contract is not in the chain.")
        price = row.get("mid") or row.get("last")
        if not price or price <= 0:
            raise TradeError("That option has no price right now.")
        return {"price": price, "bid": row.get("bid") or None, "ask": row.get("ask") or None, "pct": None,
                "source": chain["source"], "delayed": chain["source"] != "demo",
                "extra": {"delta": row.get("delta"), "gamma": row.get("gamma"), "theta": row.get("theta"),
                          "vega": row.get("vega"), "iv": row.get("iv"), "spot": chain["spot"]}}
    q = await market.get_quote(inst["symbol"])
    if not q or not q.get("price"):
        raise TradeError(f"No price available for {inst['symbol']}.")
    return {"price": q["price"], "bid": q.get("bid"), "ask": q.get("ask"), "pct": q.get("pct"),
            "source": q.get("source"), "delayed": q.get("delayed"), "extra": {}}


def exposure_usd(inst, qty, mk, fx):
    """Signed dollar exposure. Options are delta-adjusted when a delta is available."""
    if inst["kind"] == "option" and mk["extra"].get("delta") is not None and mk["extra"].get("spot"):
        return qty * mk["extra"]["delta"] * mk["extra"]["spot"] * inst["mult"]
    return qty * mk["price"] * inst["mult"] * fx


def exec_price(side, mk):
    """Buys lift the offer and sells hit the bid when a two-sided quote exists; otherwise the last price."""
    last = mk["price"]
    px = mk.get("ask") if side == "buy" else mk.get("bid")
    if px and px > 0 and 0.5 * last <= px <= 2 * last:
        return px
    return last


# ----------------------------------------------------------------------------- portfolio

def _day_start():
    now = dt.datetime.now(NY)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


async def portfolio(user_id):
    acct = ensure_account(user_id)
    rows = db.q("SELECT * FROM paper_positions WHERE user_id=? ORDER BY opened", (user_id,))

    async def value(row):
        inst = json.loads(row["inst"])
        try:
            mk, fx = await asyncio.gather(mark(inst), usd_per(inst["ccy"]))
            stale = False
        except Exception as e:
            # No price right now: carry the position at cost so NAV does not jump, and say so.
            mk = {"price": row["avg_price"], "bid": None, "ask": None, "pct": None, "source": "", "delayed": True,
                  "extra": {}}
            fx, stale = row["last_fx"] or 1.0, str(e)[:120]
        return row, inst, mk, fx, stale
    valued = await asyncio.gather(*(value(r) for r in rows))

    positions, classes = [], {}
    mv_cash = open_pnl_margin = unrealized = long_x = short_x = 0.0
    greeks = {"delta_usd": 0.0, "gamma": 0.0, "theta_usd": 0.0, "vega_usd": 0.0, "dv01_usd": 0.0}
    for row, inst, mk, fx, stale in valued:
        qty, mult = row["qty"], inst["mult"]
        mv = qty * mk["price"] * mult * fx
        cost = qty * row["avg_price"] * mult * fx
        pnl = mv - cost
        expo = exposure_usd(inst, qty, mk, fx)
        unrealized += pnl
        if inst["margin"]:
            open_pnl_margin += pnl
        else:
            mv_cash += mv
        long_x += max(expo, 0)
        short_x += min(expo, 0)
        c = classes.setdefault(inst["asset_class"], {"long": 0.0, "short": 0.0})
        c["long"] += max(expo, 0)
        c["short"] += min(expo, 0)
        ex = mk["extra"]
        if inst["kind"] == "option":
            greeks["delta_usd"] += expo
            for g, k in (("gamma", "gamma"), ("theta_usd", "theta"), ("vega_usd", "vega")):
                if ex.get(k) is not None:
                    greeks[g] += ex[k] * qty * mult
        if inst["kind"] == "bond" and ex.get("dv01") is not None:
            greeks["dv01_usd"] += ex["dv01"] * qty * mult
        positions.append({
            "id": row["id"], "key": row["pos_key"], "symbol": inst["symbol"], "desc": inst["desc"],
            "kind": inst["kind"], "asset_class": inst["asset_class"], "meta": inst["meta"], "unit": inst["unit"],
            "qty": qty, "avg_price": row["avg_price"], "price": mk["price"], "mult": mult, "ccy": inst["ccy"],
            "fx": fx, "margin": inst["margin"], "value": pnl if inst["margin"] else mv, "exposure": expo,
            "unrealized": pnl, "unrealized_pct": (pnl / abs(cost) * 100) if cost else None, "day_pct": mk["pct"],
            "source": mk["source"], "delayed": mk["delayed"], "stale": stale, "extra": ex, "opened": row["opened"]})
    equity = acct["cash"] + mv_cash + open_pnl_margin
    gross = long_x - short_x
    base = db.one("SELECT equity FROM paper_equity WHERE user_id=? AND ts<? ORDER BY ts DESC LIMIT 1",
                  (user_id, _day_start()))
    if not base:
        base = db.one("SELECT equity FROM paper_equity WHERE user_id=? ORDER BY ts LIMIT 1", (user_id,))
    day_base = base["equity"] if base else acct["starting"]
    for p in positions:
        p["pct_nav"] = abs(p["exposure"]) / equity * 100 if equity > 0 else None
    exposure = [{"asset_class": k, "long": v["long"], "short": v["short"], "net": v["long"] + v["short"],
                 "gross": v["long"] - v["short"],
                 "pct_nav": (v["long"] - v["short"]) / equity * 100 if equity > 0 else None}
                for k, v in sorted(classes.items(), key=lambda kv: -(kv[1]["long"] - kv[1]["short"]))]
    return {
        "user_id": user_id,
        "summary": {
            "equity": equity, "cash": acct["cash"], "starting": acct["starting"],
            "total_pnl": equity - acct["starting"], "return_pct": (equity / acct["starting"] - 1) * 100,
            "day_pnl": equity - day_base, "day_pct": (equity / day_base - 1) * 100 if day_base else None,
            "unrealized": unrealized, "realized": acct["realized"], "fees": acct["fees"],
            "long": long_x, "short": short_x, "gross": gross, "net": long_x + short_x,
            "leverage": gross / equity if equity > 0 else None, "max_leverage": MAX_LEVERAGE,
            "buying_power": max(0.0, MAX_LEVERAGE * equity - gross), "positions": len(positions)},
        "positions": positions, "exposure": exposure, "greeks": greeks}


def equity_curve(user_id, limit=800):
    rows = db.q("SELECT ts, equity FROM paper_equity WHERE user_id=? ORDER BY ts", (user_id,))
    step = max(1, math.ceil(len(rows) / limit))
    pts = rows[::step]
    if rows and pts[-1] is not rows[-1]:
        pts.append(rows[-1])
    return pts


def stats(user_id):
    rows = db.q("SELECT ts, equity FROM paper_equity WHERE user_id=? ORDER BY ts", (user_id,))
    values = [r["equity"] for r in rows]
    daily = {}
    for r in rows:
        daily[dt.datetime.fromtimestamp(r["ts"], NY).date()] = r["equity"]
    closes = [v for _, v in sorted(daily.items())]
    rets = [b / a - 1 for a, b in zip(closes, closes[1:]) if a > 0]
    vol = statistics.pstdev(rets) * math.sqrt(252) * 100 if len(rets) >= 5 else None
    sharpe = (statistics.fmean(rets) * 252) / (statistics.pstdev(rets) * math.sqrt(252)) \
        if len(rets) >= 5 and statistics.pstdev(rets) > 0 else None
    fills = db.q("SELECT realized FROM paper_orders WHERE user_id=? AND status='filled' AND realized<>0", (user_id,))
    wins = sum(1 for f in fills if f["realized"] > 0)
    return {"max_drawdown": max_drawdown(values), "vol": vol, "sharpe": sharpe, "days": len(closes),
            "closed_trades": len(fills), "win_rate": wins / len(fills) * 100 if fills else None}


# ----------------------------------------------------------------------------- orders

def apply_fill(user_id, inst, dq, price, fx, free=False):
    """Book a fill. No awaits in here, so it cannot interleave with another fill. Returns (realized, fee)."""
    acct = ensure_account(user_id)
    pos = db.one("SELECT * FROM paper_positions WHERE user_id=? AND pos_key=?", (user_id, inst["key"]))
    q0, a0 = (pos["qty"], pos["avg_price"]) if pos else (0.0, 0.0)
    q1, a1, realized_unit = fill_math(q0, a0, dq, price)
    mult = inst["mult"]
    realized = realized_unit * mult * fx
    fee = 0.0 if free else calc_fee(inst, dq, abs(dq) * price * mult * fx)
    cash = acct["cash"] - fee
    cash += realized if inst["margin"] else -dq * price * mult * fx
    if abs(q1) < 1e-9:
        if pos:
            db.x("DELETE FROM paper_positions WHERE id=?", (pos["id"],))
    elif pos:
        db.x("UPDATE paper_positions SET qty=?, avg_price=?, last_fx=? WHERE id=?", (q1, a1, fx, pos["id"]))
    else:
        db.ins("INSERT INTO paper_positions(user_id,pos_key,inst,qty,avg_price,last_fx,opened) VALUES(?,?,?,?,?,?,?)",
               (user_id, inst["key"], json.dumps(inst), q1, a1, fx, time.time()))
    db.x("UPDATE paper_accounts SET cash=?, realized=realized+?, fees=fees+? WHERE user_id=?",
         (cash, realized, fee, user_id))
    return realized, fee


async def assess(user_id, inst, side, qty, mk=None):
    """Price an order and test it against the leverage limit. Returns a preview dict; ok=False carries the reason."""
    mk = mk or await mark(inst)
    fx = await usd_per(inst["ccy"])
    price = exec_price(side, mk)
    dq = qty if side == "buy" else -qty
    notional = qty * price * inst["mult"] * fx
    fee = calc_fee(inst, qty, notional)
    port = await portfolio(user_id)
    s = port["summary"]
    held = next((p for p in port["positions"] if p["key"] == inst["key"]), None)
    q0 = held["qty"] if held else 0.0
    unit_expo = abs(exposure_usd(inst, 1.0, mk, fx))
    new_gross = s["gross"] + (abs(q0 + dq) - abs(q0)) * unit_expo
    lev = new_gross / s["equity"] if s["equity"] > 0 else float("inf")
    out = {"instrument": inst, "price": price, "last": mk["price"], "bid": mk.get("bid"), "ask": mk.get("ask"),
           "fx": fx, "notional": notional, "fee": fee, "cash_change": 0.0 if inst["margin"] else -dq * price * inst["mult"] * fx,
           "position_before": q0, "position_after": q0 + dq, "leverage_after": lev, "max_leverage": MAX_LEVERAGE,
           "equity": s["equity"], "buying_power": s["buying_power"], "source": mk["source"], "delayed": mk["delayed"],
           "extra": mk["extra"], "ok": True, "reason": ""}
    if q0 and (q0 > 0) != (dq > 0) and abs(dq) > abs(q0):
        out["warning"] = f"This closes your {'long' if q0 > 0 else 'short'} and opens a {'short' if q0 > 0 else 'long'}."
    elif not q0 and dq < 0:
        out["warning"] = "You hold none: this opens a short position."
    if abs(q0 + dq) > abs(q0) and lev > MAX_LEVERAGE:
        out.update(ok=False, reason=f"Gross exposure would be {lev:.1f}x equity; the desk limit is {MAX_LEVERAGE:g}x.")
    return out


def _validate(inst, side, qty, otype, limit_price, stop_price):
    if side not in ("buy", "sell"):
        raise TradeError("Side must be buy or sell.")
    if not (isinstance(qty, (int, float)) and math.isfinite(qty) and qty > 0):
        raise TradeError("Quantity must be a positive number.")
    if inst["kind"] in ("option", "future") and abs(qty - round(qty)) > 1e-9:
        raise TradeError("Trade whole contracts.")
    if inst["kind"] == "equity" and abs(qty - round(qty)) > 1e-9:
        raise TradeError("Trade whole shares.")
    if otype not in ("market", "limit", "stop"):
        raise TradeError("Order type must be market, limit or stop.")
    if otype == "limit" and not (limit_price and limit_price > 0):
        raise TradeError("A limit order needs a limit price.")
    if otype == "stop" and not (stop_price and stop_price > 0):
        raise TradeError("A stop order needs a stop price.")
    if inst["kind"] == "bond" and otype != "market":
        raise TradeError("Treasury bonds reprice once a day, so they take market orders only.")


def _triggered(side, otype, limit_price, stop_price, mk):
    px = exec_price(side, mk)
    if otype == "market":
        return True
    if otype == "limit":
        return px <= limit_price if side == "buy" else px >= limit_price
    return mk["price"] >= stop_price if side == "buy" else mk["price"] <= stop_price


async def place_order(user_id, symbol, side, qty, otype="market", limit_price=None, stop_price=None,
                      contract=None, note="", position_id=None):
    if position_id:
        pos = db.one("SELECT inst FROM paper_positions WHERE id=? AND user_id=?", (position_id, user_id))
        if not pos:
            raise TradeError("That position no longer exists.")
        inst = json.loads(pos["inst"])
    else:
        inst = await resolve(symbol, contract)
    _validate(inst, side, qty, otype, limit_price, stop_price)
    mk = await mark(inst)
    order_id = db.ins(
        "INSERT INTO paper_orders(user_id,pos_key,symbol,descr,inst,side,qty,type,limit_price,stop_price,status,note,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, inst["key"], inst["symbol"], inst["desc"], json.dumps(inst), side, qty, otype, limit_price,
         stop_price, "open", (note or "").strip()[:300], time.time()))
    if _triggered(side, otype, limit_price, stop_price, mk):
        await _try_fill(order_id, mk)
    return db.one("SELECT * FROM paper_orders WHERE id=?", (order_id,))


async def _try_fill(order_id, mk=None):
    o = db.one("SELECT * FROM paper_orders WHERE id=? AND status='open'", (order_id,))
    if not o:
        return None
    inst = json.loads(o["inst"])
    try:
        mk = mk or await mark(inst)
        a = await assess(o["user_id"], inst, o["side"], o["qty"], mk)
    except Exception as e:
        db.x("UPDATE paper_orders SET status='rejected', reason=? WHERE id=?", (str(e)[:200], order_id))
        return "rejected"
    if not a["ok"]:
        db.x("UPDATE paper_orders SET status='rejected', reason=? WHERE id=?", (a["reason"], order_id))
        return "rejected"
    # Re-check that it is still open: another coroutine may have cancelled it while we were pricing.
    if not db.one("SELECT id FROM paper_orders WHERE id=? AND status='open'", (order_id,)):
        return None
    dq = o["qty"] if o["side"] == "buy" else -o["qty"]
    realized, fee = apply_fill(o["user_id"], inst, dq, a["price"], a["fx"])
    db.x("UPDATE paper_orders SET status='filled', fill_price=?, fill_fx=?, fee=?, realized=?, filled_at=? WHERE id=?",
         (a["price"], a["fx"], fee, realized, time.time(), order_id))
    return "filled"


def cancel_order(user_id, order_id):
    return db.x("UPDATE paper_orders SET status='cancelled' WHERE id=? AND user_id=? AND status='open'",
                (order_id, user_id))


def orders(user_id, limit=200):
    rows = db.q("SELECT id,symbol,descr,side,qty,type,limit_price,stop_price,status,fill_price,fee,realized,note,"
                "reason,created,filled_at FROM paper_orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    return rows


# ----------------------------------------------------------------------------- engine

def _px(v):
    return f"{v:,.2f}" if abs(v) >= 10 else f"{v:,.4f}"


def _notify(user_id, message):
    db.ins("INSERT INTO alert_events(alert_id,user_id,ts,value,message) VALUES(0,?,?,NULL,?)",
           (user_id, time.time(), message))


async def _settle(row, inst, price, why):
    """Close a position at a settlement price with no fee and record it in the blotter."""
    dq = -row["qty"]
    fx = await usd_per(inst["ccy"])
    realized, _ = apply_fill(row["user_id"], inst, dq, price, fx, free=True)
    now = time.time()
    db.ins("INSERT INTO paper_orders(user_id,pos_key,symbol,descr,inst,side,qty,type,status,fill_price,fill_fx,fee,"
           "realized,note,created,filled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (row["user_id"], inst["key"], inst["symbol"], inst["desc"], json.dumps(inst), "buy" if dq > 0 else "sell",
            abs(dq), "settlement", "filled", price, fx, 0.0, realized, why, now, now))
    _notify(row["user_id"], f"Order: {inst['desc']} {why} at {_px(price)}. Realized P&L ${realized:,.0f}.")


async def cycle():
    """One engine pass: work resting orders, settle expiries, record equity."""
    now_ny = dt.datetime.now(NY)
    for o in db.q("SELECT * FROM paper_orders WHERE status='open'"):
        inst = json.loads(o["inst"])
        try:
            mk = await mark(inst)
        except Exception:
            continue                      # no price this pass; try again next time
        if _triggered(o["side"], o["type"], o["limit_price"], o["stop_price"], mk):
            result = await _try_fill(o["id"], mk)
            done = db.one("SELECT * FROM paper_orders WHERE id=?", (o["id"],))
            if result == "filled":
                _notify(o["user_id"], f"Order filled: {o['side']} {o['qty']:g} {o['descr']} at {_px(done['fill_price'])} "
                                      f"({o['type']}).")
            elif result == "rejected":
                _notify(o["user_id"], f"Order rejected: {o['side']} {o['qty']:g} {o['descr']}. {done['reason']}")
    for row in db.q("SELECT * FROM paper_positions"):
        inst = json.loads(row["inst"])
        try:
            if inst["kind"] == "option":
                expiry = dt.datetime.combine(dt.date.fromisoformat(inst["meta"]["expiry"]), dt.time(16, 15), tzinfo=NY)
                if now_ny >= expiry:
                    q = await market.get_quote(inst["symbol"])
                    if q and q.get("price"):
                        k, cp = inst["meta"]["strike"], inst["meta"]["type"]
                        intrinsic = max(0.0, q["price"] - k) if cp == "C" else max(0.0, k - q["price"])
                        await _settle(row, inst, intrinsic, "expired" if intrinsic == 0 else "expired in the money, cash-settled")
            elif inst["kind"] == "bond" and dt.date.fromisoformat(inst["meta"]["maturity"]) <= now_ny.date():
                mk = await mark(inst)
                await _settle(row, inst, mk["price"], "matured")
        except Exception as e:
            log.warning("settlement of position %s failed: %s", row["id"], e)
    now = time.time()
    for acct in db.q("SELECT user_id FROM paper_accounts"):
        last = db.one("SELECT ts FROM paper_equity WHERE user_id=? ORDER BY ts DESC LIMIT 1", (acct["user_id"],))
        if last and now - last["ts"] < SNAPSHOT_SECONDS:
            continue
        try:
            eq = (await portfolio(acct["user_id"]))["summary"]["equity"]
            db.ins("INSERT INTO paper_equity(user_id,ts,equity) VALUES(?,?,?)", (acct["user_id"], now, eq))
        except Exception as e:
            log.warning("equity snapshot for user %s failed: %s", acct["user_id"], e)


async def run(stop):
    log.info("Paper-trading engine started")
    while not stop.is_set():
        try:
            await cycle()
        except Exception:
            log.exception("paper cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.alert_poll)
        except asyncio.TimeoutError:
            pass


# ----------------------------------------------------------------------------- firm view (CIO)

async def firm():
    users = db.q("SELECT id, username, role, active FROM users WHERE active=1 ORDER BY id")
    ports = await asyncio.gather(*(portfolio(u["id"]) for u in users), return_exceptions=True)
    rows, classes = [], {}
    total = {"equity": 0.0, "starting": 0.0, "day_pnl": 0.0, "long": 0.0, "short": 0.0}
    for u, p in zip(users, ports):
        if isinstance(p, Exception):
            rows.append({"user_id": u["id"], "username": u["username"], "role": u["role"], "error": str(p)[:120]})
            continue
        s = p["summary"]
        top = max(p["positions"], key=lambda x: abs(x["exposure"]), default=None)
        rows.append({"user_id": u["id"], "username": u["username"], "role": u["role"], **s, **stats(u["id"]),
                     "top": f"{top['desc']} ({top['pct_nav']:.0f}% NAV)" if top and top.get("pct_nav") is not None else ""})
        for k in total:
            total[k] += s[k]
        for e in p["exposure"]:
            c = classes.setdefault(e["asset_class"], {"long": 0.0, "short": 0.0})
            c["long"] += e["long"]
            c["short"] += e["short"]
    rows.sort(key=lambda r: -(r.get("return_pct") if r.get("return_pct") is not None else -1e9))
    total["return_pct"] = (total["equity"] / total["starting"] - 1) * 100 if total["starting"] else 0.0
    total["gross"] = total["long"] - total["short"]
    total["net"] = total["long"] + total["short"]
    exposure = [{"asset_class": k, "long": v["long"], "short": v["short"], "net": v["long"] + v["short"],
                 "gross": v["long"] - v["short"]} for k, v in sorted(classes.items(), key=lambda kv: -(kv[1]["long"] - kv[1]["short"]))]
    return {"analysts": rows, "total": total, "exposure": exposure}
