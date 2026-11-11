"""Alarm engine: computes metrics, checks every active alarm on a timer, and notifies.

Runs on the server, so alarms fire whether or not the analyst has the terminal open.
"""
import asyncio
import json
import logging
import math
import smtplib
import statistics
import time
from email.message import EmailMessage

from . import db, market
from . import providers as P
from .config import settings

log = logging.getLogger("augusta.alerts")

# key -> (label, unit, group). Groups: quote, intraday, daily, option.
METRICS = {
    "price": ("Last price / level", "", "quote"),
    "change_pct": ("Day change %", "%", "quote"),
    "change_abs": ("Day change (absolute)", "", "quote"),
    "volume": ("Day volume", "", "quote"),
    "day_high": ("Day high", "", "quote"),
    "day_low": ("Day low", "", "quote"),
    "vwap": ("Session VWAP", "", "intraday"),
    "price_vs_vwap": ("Price vs VWAP %", "%", "intraday"),
    "rvol20": ("Relative volume (x 20-day avg)", "x", "daily"),
    "rsi14": ("RSI (14-day)", "", "daily"),
    "zscore20": ("Z-score vs 20-day mean", "sd", "daily"),
    "hv20": ("Realized vol, 20-day annualized %", "%", "daily"),
    "sma50": ("50-day moving average", "", "daily"),
    "sma200": ("200-day moving average", "", "daily"),
    "price_vs_sma50": ("Price vs 50-day SMA %", "%", "daily"),
    "price_vs_sma200": ("Price vs 200-day SMA %", "%", "daily"),
    "off_52w_high": ("Distance from 52-week high %", "%", "daily"),
    "off_52w_low": ("Distance from 52-week low %", "%", "daily"),
    "opt_price": ("Option mid price", "", "option"),
    "iv": ("Option implied vol %", "%", "option"),
    "delta": ("Option delta", "", "option"),
    "gamma": ("Option gamma", "", "option"),
    "theta": ("Option theta (per day)", "", "option"),
    "vega": ("Option vega (per vol point)", "", "option"),
    "opt_volume": ("Option volume", "", "option"),
    "open_interest": ("Option open interest", "", "option"),
}

OPS = {">=": "at or above", "<=": "at or below", "cross_up": "crosses above", "cross_down": "crosses below"}
CHANNELS = ("app", "email", "discord", "slack")


# ----------------------------------------------------------------------------- indicators

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def zscore(values, n=20):
    if len(values) < n:
        return None
    window = values[-n:]
    sd = statistics.pstdev(window)
    return (values[-1] - statistics.fmean(window)) / sd if sd else None


def realized_vol(closes, n=20):
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    return statistics.pstdev(rets) * math.sqrt(252) * 100 if len(rets) > 1 else None


async def compute_metrics(symbol, contract=None, wanted=None):
    """Returns {metric: value or None} for the requested metrics (all applicable when wanted is None)."""
    is_fred = market.kind(symbol) == "fred"
    if wanted is None:
        wanted = set(METRICS)
        if not contract:
            wanted -= {k for k, v in METRICS.items() if v[2] == "option"}
        if is_fred:
            wanted = {"price", "change_abs", "change_pct", "zscore20", "sma50", "sma200",
                      "off_52w_high", "off_52w_low"}
    groups = {METRICS[m][2] for m in wanted if m in METRICS}
    out = {}
    quote = await market.get_quote(symbol)
    if not quote:
        raise RuntimeError(f"No quote for {symbol}")
    price = quote["price"]
    out.update(price=price, change_pct=quote.get("pct"), change_abs=quote.get("change"),
               volume=quote.get("volume"), day_high=quote.get("high"), day_low=quote.get("low"))
    if "intraday" in groups and not is_fred:
        vwap = await market.session_vwap(symbol)
        out["vwap"] = vwap
        out["price_vs_vwap"] = (price / vwap - 1) * 100 if vwap else None
    if "daily" in groups:
        bars = (await market.get_history(symbol, "1Y"))["bars"]
        closes = [b["c"] for b in bars]
        if closes:
            closes[-1] = price
            vols = [b["v"] for b in bars[:-1] if b["v"]]
            avg_v = sum(vols[-20:]) / len(vols[-20:]) if vols[-20:] else None
            s50, s200 = sma(closes, 50), sma(closes, 200)
            hi, lo = max(b["h"] for b in bars), min(b["l"] for b in bars)
            out.update(
                rvol20=(quote["volume"] / avg_v) if quote.get("volume") and avg_v else None,
                rsi14=rsi(closes), zscore20=zscore(closes), hv20=realized_vol(closes),
                sma50=s50, sma200=s200,
                price_vs_sma50=(price / s50 - 1) * 100 if s50 else None,
                price_vs_sma200=(price / s200 - 1) * 100 if s200 else None,
                off_52w_high=(price / hi - 1) * 100 if hi else None,
                off_52w_low=(price / lo - 1) * 100 if lo else None)
    if "option" in groups and contract:
        chain = await market.get_chain(symbol, contract["expiry"])
        row = next((r for r in chain["rows"] if r["type"] == contract["type"]
                    and r.get("strike") is not None and abs(r["strike"] - float(contract["strike"])) < 1e-6), None)
        if not row:
            raise RuntimeError("Option contract not found in chain")
        out.update(opt_price=row.get("mid"), iv=row["iv"] * 100 if row.get("iv") else None,
                   delta=row.get("delta"), gamma=row.get("gamma"), theta=row.get("theta"),
                   vega=row.get("vega"), opt_volume=row.get("volume"), open_interest=row.get("oi"))
    return {k: v for k, v in out.items() if k in wanted}


