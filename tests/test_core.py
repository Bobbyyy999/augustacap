"""Run with:  python -m pytest -q     (uses simulated data and a throwaway database)"""
import asyncio
import math
import os
import tempfile

os.environ["DATA_MODE"] = "demo"
os.environ["AUGUSTA_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "Admin-pass-123"

from fastapi.testclient import TestClient  # noqa: E402

from app import alerts, db, greeks, market  # noqa: E402
from app.main import app  # noqa: E402

H = {"X-Requested-With": "augusta"}


def login(c, user, pw):
    return c.post("/api/login", json={"username": user, "password": pw}, headers=H)


def fresh_admin(c):
    assert login(c, "admin", "Admin-pass-123").status_code == 200
    r = c.post("/api/me/password", json={"current": "Admin-pass-123", "new": "Better-pass-456"}, headers=H)
    assert r.status_code == 200, r.text


def test_greeks_match_textbook_values():
    # Hull-style check: S=100 K=100 T=1 r=5% q=0 vol=20% -> call 10.4506, put 5.5735
    assert abs(greeks.bs_price(100, 100, 1, 0.05, 0, 0.2, "C") - 10.4506) < 1e-3
    assert abs(greeks.bs_price(100, 100, 1, 0.05, 0, 0.2, "P") - 5.5735) < 1e-3
    g = greeks.greeks(100, 100, 1, 0.05, 0, 0.2, "C")
    assert abs(g["delta"] - 0.6368) < 1e-3 and abs(g["gamma"] - 0.01876) < 1e-4
    assert abs(g["vega"] - 0.3752) < 1e-3 and abs(g["theta"] * 365 + 6.414) < 1e-2
    p = greeks.greeks(100, 100, 1, 0.05, 0, 0.2, "P")
    assert abs(p["delta"] - (g["delta"] - 1)) < 1e-9          # put-call parity for delta
    iv = greeks.implied_vol(10.4506, 100, 100, 1, 0.05, 0, "C")
    assert abs(iv - 0.2) < 1e-4
    assert greeks.implied_vol(0.5, 100, 50, 1, 0.05, 0, "C") is None   # below intrinsic


def test_indicators():
    up = [float(i) for i in range(1, 40)]
    assert alerts.rsi(up) == 100.0
    assert alerts.sma([1, 2, 3, 4], 2) == 3.5
    assert abs(alerts.zscore([0.0] * 19 + [1.0]) - math.sqrt(19)) < 1e-9
    assert alerts.check(">=", 10, None, 10) and not alerts.check("<=", 10, None, 11)
    assert alerts.check("cross_up", 10, 9, 10.5) and not alerts.check("cross_up", 10, 10.2, 10.5)
    assert alerts.check("cross_down", 10, 11, 9) and not alerts.check("cross_down", 10, None, 9)


def test_login_roles_and_lockout():
    with TestClient(app) as c:
        assert c.get("/api/quotes?symbols=AAPL").status_code == 401
        assert c.get("/", follow_redirects=False).status_code == 307
        assert c.post("/api/login", json={"username": "admin", "password": "x"}).status_code == 403  # no header
        assert login(c, "admin", "wrong").status_code == 401
        assert login(c, "admin", "Admin-pass-123").status_code == 200
        # Temporary password must be changed before any data is served.
        assert c.get("/api/quotes?symbols=AAPL").status_code == 403
        assert c.post("/api/me/password", json={"current": "Admin-pass-123", "new": "short"}, headers=H).status_code == 400
        assert c.post("/api/me/password", json={"current": "Admin-pass-123", "new": "Better-pass-456"}, headers=H).status_code == 200
        assert c.get("/api/quotes?symbols=AAPL").status_code == 200
        made = c.post("/api/admin/users", json={"username": "priya", "email": "p@example.com"}, headers=H).json()
        assert made["temporary_password"]
    with TestClient(app) as a:
        assert login(a, "priya", made["temporary_password"]).status_code == 200
        a.post("/api/me/password", json={"current": made["temporary_password"], "new": "Analyst-pass-789"}, headers=H)
        assert a.get("/api/admin/users").status_code == 403
        assert a.get("/api/watchlist").json()["symbols"]
    with TestClient(app) as b:
        for _ in range(5):
            assert login(b, "priya", "nope").status_code == 401
        assert login(b, "priya", "Analyst-pass-789").status_code == 429
    db.x("UPDATE users SET locked_until=0, failed=0")


def test_market_endpoints():
    with TestClient(app) as c:
        assert login(c, "admin", "Better-pass-456").status_code == 200
        q = c.get("/api/quotes?symbols=AAPL,EURUSD=X,GC=F,FRED:DGS10").json()
        assert set(q) == {"AAPL", "EURUSD=X", "GC=F", "FRED:DGS10"} and q["AAPL"]["price"] > 0
        for name in ("wei", "fx", "fut", "cmdty", "bond_etfs"):
            assert c.get(f"/api/screen/{name}").json()["quotes"]
        for rng in ("1D", "5D", "1M", "YTD", "1Y", "MAX"):
            assert len(c.get(f"/api/history?symbol=AAPL&range={rng}").json()["bars"]) > 5
        csv_text = c.get("/api/history?symbol=AAPL&range=1M&format=csv").text
        assert csv_text.startswith("datetime_utc,open") and csv_text.count("\n") > 10
        rates = c.get("/api/rates").json()
        assert len(rates["curve"]) == 11 and rates["g7"][0]["value"] and rates["em"]
        assert c.get("/api/credit").json()["spreads"][0]["value"]
        exps = c.get("/api/options/AAPL/expirations").json()["expirations"]
        chain = c.get(f"/api/options/AAPL/chain?expiry={exps[2]}").json()
        calls = [r for r in chain["rows"] if r["type"] == "C" and r["delta"] is not None]
        assert calls and all(0 <= r["delta"] <= 1 for r in calls)
        deltas = [r["delta"] for r in sorted(calls, key=lambda r: r["strike"])]
        assert deltas == sorted(deltas, reverse=True)           # call delta falls as strike rises
        assert all(-1 <= r["delta"] <= 0 for r in chain["rows"] if r["type"] == "P" and r["delta"] is not None)
        assert c.get("/api/news").json()["items"]
        cal = c.get("/api/calendar?start=2026-12-01&end=2027-02-15").json()["events"]
        assert any(e["category"] == "central_bank" and e["date"] == "2026-12-09" for e in cal)
        assert c.get("/api/quotes?symbols=<script>").status_code == 400


def test_alarms_fire_once_and_respect_ownership():
    with TestClient(app) as c:
        login(c, "admin", "Better-pass-456")
        price = c.get("/api/quotes?symbols=AAPL").json()["AAPL"]["price"]
        r = c.post("/api/alerts", json={"symbol": "AAPL", "metric": "price", "op": ">=", "threshold": price * 0.5}, headers=H)
        assert r.status_code == 200, r.text
        far = c.post("/api/alerts", json={"symbol": "AAPL", "metric": "rsi14", "op": ">=", "threshold": 101}, headers=H).json()["id"]
        exps = c.get("/api/options/AAPL/expirations").json()["expirations"]
        strike = c.get(f"/api/options/AAPL/chain?expiry={exps[1]}").json()["rows"][20]["strike"]
        opt = c.post("/api/alerts", json={"symbol": "AAPL", "metric": "delta", "op": "<=", "threshold": 2,
                                          "contract": {"expiry": exps[1], "strike": strike, "type": "C"}}, headers=H)
        assert opt.status_code == 200, opt.text
        assert c.post("/api/alerts", json={"symbol": "AAPL", "metric": "delta", "op": "<=", "threshold": 1}, headers=H).status_code == 400
        fired = asyncio.run(alerts.evaluate_once())
        assert len(fired) == 2
        assert asyncio.run(alerts.evaluate_once()) == []          # one-shot alarms do not re-fire
        ev = c.get("/api/alerts/events").json()
        assert ev["unseen"] == 2 and "AAPL" in ev["events"][0]["message"]
        states = {a["id"]: a["status"] for a in c.get("/api/alerts").json()["alerts"]}
        assert states[far] == "active" and list(states.values()).count("triggered") == 2
        c.post("/api/alerts/events/ack", headers=H)
        assert c.get("/api/alerts/events").json()["unseen"] == 0
    with TestClient(app) as other:
        login(other, "priya", "Analyst-pass-789")
        assert other.get("/api/alerts").json()["alerts"] == []
        assert other.delete(f"/api/alerts/{far}", headers=H).status_code == 404


def test_symbol_mapping():
    assert market.td_symbol("EURUSD=X") == "EUR/USD" and market.td_symbol("JPY=X") == "USD/JPY"
    assert market.td_symbol("BTC-USD") == "BTC/USD" and market.td_symbol("AAPL") == "AAPL"
    assert market.td_symbol("^GSPC") is None and market.td_symbol("CL=F") is None
    assert market.kind("7203.T") == "intl" and market.kind("FRED:DGS10") == "fred"


def test_live_provider_parsing_with_canned_responses(monkeypatch):
    """The live vendors cannot be called from a test, so feed each parser a response in the vendor's format."""
    import httpx
    import pandas as pd

    from app import providers as P

    # Yahoo batch download frame: columns (ticker, field), one row per day.
    idx = pd.to_datetime(["2026-11-06", "2026-11-09"])
    cols = pd.MultiIndex.from_product([["AAPL", "BAD"], ["Open", "High", "Low", "Close", "Volume"]])
    frame = pd.DataFrame([[1, 2, 0.5, 100.0, 10, None, None, None, None, None],
                          [1, 2, 0.5, 110.0, 20, None, None, None, None, None]], index=idx, columns=cols)

    class FakeYF:
        @staticmethod
        def download(*a, **k):
            return frame
    monkeypatch.setattr(P, "_yf", lambda: FakeYF)
    q = P.yahoo_quotes_sync(["AAPL", "BAD"])
    assert list(q) == ["AAPL"] and q["AAPL"]["price"] == 110.0 and abs(q["AAPL"]["pct"] - 10) < 1e-9
    assert q["AAPL"]["delayed"] and q["AAPL"]["session"] == "2026-11-09"

    def handler(request):
        url = str(request.url)
        if "twelvedata.com/quote" in url:
            return httpx.Response(200, json={
                "AAPL": {"symbol": "AAPL", "name": "Apple Inc", "close": "271.50", "previous_close": "270.00",
                         "open": "270.5", "high": "272", "low": "269", "volume": "1000", "last_quote_at": 1794350000},
                "EUR/USD": {"code": 404, "status": "error", "message": "not found"}})
        if "time_series" in url:
            return httpx.Response(200, json={"status": "ok", "values": [
                {"datetime": "2026-11-10 14:30:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "7"}]})
        if "snapshot/options" in url:
            return httpx.Response(200, json={"results": [{
                "details": {"contract_type": "put", "strike_price": 270, "ticker": "O:AAPL261218P00270000"},
                "greeks": {"delta": -0.45, "gamma": 0.03, "theta": -0.1, "vega": 0.2}, "implied_volatility": 0.22,
                "open_interest": 55, "day": {"close": 5.1, "volume": 9}, "underlying_asset": {"price": 271.0}}]})
        if "calendar/earnings" in url:
            return httpx.Response(200, json={"earningsCalendar": [{"date": "2026-11-19", "symbol": "NVDA", "hour": "amc", "epsEstimate": 1.2}]})
        if "fredgraph.csv" in url:
            return httpx.Response(200, text="observation_date,DGS10\n2026-11-05,4.10\n2026-11-06,.\n2026-11-09,4.12\n")
        return httpx.Response(404, json={})
    monkeypatch.setattr(P, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def go():
        td = await P.td_quotes({"AAPL": "AAPL", "EURUSD=X": "EUR/USD"})
        assert list(td) == ["AAPL"] and td["AAPL"]["price"] == 271.5 and not td["AAPL"]["delayed"]
        bars = await P.td_history("AAPL", "5min", 10)
        assert bars[0]["c"] == 1.5 and bars[0]["t"] == 1794321000
        rows, spot = await P.massive_chain("AAPL", "2026-12-18")
        assert rows[0]["type"] == "P" and rows[0]["delta"] == -0.45 and spot == 271.0
        assert (await P.finnhub_earnings(__import__("datetime").date(2026, 11, 10), __import__("datetime").date(2026, 12, 1)))[0]["symbol"] == "NVDA"
        assert await P.fred_series("DGS10") == [("2026-11-05", 4.1), ("2026-11-09", 4.12)]
    asyncio.run(go())

    rss = b"""<rss><channel><item><title>Fed statement</title><link>https://example.org/a</link>
              <pubDate>Tue, 10 Nov 2026 14:00:00 GMT</pubDate></item></channel></rss>"""
    item = P.parse_feed(rss, "Fed")[0]
    assert item["title"] == "Fed statement" and item["url"] == "https://example.org/a" and item["t"] == 1794319200
    assert P.parse_feed(b"<!DOCTYPE x [<!ENTITY a 'b'>]><rss/>", "x") == []


def test_paper_maths():
    from app import paper
    assert paper.fill_math(0, 0, 100, 10) == (100, 10, 0.0)
    assert paper.fill_math(100, 10, 100, 20) == (200, 15, 0.0)
    assert paper.fill_math(100, 10, -40, 12) == (60, 10, 80.0)              # partial close, long
    assert paper.fill_math(100, 10, -150, 12) == (-50, 12, 200.0)           # close and flip short
    assert paper.fill_math(-50, 12, 50, 9) == (0.0, 0.0, 150.0)             # cover a short at a profit
    assert abs(paper.bond_price(4.0, 4.0, 10) - 100) < 1e-9                 # par bond
    assert abs(paper.bond_price(4.0, 5.0, 10) - 92.2054) < 1e-3             # textbook: 4% 10y at 5% yield
    assert paper.bond_price(4.0, 3.0, 10) > 100
    assert paper.interp([(1, 3.0), (2, 4.0)], 1.5) == 3.5 and paper.interp([(1, 3.0), (2, 4.0)], 9) == 4.0
    assert abs(paper.max_drawdown([100, 120, 90, 130]) + 25) < 1e-9


def test_paper_trading_flow():
    from app import paper
    with TestClient(app) as c:
        login(c, "admin", "Better-pass-456")
        s = c.get("/api/paper/portfolio").json()["summary"]
        assert s["equity"] == 1_000_000 and s["cash"] == 1_000_000 and s["positions"] == 0

        # stock: buy 1,000 shares, cash falls by cost + 1 bp commission
        o = c.post("/api/paper/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1000, "note": "test thesis"}, headers=H).json()
        assert o["status"] == "filled", o
        cost = 1000 * o["fill_price"]
        s = c.get("/api/paper/portfolio").json()["summary"]
        assert abs(s["cash"] - (1_000_000 - cost - cost * 1e-4)) < 0.01 and abs(o["fee"] - cost * 1e-4) < 1e-6
        assert abs(s["equity"] - 1_000_000) < cost * 0.01          # NAV only moves by fee and spread

        # sell 1,500: closes the long and leaves 500 short
        o2 = c.post("/api/paper/orders", json={"symbol": "AAPL", "side": "sell", "qty": 1500}, headers=H).json()
        pos = {p["symbol"]: p for p in c.get("/api/paper/portfolio").json()["positions"]}
        assert pos["AAPL"]["qty"] == -500 and pos["AAPL"]["value"] < 0 and pos["AAPL"]["exposure"] < 0

        # future: margin product, cash only pays the per-contract fee
        cash0 = c.get("/api/paper/portfolio").json()["summary"]["cash"]
        f = c.post("/api/paper/orders", json={"symbol": "ES=F", "side": "buy", "qty": 2}, headers=H).json()
        port = c.get("/api/paper/portfolio").json()
        assert f["status"] == "filled" and abs(port["summary"]["cash"] - (cash0 - 5.0)) < 1e-6
        es = next(p for p in port["positions"] if p["symbol"] == "ES=F")
        assert es["mult"] == 50 and abs(es["exposure"] - 2 * es["price"] * 50) < 1 and es["margin"]

        # FX in a non-USD quote currency, an option, and a Treasury bond
        assert c.post("/api/paper/orders", json={"symbol": "USDJPY=X", "side": "sell", "qty": 100000}, headers=H).json()["status"] == "filled"
        exps = c.get("/api/options/NVDA/expirations").json()["expirations"]
        strike = c.get(f"/api/options/NVDA/chain?expiry={exps[3]}").json()["rows"][24]["strike"]
        opt = c.post("/api/paper/orders", json={"symbol": "NVDA", "side": "buy", "qty": 10,
                                                "contract": {"expiry": exps[3], "strike": strike, "type": "C"}}, headers=H).json()
        assert opt["status"] == "filled" and abs(opt["fee"] - 6.5) < 1e-9
        b = c.post("/api/paper/orders", json={"symbol": "UST:10Y", "side": "buy", "qty": 500000}, headers=H).json()
        assert b["status"] == "filled" and abs(b["fill_price"] - 100) < 0.05
        port = c.get("/api/paper/portfolio").json()
        kinds = {p["kind"] for p in port["positions"]}
        assert kinds == {"equity", "future", "fx", "option", "bond"}
        bond = next(p for p in port["positions"] if p["kind"] == "bond")
        assert abs(bond["value"] - 500000) < 500 and 350 < port["greeks"]["dv01_usd"] < 500
        jpy = next(p for p in port["positions"] if p["kind"] == "fx")
        assert jpy["ccy"] == "JPY" and abs(jpy["exposure"] + 100000) < 1     # short 100k USD, valued in USD
        assert {e["asset_class"] for e in port["exposure"]} >= {"Equity", "Equity index", "FX", "Options", "Rates"}
        # NAV identity: equity = cash + cash-product values + open P&L of margin products
        assert abs(port["summary"]["equity"] - (port["summary"]["cash"] + sum(p["value"] for p in port["positions"]))) < 0.01

        # things that must be refused
        assert c.post("/api/paper/orders", json={"symbol": "^GSPC", "side": "buy", "qty": 1}, headers=H).status_code == 400
        assert c.post("/api/paper/orders", json={"symbol": "FRED:DGS10", "side": "buy", "qty": 1}, headers=H).status_code == 400
        assert c.post("/api/paper/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1.5}, headers=H).status_code == 400
        big = c.post("/api/paper/orders", json={"symbol": "ES=F", "side": "buy", "qty": 40}, headers=H).json()
        assert big["status"] == "rejected" and "limit" in big["reason"]
        pv = c.post("/api/paper/preview", json={"symbol": "MSFT", "side": "sell", "qty": 10}, headers=H).json()
        assert pv["ok"] and "short" in pv["warning"]

        # resting limit order: far from the market it waits, the engine fills it once the price is through
        px = c.get("/api/quotes?symbols=MSFT").json()["MSFT"]["price"]
        lo = c.post("/api/paper/orders", json={"symbol": "MSFT", "side": "buy", "qty": 10, "type": "limit", "limit_price": px * 0.5}, headers=H).json()
        assert lo["status"] == "open"
        asyncio.run(paper.cycle())
        assert db.one("SELECT status FROM paper_orders WHERE id=?", (lo["id"],))["status"] == "open"
        db.x("UPDATE paper_orders SET limit_price=? WHERE id=?", (px * 1.5, lo["id"]))
        asyncio.run(paper.cycle())
        done = db.one("SELECT * FROM paper_orders WHERE id=?", (lo["id"],))
        assert done["status"] == "filled" and done["fill_price"] < px * 1.5
        assert any("Order filled" in e["message"] for e in c.get("/api/alerts/events").json()["events"])
        st = c.post("/api/paper/orders", json={"symbol": "GC=F", "side": "sell", "qty": 1, "type": "stop", "stop_price": 1}, headers=H).json()
        assert st["status"] == "open" and c.delete(f"/api/paper/orders/{st['id']}", headers=H).status_code == 200

        # closing by position id (how the Close button works) flattens the line
        c.post("/api/paper/orders", json={"side": "sell", "qty": 500000, "position_id": bond["id"]}, headers=H)
        assert not any(p["kind"] == "bond" for p in c.get("/api/paper/portfolio").json()["positions"])

        # expired option cash-settles at intrinsic value
        db.x("UPDATE paper_positions SET inst=replace(inst, ?, '2020-01-17'), pos_key=replace(pos_key, ?, '2020-01-17') "
             "WHERE pos_key LIKE 'NVDA|%'", (exps[3], exps[3]))
        asyncio.run(paper.cycle())
        assert not any(p["kind"] == "option" for p in c.get("/api/paper/portfolio").json()["positions"])
        assert any(o["type"] == "settlement" for o in c.get("/api/paper/orders").json()["orders"])

        firm = c.get("/api/paper/firm").json()
        assert {a["username"] for a in firm["analysts"]} == {"admin", "priya"} and firm["total"]["starting"] == 2_000_000
        priya = next(a for a in firm["analysts"] if a["username"] == "priya")
        assert c.get(f"/api/paper/portfolio?user_id={priya['user_id']}").json()["mine"] is False
    with TestClient(app) as a:
        login(a, "priya", "Analyst-pass-789")
        assert a.get("/api/paper/portfolio").json()["summary"]["equity"] == 1_000_000
        assert a.get("/api/paper/portfolio?user_id=1").status_code == 403
        assert a.get("/api/paper/firm").status_code == 403 and a.post("/api/paper/reset/1", headers=H).status_code == 403
    with TestClient(app) as c:
        login(c, "admin", "Better-pass-456")
        assert c.post("/api/paper/reset/1", headers=H).status_code == 200
        s = c.get("/api/paper/portfolio").json()
        assert s["summary"]["equity"] == 1_000_000 and s["positions"] == [] and c.get("/api/paper/orders").json()["orders"] == []
