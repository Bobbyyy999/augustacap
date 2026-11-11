"""Augusta Terminal web server (FastAPI)."""
import asyncio
import contextlib
import csv
import datetime as dt
import io
import json
import logging
import math
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import alerts, auth, db, market, paper
from . import providers as P
from .config import BASE, settings
from .universe import (CREDIT, DEFAULT_WATCHLIST, EM_10Y, G7_10Y, POLICY_RATES, SCREENS, TICKER_STRIP,
                       US_CURVE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("augusta")
STATIC = BASE / "static"


@contextlib.asynccontextmanager
async def lifespan(app):
    db.connect()
    msg = auth.ensure_admin()
    if msg:
        print("\n" + "=" * 78 + f"\n{msg}\n" + "=" * 78 + "\n", flush=True)
        admin = db.one("SELECT id FROM users ORDER BY id LIMIT 1")
        seed_watchlist(admin["id"])
    paper.ensure_all_accounts()
    stop = asyncio.Event()
    tasks = [asyncio.create_task(alerts.run(stop)), asyncio.create_task(paper.run(stop))]
    if settings.twelvedata_stream and settings.twelvedata_key and not settings.demo:
        tasks.append(asyncio.create_task(P.td_stream(market.stream_symbols, market.on_stream_price, stop)))
    log.info("Augusta Terminal up. mode=%s  http://%s:%s", settings.data_mode, settings.host, settings.port)
    yield
    stop.set()
    for t in tasks:
        t.cancel()


app = FastAPI(title="Augusta Terminal", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    # Cross-site request forgery guard: browsers cannot attach this header cross-site without CORS,
    # and this server never enables CORS.
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "augusta":
            return JSONResponse({"detail": "Missing request header."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def active_user(request: Request):
    user = auth.current_user(request)
    if user["must_change"]:
        raise HTTPException(403, "Change your temporary password first.")
    return user


def admin(request: Request):
    user = active_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only.")
    return user


def seed_watchlist(user_id):
    for i, sym in enumerate(DEFAULT_WATCHLIST):
        db.x("INSERT OR IGNORE INTO watchlist(user_id,symbol,pos) VALUES(?,?,?)", (user_id, sym, i))


def clean(obj):
    """Replace NaN/inf (not valid JSON) with None, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def sym(symbol):
    symbol = (symbol or "").strip()
    symbol = symbol if symbol.startswith("FRED:") else symbol.upper()
    if symbol.upper().startswith("FRED:"):
        symbol = "FRED:" + symbol[5:].upper()
    if not market.valid_symbol(symbol):
        raise HTTPException(400, "Bad symbol.")
    return symbol


async def data(coro):
    """Turn provider failures into a readable 502 instead of a stack trace."""
    try:
        return clean(await coro)
    except HTTPException:
        raise
    except Exception as e:
        log.warning("data error: %s", e)
        raise HTTPException(502, f"Data source error: {str(e)[:200]}")


# ----------------------------------------------------------------------------- pages

@app.get("/")
def index(request: Request):
    if not auth.user_from_token(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ----------------------------------------------------------------------------- session

class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@app.post("/api/login")
def api_login(body: LoginBody):
    token = auth.login(body.username.strip(), body.password)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=settings.https,
                    max_age=settings.session_hours * 3600, path="/")
    return resp


@app.post("/api/logout")
def api_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/me")
def api_me(user=Depends(auth.current_user)):
    return user


class PasswordBody(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@app.post("/api/me/password")
def api_password(body: PasswordBody, request: Request, user=Depends(auth.current_user)):
    row = db.one("SELECT pw_hash FROM users WHERE id=?", (user["id"],))
    if not auth.verify_password(body.current, row["pw_hash"]):
        raise HTTPException(400, "Current password is wrong.")
    if body.new == body.current:
        raise HTTPException(400, "Choose a different password.")
    auth.check_password_policy(body.new)
    db.x("UPDATE users SET pw_hash=?, must_change=0 WHERE id=?", (auth.hash_password(body.new), user["id"]))
    auth.end_sessions(user["id"], keep_token=request.cookies.get(auth.COOKIE, ""))
    return {"ok": True}


class MeBody(BaseModel):
    email: str = Field(max_length=120)


@app.patch("/api/me")
def api_me_update(body: MeBody, user=Depends(active_user)):
    email = body.email.strip()
    if email and ("@" not in email or " " in email):
        raise HTTPException(400, "That does not look like an email address.")
    db.x("UPDATE users SET email=? WHERE id=?", (email, user["id"]))
    return {"ok": True}


@app.get("/api/config")
def api_config(user=Depends(active_user)):
    summary = market.provider_summary()
    return {
        "mode": settings.data_mode, "refresh": 2 if settings.demo else max(settings.quote_ttl, 2),
        "strip": TICKER_STRIP, "ranges": list(market.RANGES),
        "metrics": {k: {"label": v[0], "unit": v[1], "group": v[2]} for k, v in alerts.METRICS.items()},
        "ops": alerts.OPS,
        "paper": {"start_cash": paper.START_CASH, "max_leverage": paper.MAX_LEVERAGE,
                  "commission_bps": paper.COMMISSION_BPS, "option_fee": paper.OPTION_FEE,
                  "future_fee": paper.FUTURE_FEE, "tenors": list(paper.UST_TENORS)},
        "channels": {"app": True, "email": bool(settings.smtp_host),
                     "discord": bool(settings.discord_webhook), "slack": bool(settings.slack_webhook)},
        "providers": summary["providers"] if user["role"] == "admin" else
        [{"name": p["name"], "configured": p["configured"], "used_for": p["used_for"]} for p in summary["providers"]],
    }


# ----------------------------------------------------------------------------- market data

@app.get("/api/quotes")
async def api_quotes(symbols: str, user=Depends(active_user)):
    syms = [sym(s) for s in symbols.split(",") if s.strip()][:80]
    return await data(market.get_quotes(syms))


@app.get("/api/quote/{symbol}")
async def api_quote(symbol: str, user=Depends(active_user)):
    symbol = sym(symbol)
    quote, profile = await asyncio.gather(data(market.get_quote(symbol)), data(market.get_profile(symbol)))
    if not quote:
        raise HTTPException(404, f"No data found for {symbol}.")
    return {"quote": quote, "profile": profile, "kind": market.kind(symbol)}


@app.get("/api/history")
async def api_history(symbol: str, range: str = "1Y", format: str = "json", user=Depends(active_user)):
    symbol = sym(symbol)
    if range not in market.RANGES:
        raise HTTPException(400, "Unknown range.")
    hist = await data(market.get_history(symbol, range))
    if format != "csv":
        return hist
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["datetime_utc", "open", "high", "low", "close", "volume"])
    for b in hist["bars"]:
        stamp = dt.datetime.fromtimestamp(b["t"], dt.timezone.utc)
        w.writerow([stamp.strftime("%Y-%m-%d %H:%M" if hist["intraday"] else "%Y-%m-%d"),
                    b["o"], b["h"], b["l"], b["c"], b["v"]])
    name = "".join(c if c.isalnum() else "_" for c in symbol)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_{range}.csv"'})


@app.get("/api/screen/{name}")
async def api_screen(name: str, user=Depends(active_user)):
    scr = SCREENS.get(name)
    if not scr:
        raise HTTPException(404, "Unknown screen.")
    symbols = [s for _, rows in scr["groups"] for s, _ in rows]
    quotes = await data(market.get_quotes(symbols))
    return {"title": scr["title"], "quotes": quotes,
            "groups": [{"name": g, "rows": [{"symbol": s, "name": n} for s, n in rows]} for g, rows in scr["groups"]]}


@app.get("/api/rates")
async def api_rates(user=Depends(active_user)):
    curve, g7, em, policy = await asyncio.gather(
        market.fred_snapshot([(t, s) for t, s in US_CURVE]), market.fred_snapshot(G7_10Y),
        market.fred_snapshot(EM_10Y), market.fred_snapshot(POLICY_RATES))
    return clean({"curve": curve, "g7": g7, "em": em, "policy": policy})


@app.get("/api/credit")
async def api_credit(user=Depends(active_user)):
    return clean({"spreads": await market.fred_snapshot(CREDIT)})


@app.get("/api/options/{symbol}/expirations")
async def api_expirations(symbol: str, user=Depends(active_user)):
    return {"symbol": sym(symbol), "expirations": await data(market.get_expirations(sym(symbol)))}


@app.get("/api/options/{symbol}/chain")
async def api_chain(symbol: str, expiry: str, user=Depends(active_user)):
    try:
        dt.date.fromisoformat(expiry)
    except ValueError:
        raise HTTPException(400, "Expiry must be YYYY-MM-DD.")
    return await data(market.get_chain(sym(symbol), expiry))


@app.get("/api/news")
async def api_news(symbol: str = "", user=Depends(active_user)):
    return {"items": await data(market.get_news(sym(symbol) if symbol else None))}


@app.get("/api/search")
async def api_search(q: str, user=Depends(active_user)):
    return {"results": await data(market.search(q))}


@app.get("/api/metrics")
async def api_metrics(symbol: str, expiry: str = "", strike: float = 0, type: str = "", user=Depends(active_user)):
    contract = {"expiry": expiry, "strike": strike, "type": type} if expiry and type in ("C", "P") else None
    return await data(alerts.compute_metrics(sym(symbol), contract))


# ----------------------------------------------------------------------------- calendar

def central_bank_events(start, end):
    try:
        cfg = json.loads((BASE / "data" / "central_banks.json").read_text())
    except Exception:
        return []
    out = []
    for bank in cfg.get("banks", []):
        for d in bank.get("dates", []):
            if start <= d <= end:
                extra = " + projections" if d in bank.get("projections", []) else ""
                out.append({"date": d, "time": bank.get("time", ""), "category": "central_bank",
                            "title": f"{bank['bank']} rate decision{extra}", "code": bank.get("code")})
    return out


@app.get("/api/calendar")
async def api_calendar(start: str = "", end: str = "", user=Depends(active_user)):
    try:
        d0 = dt.date.fromisoformat(start) if start else dt.date.today()
        d1 = dt.date.fromisoformat(end) if end else d0 + dt.timedelta(days=45)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD.")
    if d1 < d0 or (d1 - d0).days > 400:
        raise HTTPException(400, "Choose a range of up to 400 days.")
    s0, s1 = d0.isoformat(), d1.isoformat()
    events = central_bank_events(s0, s1)
    watch = [r["symbol"] for r in db.q("SELECT symbol FROM watchlist WHERE user_id=?", (user["id"],))]
    earn_end = min(d1, d0 + dt.timedelta(days=30))
    earnings, macro = await asyncio.gather(market.get_earnings(d0, earn_end, tuple(watch)),
                                           market.get_macro_releases(d0, d1), return_exceptions=True)
    if isinstance(earnings, list):
        for e in earnings:
            est = f", EPS est {e['eps_est']:.2f}" if e.get("eps_est") is not None else ""
            when = {"bmo": " before open", "amc": " after close"}.get(e.get("when", ""), "")
            events.append({"date": e["date"], "time": when.strip(), "category": "earnings",
                           "title": f"{e['symbol']} earnings{est}", "symbol": e["symbol"],
                           "watch": e["symbol"] in watch})
    if isinstance(macro, list):
        events += [{"date": m["date"], "time": "", "category": "macro", "title": m["title"]} for m in macro]
    for row in db.q("SELECT c.*, u.username FROM calendar_events c LEFT JOIN users u ON u.id=c.created_by "
                    "WHERE date BETWEEN ? AND ?", (s0, s1)):
        events.append({"date": row["date"], "time": row["time"], "category": "custom", "title": row["title"],
                       "id": row["id"], "by": row["username"]})
    events.sort(key=lambda e: (e["date"], e["category"], e["title"]))
    return clean({"start": s0, "end": s1, "events": events})


class EventBody(BaseModel):
    date: str
    time: str = Field("", max_length=20)
    title: str = Field(min_length=1, max_length=160)


@app.post("/api/calendar")
def api_calendar_add(body: EventBody, user=Depends(active_user)):
    try:
        dt.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "Date must be YYYY-MM-DD.")
    return {"id": db.ins("INSERT INTO calendar_events(date,time,title,created_by) VALUES(?,?,?,?)",
                         (body.date, body.time.strip(), body.title.strip(), user["id"]))}


@app.delete("/api/calendar/{event_id}")
def api_calendar_delete(event_id: int, user=Depends(active_user)):
    row = db.one("SELECT created_by FROM calendar_events WHERE id=?", (event_id,))
    if not row:
        raise HTTPException(404, "No such event.")
    if row["created_by"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "Only the creator or an admin can delete this.")
    db.x("DELETE FROM calendar_events WHERE id=?", (event_id,))
    return {"ok": True}


# ----------------------------------------------------------------------------- watchlist

@app.get("/api/watchlist")
def api_watchlist(user=Depends(active_user)):
    return {"symbols": [r["symbol"] for r in
                        db.q("SELECT symbol FROM watchlist WHERE user_id=? ORDER BY pos, symbol", (user["id"],))]}


class SymbolBody(BaseModel):
    symbol: str = Field(max_length=24)


@app.post("/api/watchlist")
def api_watchlist_add(body: SymbolBody, user=Depends(active_user)):
    symbol = sym(body.symbol)
    count = db.one("SELECT COUNT(*) n, COALESCE(MAX(pos),0) p FROM watchlist WHERE user_id=?", (user["id"],))
    if count["n"] >= 100:
        raise HTTPException(400, "Watchlist is limited to 100 symbols.")
    db.x("INSERT OR IGNORE INTO watchlist(user_id,symbol,pos) VALUES(?,?,?)", (user["id"], symbol, count["p"] + 1))
    return {"ok": True}


@app.delete("/api/watchlist/{symbol}")
def api_watchlist_remove(symbol: str, user=Depends(active_user)):
    db.x("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (user["id"], sym(symbol)))
    return {"ok": True}


# ----------------------------------------------------------------------------- alarms

class ContractBody(BaseModel):
    expiry: str
    strike: float
    type: str


class AlertBody(BaseModel):
    symbol: str = Field(max_length=24)
    metric: str
    op: str
    threshold: float
    note: str = Field("", max_length=200)
    channels: list[str] = ["app"]
    repeat: bool = False
    cooldown_min: int = Field(30, ge=1, le=10080)
    contract: ContractBody | None = None


def alert_view(row):
    row = dict(row)
    row["contract"] = json.loads(row["contract"]) if row.get("contract") else None
    row["channels"] = json.loads(row["channels"] or "[]")
    row["description"] = alerts.describe({**row, "contract": json.dumps(row["contract"]) if row["contract"] else None})
    row["unit"] = alerts.METRICS[row["metric"]][1]
    return row


@app.get("/api/alerts")
def api_alerts(user=Depends(active_user)):
    rows = db.q("SELECT * FROM alerts WHERE user_id=? ORDER BY status='active' DESC, id DESC", (user["id"],))
    return {"alerts": clean([alert_view(r) for r in rows])}


@app.post("/api/alerts")
async def api_alert_create(body: AlertBody, user=Depends(active_user)):
    symbol = sym(body.symbol)
    if body.metric not in alerts.METRICS or body.op not in alerts.OPS:
        raise HTTPException(400, "Unknown metric or condition.")
    if not math.isfinite(body.threshold):
        raise HTTPException(400, "Threshold must be a number.")
    channels = [c for c in dict.fromkeys(body.channels) if c in alerts.CHANNELS] or ["app"]
    is_option = alerts.METRICS[body.metric][2] == "option"
    contract = None
    if is_option:
        if not body.contract or body.contract.type not in ("C", "P"):
            raise HTTPException(400, "Option metrics need an expiry, strike and call/put.")
        try:
            dt.date.fromisoformat(body.contract.expiry)
        except ValueError:
            raise HTTPException(400, "Expiry must be YYYY-MM-DD.")
        contract = {"expiry": body.contract.expiry, "strike": body.contract.strike, "type": body.contract.type}
    if db.one("SELECT COUNT(*) n FROM alerts WHERE user_id=?", (user["id"],))["n"] >= 300:
        raise HTTPException(400, "Limit of 300 alarms per analyst. Delete old ones first.")
    try:
        values = await alerts.compute_metrics(symbol, contract, {body.metric})
    except Exception as e:
        raise HTTPException(400, f"Could not read {symbol}: {str(e)[:150]}")
    current = values.get(body.metric)
    if current is None:
        raise HTTPException(400, "That metric has no data for this instrument right now.")
    alert_id = db.ins(
        "INSERT INTO alerts(user_id,symbol,contract,metric,op,threshold,note,channels,repeat,cooldown_min,"
        "last_value,last_checked,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user["id"], symbol, json.dumps(contract, sort_keys=True) if contract else None, body.metric, body.op,
         body.threshold, body.note.strip(), json.dumps(channels), 1 if body.repeat else 0, body.cooldown_min,
         current if body.op.startswith("cross") else None, time.time(), time.time()))
    return {"id": alert_id, "current": clean(current)}


class AlertPatch(BaseModel):
    action: str


@app.patch("/api/alerts/{alert_id}")
def api_alert_patch(alert_id: int, body: AlertPatch, user=Depends(active_user)):
    row = db.one("SELECT * FROM alerts WHERE id=? AND user_id=?", (alert_id, user["id"]))
    if not row:
        raise HTTPException(404, "No such alarm.")
    if body.action == "pause":
        db.x("UPDATE alerts SET status='paused' WHERE id=?", (alert_id,))
    elif body.action in ("resume", "rearm"):
        db.x("UPDATE alerts SET status='active', cooldown_until=0, last_value=NULL, last_error=NULL WHERE id=?",
             (alert_id,))
    else:
        raise HTTPException(400, "Unknown action.")
    return {"ok": True}


@app.delete("/api/alerts/{alert_id}")
def api_alert_delete(alert_id: int, user=Depends(active_user)):
    if not db.x("DELETE FROM alerts WHERE id=? AND user_id=?", (alert_id, user["id"])):
        raise HTTPException(404, "No such alarm.")
    return {"ok": True}


@app.get("/api/alerts/events")
def api_alert_events(after: int = 0, user=Depends(active_user)):
    rows = db.q("SELECT e.*, a.symbol FROM alert_events e LEFT JOIN alerts a ON a.id=e.alert_id "
                "WHERE e.user_id=? AND e.id>? ORDER BY e.id DESC LIMIT 100", (user["id"], after))
    unseen = db.one("SELECT COUNT(*) n FROM alert_events WHERE user_id=? AND seen=0", (user["id"],))["n"]
    return {"events": clean(rows), "unseen": unseen}


@app.post("/api/alerts/events/ack")
def api_alert_ack(user=Depends(active_user)):
    db.x("UPDATE alert_events SET seen=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}


# ----------------------------------------------------------------------------- paper trading

def book_owner(user, user_id):
    """Whose book a request is about. Only the CIO/admin may look at someone else's."""
    if not user_id or user_id == user["id"]:
        return user["id"]
    if user["role"] != "admin":
        raise HTTPException(403, "You can only see your own portfolio.")
    if not db.one("SELECT id FROM users WHERE id=?", (user_id,)):
        raise HTTPException(404, "No such user.")
    return user_id


async def trade(coro):
    try:
        return clean(await coro)
    except paper.TradeError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.warning("paper error: %s", e)
        raise HTTPException(502, f"Could not price that instrument: {str(e)[:160]}")


class OrderBody(BaseModel):
    symbol: str = Field("", max_length=24)
    side: str
    qty: float
    type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    contract: ContractBody | None = None
    note: str = Field("", max_length=300)
    position_id: int | None = None


@app.get("/api/paper/portfolio")
async def api_paper_portfolio(user_id: int = 0, user=Depends(active_user)):
    owner = book_owner(user, user_id)
    port = await trade(paper.portfolio(owner))
    port["stats"] = clean(paper.stats(owner))
    port["owner"] = db.one("SELECT id, username, role FROM users WHERE id=?", (owner,))
    port["mine"] = owner == user["id"]
    return port


@app.get("/api/paper/equity")
def api_paper_equity(user_id: int = 0, user=Depends(active_user)):
    return {"points": paper.equity_curve(book_owner(user, user_id))}


@app.get("/api/paper/orders")
def api_paper_orders(user_id: int = 0, user=Depends(active_user)):
    return {"orders": clean(paper.orders(book_owner(user, user_id)))}


async def _order_inst(user, body):
    if body.position_id:
        pos = db.one("SELECT inst FROM paper_positions WHERE id=? AND user_id=?", (body.position_id, user["id"]))
        if not pos:
            raise paper.TradeError("That position no longer exists.")
        return json.loads(pos["inst"])
    contract = body.contract.model_dump() if body.contract else None
    return await paper.resolve(body.symbol, contract)


@app.post("/api/paper/preview")
async def api_paper_preview(body: OrderBody, user=Depends(active_user)):
    async def go():
        inst = await _order_inst(user, body)
        qty = body.qty if body.qty and body.qty > 0 else 0.0
        return await paper.assess(user["id"], inst, body.side if body.side in ("buy", "sell") else "buy", qty)
    return await trade(go())


@app.post("/api/paper/orders")
async def api_paper_order(body: OrderBody, user=Depends(active_user)):
    contract = body.contract.model_dump() if body.contract else None
    order = await trade(paper.place_order(user["id"], body.symbol, body.side, body.qty, body.type, body.limit_price,
                                          body.stop_price, contract, body.note, body.position_id))
    order.pop("inst", None)
    return order


@app.delete("/api/paper/orders/{order_id}")
def api_paper_cancel(order_id: int, user=Depends(active_user)):
    if not paper.cancel_order(user["id"], order_id):
        raise HTTPException(404, "No open order with that number.")
    return {"ok": True}


@app.get("/api/paper/firm")
async def api_paper_firm(user=Depends(admin)):
    return await trade(paper.firm())


@app.post("/api/paper/reset/{user_id}")
def api_paper_reset(user_id: int, user=Depends(admin)):
    if not db.one("SELECT id FROM users WHERE id=?", (user_id,)):
        raise HTTPException(404, "No such user.")
    paper.reset_account(user_id)
    return {"ok": True}


# ----------------------------------------------------------------------------- admin

class UserBody(BaseModel):
    username: str = Field(max_length=32)
    email: str = Field("", max_length=120)
    role: str = "analyst"
    password: str = Field("", max_length=256)


def temp_password():
    return "Aug-" + secrets.token_urlsafe(9) + "7"


@app.get("/api/admin/users")
def api_users(user=Depends(admin)):
    rows = db.q("SELECT id,username,email,role,active,must_change,created,last_login,locked_until FROM users ORDER BY id")
    counts = {r["user_id"]: r["n"] for r in db.q("SELECT user_id, COUNT(*) n FROM alerts GROUP BY user_id")}
    for r in rows:
        r["alarms"] = counts.get(r["id"], 0)
    return {"users": rows}


@app.post("/api/admin/users")
def api_user_create(body: UserBody, user=Depends(admin)):
    password = body.password or temp_password()
    uid = auth.create_user(body.username.strip(), password, body.role, body.email.strip(), must_change=True)
    seed_watchlist(uid)
    paper.ensure_account(uid)
    return {"id": uid, "username": body.username.strip(), "temporary_password": password}


class UserPatch(BaseModel):
    action: str
    role: str = ""


@app.patch("/api/admin/users/{user_id}")
def api_user_patch(user_id: int, body: UserPatch, user=Depends(admin)):
    target = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        raise HTTPException(404, "No such user.")

    def other_admins():
        return db.one("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,))["n"]
    if body.action == "reset_password":
        password = temp_password()
        db.x("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked_until=0 WHERE id=?",
             (auth.hash_password(password), user_id))
        auth.end_sessions(user_id)
        return {"temporary_password": password}
    if body.action == "deactivate":
        if target["role"] == "admin" and not other_admins():
            raise HTTPException(400, "This is the only active admin.")
        db.x("UPDATE users SET active=0 WHERE id=?", (user_id,))
        auth.end_sessions(user_id)
    elif body.action == "activate":
        db.x("UPDATE users SET active=1, failed=0, locked_until=0 WHERE id=?", (user_id,))
    elif body.action == "set_role":
        if body.role not in ("analyst", "admin"):
            raise HTTPException(400, "Role must be analyst or admin.")
        if target["role"] == "admin" and body.role != "admin" and not other_admins():
            raise HTTPException(400, "This is the only active admin.")
        db.x("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
    else:
        raise HTTPException(400, "Unknown action.")
    return {"ok": True}


class NotifyBody(BaseModel):
    channel: str


@app.post("/api/admin/test-notify")
async def api_test_notify(body: NotifyBody, user=Depends(admin)):
    if body.channel not in ("email", "discord", "slack"):
        raise HTTPException(400, "Unknown channel.")
    result = await alerts.notify([body.channel], "Augusta Terminal test",
                                 f"Test notification sent by {user['username']}.", user.get("email"))
    return {"result": result.get(body.channel)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