# ----------------------------------------------------------------------------- evaluation

def check(op, threshold, last, cur):
    if op == ">=":
        return cur >= threshold
    if op == "<=":
        return cur <= threshold
    if last is None:
        return False
    if op == "cross_up":
        return last < threshold <= cur
    if op == "cross_down":
        return last > threshold >= cur
    return False


def fmt(v):
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.2f}"
    if a >= 1:
        return f"{v:.4g}" if a < 10 else f"{v:.2f}"
    return f"{v:.4f}"


def describe_target(symbol, contract):
    if not contract:
        return symbol
    return f"{symbol} {contract['expiry']} {float(contract['strike']):g}{contract['type']}"


def describe(alert):
    contract = json.loads(alert["contract"]) if alert.get("contract") else None
    label, unit, _ = METRICS[alert["metric"]]
    return f"{describe_target(alert['symbol'], contract)} — {label} {OPS[alert['op']]} {fmt(alert['threshold'])}{unit}"


async def evaluate_once():
    """One pass over all active alarms. Returns the events fired."""
    alerts = db.q("SELECT a.*, u.username, u.email FROM alerts a JOIN users u ON u.id=a.user_id "
                  "WHERE a.status='active' AND u.active=1")
    groups = {}
    for a in alerts:
        groups.setdefault((a["symbol"], a["contract"] or ""), []).append(a)
    fired = []

    async def run_group(key, items):
        symbol, contract_json = key
        contract = json.loads(contract_json) if contract_json else None
        now = time.time()
        try:
            values = await compute_metrics(symbol, contract, {a["metric"] for a in items})
        except Exception as e:
            for a in items:
                db.x("UPDATE alerts SET last_checked=?, last_error=? WHERE id=?", (now, str(e)[:200], a["id"]))
            return
        for a in items:
            cur = values.get(a["metric"])
            if cur is None:
                db.x("UPDATE alerts SET last_checked=?, last_error=? WHERE id=?",
                     (now, "No data for this metric right now", a["id"]))
                continue
            hit = check(a["op"], a["threshold"], a["last_value"], cur) and now >= a["cooldown_until"]
            db.x("UPDATE alerts SET last_value=?, last_checked=?, last_error=NULL WHERE id=?", (cur, now, a["id"]))
            if hit:
                fired.append(fire(a, cur, now))

    await asyncio.gather(*(run_group(k, v) for k, v in groups.items()))
    return fired


def fire(alert, value, now):
    unit = METRICS[alert["metric"]][1]
    message = f"{describe(alert)}. Now {fmt(value)}{unit}."
    if alert.get("note"):
        message += f" Note: {alert['note']}"
    event_id = db.ins("INSERT INTO alert_events(alert_id,user_id,ts,value,message) VALUES(?,?,?,?,?)",
                      (alert["id"], alert["user_id"], now, value, message))
    if alert["repeat"]:
        db.x("UPDATE alerts SET triggered_at=?, cooldown_until=? WHERE id=?",
             (now, now + alert["cooldown_min"] * 60, alert["id"]))
    else:
        db.x("UPDATE alerts SET status='triggered', triggered_at=? WHERE id=?", (now, alert["id"]))
    channels = [c for c in json.loads(alert["channels"] or "[]") if c != "app"]
    if channels:
        asyncio.get_running_loop().create_task(
            notify(channels, f"Augusta alarm: {alert['symbol']}", f"[{alert['username']}] {message}",
                   alert.get("email")))
    return {"id": event_id, "alert_id": alert["id"], "user_id": alert["user_id"], "message": message}


# ----------------------------------------------------------------------------- notifications

def _send_email_sync(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(body + "\n\nSent by the Augusta Terminal alarm engine.")
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.starttls()
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


async def notify(channels, subject, body, email=None):
    """Returns {channel: "sent" | reason}. Never raises."""
    results = {}
    for ch in channels:
        try:
            if ch == "email":
                if not settings.smtp_host:
                    results[ch] = "SMTP is not configured"
                elif not email:
                    results[ch] = "No email address on this account"
                else:
                    await asyncio.to_thread(_send_email_sync, email, subject, body)
                    results[ch] = "sent"
            elif ch in ("discord", "slack"):
                url = settings.discord_webhook if ch == "discord" else settings.slack_webhook
                if not url:
                    results[ch] = f"{ch.title()} webhook is not configured"
                else:
                    payload = {"content": body[:1900]} if ch == "discord" else {"text": body}
                    r = await P.client().post(url, json=payload)
                    results[ch] = "sent" if r.status_code < 300 else f"HTTP {r.status_code}"
        except Exception as e:
            results[ch] = f"failed: {str(e)[:150]}"
            log.warning("notify %s failed: %s", ch, e)
    return results


async def run(stop):
    log.info("Alarm engine started (every %ss)", settings.alert_poll)
    while not stop.is_set():
        try:
            await evaluate_once()
        except Exception:
            log.exception("alarm pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.alert_poll)
        except asyncio.TimeoutError:
            pass
