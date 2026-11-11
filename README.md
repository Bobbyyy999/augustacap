# Augusta Terminal

Internal market terminal and paper-trading desk for Augusta Capital Management. This README is the user manual; the developer notes are in section 11.

Manual as of 10 November 2026.

## 1. What the terminal is

Augusta Terminal is a private website the fund runs on its own machine: one screen for prices across asset classes, news, calendars and alarms, with a separate login for each analyst. It places no real trades; each analyst practises in a $1,000,000 paper account (section 9).

| Asset class | Screen (command) | Where the data comes from | How fresh |
| --- | --- | --- | --- |
| US equities and ETFs | type the ticker, e.g. `AAPL` | Twelve Data with a key, otherwise Yahoo Finance | Real time with Twelve Data; about 15 minutes delayed on Yahoo |
| Global equities and indices | `WEI`, or a ticker such as `7203.T` | Yahoo Finance | Delayed 15 to 20 minutes, varies by exchange |
| FX (G10, EM) and crypto | `FX` | Twelve Data with a key, otherwise Yahoo Finance | Real time with Twelve Data |
| US Treasury curve | `RATES` | FRED | Daily close, published the next business day |
| G7 and EM 10-year government yields | `RATES` | FRED (OECD series) | Monthly averages, except the US |
| Corporate credit spreads (IG, HY, by rating, EM) | `CREDIT` | FRED (ICE BofA indices) | Daily |
| Bond and credit ETFs | `RATES`, `CREDIT` | Same as US equities | Same as US equities |
| Futures: index, rates, currency | `FUT` | Yahoo Finance, front month | Delayed about 10 to 15 minutes |
| Commodities and metals | `CMDTY` | Yahoo Finance, front-month futures | Delayed about 10 to 15 minutes |
| Options with greeks | `AAPL OPT` | Massive with a key, otherwise Yahoo Finance | 15 minutes delayed on entry plans |
| News | `NEWS`, `AAPL N` | Finnhub with a key, plus central bank and market RSS feeds | Minutes |
| Earnings dates | `CAL` | Finnhub with a key, otherwise Yahoo for your watchlist and 20 large caps | Daily |
| Central bank meetings | `CAL` | Each bank's published schedule, stored in a file you can edit | Through December 2027 |
| Any economic series | `FRED CPIAUCSL` | FRED | As published |

Section 3 explains which paid keys make which rows real time, and what no affordable feed covers.

## 2. Setting it up (admin, once)

One person installs the terminal on one computer; everyone else just opens a web address. You need Python 3.10 or newer.

1. Clone this repository and open a terminal window in its folder.
2. Create an isolated Python environment and install the requirements:
   - Mac or Linux: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
   - Windows: `python -m venv .venv`, then `.venv\Scripts\activate`, then `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`. To try it first with no keys and no internet, set `DATA_MODE=demo` in that file. Demo prices are simulated and every screen says so.
4. Start it with `python run.py` and open `http://127.0.0.1:8000`.
5. The console prints the admin username and a temporary password once. Sign in; the terminal makes you replace the password before it shows any data.
6. Open `ADMIN`, create an account for each analyst, and pass each temporary password on privately.
7. When you are ready for real data, set `DATA_MODE=live`, add your keys (section 3) and restart.

### Sharing it with the team

Out of the box the terminal only answers on the computer it runs on. To give the team access, keep it running on one machine and reach it over a private network.

- Easiest: install Tailscale (a free private VPN) on the host and on each analyst's laptop, set `HOST=0.0.0.0` in `.env`, and open `http://<host's Tailscale name>:8000`.
- On campus or shared Wi-Fi, do not use `HOST=0.0.0.0` without a VPN: anyone on that network could reach the login page.
- If you later put it behind HTTPS (Caddy does this in two lines), set `AUGUSTA_HTTPS=1` so the login cookie is only sent over HTTPS.
- Never open the port to the public internet. The data licences in section 3 and the word "internal" both depend on it.

Everything the terminal stores (accounts, watchlists, alarms, team events) is in one file, `data/augusta.db`. Copy that file to back it up.

## 3. Data sources, keys and cost

The terminal runs with no keys at all, on delayed free data; each key you add to `.env` upgrades one part of it. A sensible small-budget setup is Twelve Data Pro plus Massive Options Starter, roughly $130 a month, with the free Finnhub and FRED keys.

| Provider | `.env` setting | What it adds | Cost when checked (Nov 2026) |
| --- | --- | --- | --- |
| [Twelve Data](https://twelvedata.com/pricing) | `TWELVEDATA_API_KEY` | Real-time quotes and intraday bars for US equities, FX and crypto | Free key: 8 requests a minute. Pro: about $99 a month |
| Twelve Data stream | `TWELVEDATA_STREAM=1` | Tick-by-tick updates pushed over a WebSocket instead of polling | Needs Pro or higher |
| [Massive](https://massive.com/pricing) (the former Polygon.io) | `MASSIVE_API_KEY` | Option chains with the vendor's own greeks and implied vol | Options Starter about $29 a month, 15 minutes delayed; real time is about $199 |
| [Finnhub](https://finnhub.io/pricing) | `FINNHUB_API_KEY` | Market and company news, market-wide earnings calendar | Free key, 60 requests a minute |
| [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) | `FRED_API_KEY` | Official API for yields and spreads, plus the macro release calendar (CPI, payrolls, GDP) | Free |
| Yahoo Finance | none | Everything else: global equities, indices, futures, commodities, option chains, deep history | Free, unofficial |

Prices move, so confirm on each vendor's pricing page before subscribing.

### How the terminal chooses a source

- For each quote it tries Twelve Data first (if a key is set and the instrument is a US stock, FX pair or crypto), then falls back to Yahoo.
- Every price carries a badge: green LIVE with the source name, amber DELAYED, or SIMULATED in demo mode. Trust the badge, not the clock.
- `QUOTE_TTL` in `.env` sets how often quotes refresh (default 5 seconds). Lower is fresher but spends more API requests; on the free Twelve Data key keep it at 15 or more.
- `ADMIN` shows each provider, whether its key is set, and whether its last call worked.

### What a small budget does not buy

- **Individual corporate bonds.** Single-bond prices (TRACE) need a bond feed costing far more than this budget. `CREDIT` gives index spreads by rating and liquid credit ETFs instead.
- **Live non-US government yields.** FRED carries G7 and EM 10-year yields as monthly averages. For an intraday read use the futures and bond ETFs on `RATES`.
- **Real-time futures and non-US exchanges.** These need exchange licences; Yahoo's delayed quotes are the stand-in.
- **Order book depth, time and sales.** Not included.

### Two cautions on licensing

- Yahoo Finance has no official API. The `yfinance` library reads the same data the website shows; it is fine for research but can break or throttle without notice, and Yahoo's terms limit it to personal use.
- Twelve Data's and Massive's entry plans are licensed for individual, non-commercial use. Once Augusta manages outside money, ask each vendor about a business licence (Twelve Data's start around $499 a month).

## 4. Signing in and getting around

Sign in with the username and temporary password your admin gave you; the first thing you do is choose your own password (10 or more characters, with an upper-case letter and a digit). Five wrong passwords lock the account for 15 minutes.

### The screen

- **Command bar** (top): type a ticker or a function and press Enter. Press `/` anywhere to jump to it. Type a company name and it suggests symbols.
- **Ticker strip**: nine benchmarks, always on. Click one to open it.
- **Left menu**: every screen, with its command code on the right.
- **Badge beside the clocks**: LIVE, DELAYED FEED or DEMO DATA for the terminal as a whole.
- **ALARMS button**: a red count appears when an alarm has fired that you have not looked at.
- **Footer**: who is signed in, the refresh interval, the time of the last update and the active sources.

### Commands

| Type this | You get |
| --- | --- |
| `AAPL` | Quote, chart, statistics and news for a ticker |
| `AAPL OPT` | Options chain with greeks |
| `AAPL HP` | Full price history as a CSV download |
| `AAPL ALRT` | The new-alarm form for that ticker |
| `AAPL N` | News for that ticker |
| `FRED DGS10` | Any FRED series: chart, download, alarm |
| `WEI` | World equity indices |
| `FX` | G10 and EM currencies, crypto |
| `RATES` | Treasury curve, G7 and EM government yields, policy rates |
| `CREDIT` | Corporate spreads and credit ETFs |
| `FUT` | Index, rate and currency futures |
| `CMDTY` | Energy, metals, agriculture |
| `NEWS` | Market headlines |
| `CAL` | Central banks, earnings, macro releases, team events |
| `ALRT` | Your alarms and the trigger log |
| `ACCT` | Password, alarm email, browser notifications |
| `HOME` | Dashboard and your watchlist |
| `HELP` | This list, inside the terminal |

Trading commands (`NVDA BUY 100`, `ES=F SELL 2`, `UST 10Y`, `PORT`, and `FIRM` for the CIO) are covered in section 9.

### Symbols

Symbols follow Yahoo Finance notation.

| Instrument | Examples |
| --- | --- |
| US stock or ETF | `AAPL`, `SPY`, `BRK-B` |
| Index | `^GSPC`, `^N225`, `^STOXX50E` |
| Non-US stock | `7203.T`, `VOD.L`, `RELIANCE.NS`, `SAP.DE` |
| FX pair | `EURUSD=X`, `USDJPY=X`, `USDINR=X` |
| Future or commodity | `ES=F`, `ZN=F`, `CL=F`, `GC=F` |
| Crypto | `BTC-USD` |
| Economic series | `FRED:DGS10`, `FRED:BAMLH0A0HYM2`, `FRED:CPIAUCSL` |

A few tickers collide with command words (`N`, `FX`, `CAL`). To open one as a ticker, add `GP` after it: `FX GP`.

## 5. The screens

Every price table works the same way: prices flash green or red as they change, clicking a row opens that instrument, and the clock button on a row sets an alarm on it.

### Home

Your watchlist, key indices, headlines, the next calendar events and your recent alarm triggers. Add a symbol with the box on the watchlist panel; remove one with the ✕ on its row. Each analyst has their own watchlist of up to 100 symbols, and FRED series can sit in it beside tickers.

### Instrument page (any ticker)

- **Header**: last price, change, source badge, and buttons for Set alarm, Watch and Options chain.
- **Chart**: ranges from 1D to MAX, line or candles, volume underneath. Move the mouse over it for the open, high, low, close and volume of any bar. The title states the move over the range you picked.
- **Quote panel**: open, high, low, previous close, volume, bid and ask, 52-week range, market cap, P/E, beta.
- **Statistics panel**: session VWAP, price versus VWAP, relative volume, 14-day RSI, 20-day z-score, 20-day realized volatility, 50- and 200-day averages, distance from the 52-week high and low. These are the same numbers alarms can watch.
- **News and profile** for the company.

### Options (`AAPL OPT`)

Calls on the left, puts on the right, strikes down the middle. Pick the expiry from the dropdown. Each side shows last, bid, ask, implied vol, delta, gamma, theta, vega, volume and open interest. The at-the-money row is highlighted and in-the-money cells are shaded.

- Theta is per calendar day; vega is per one volatility point.
- The line above the table shows the spot price, days to expiry, the risk-free rate used (the 3-month Treasury yield) and whether the greeks are the vendor's or the terminal's model.
- Without a Massive key the terminal computes greeks itself with Black-Scholes. Listed US equity options are American-style, so model greeks are close approximations; the gap is largest for deep in-the-money puts and around ex-dividend dates.
- Click any number to set an alarm on that contract's value.

### Rates (`RATES`)

The US Treasury curve today against one month and one year ago, each tenor with its 1-day, 1-month and 1-year change in basis points. Below it: G7 and emerging-market 10-year yields, policy and money-market rates, and Treasury futures and government bond ETFs as the intraday read. Click a row for its full history.

### Credit (`CREDIT`)

Option-adjusted spreads for US investment grade, each rating bucket from AAA to CCC, high yield, euro high yield and EM corporates, with changes and the 1-year range. Credit ETFs (LQD, HYG, JNK, BKLN, EMB, EMLC, CEMB, HYEM) trade intraday beside them.

### World indices, FX, futures, commodities

`WEI`, `FX`, `FUT` and `CMDTY` are grouped price tables: 25 indices, 22 currency pairs plus crypto, 12 futures, and 19 energy, metal and agricultural contracts. To change what a screen lists, edit `app/universe.py` and restart.

### News (`NEWS`)

Headlines with source and age, newest first. Enter a ticker for company news. Headlines open at the publisher in a new tab; the terminal stores no article text. The feeds are listed in `data/news_feeds.json`.

### Calendar (`CAL`)

One list by date: central bank decisions (Fed, ECB, Bank of England, Bank of Japan, Bank of Canada), earnings, US macro releases (needs the FRED key) and events the team adds. Use the buttons to change the window from 2 weeks to 13 months and to hide categories. The form at the bottom adds a team event everyone sees. To add another central bank, edit `data/central_banks.json`.

## 6. Alarms

An alarm watches one number on one instrument and tells you when it reaches your level. The server checks every alarm every 15 seconds, so alarms fire even when your browser is closed; only the pop-up and sound need the terminal open.

### Setting one

1. Open the form: the clock button on any row, **Set alarm** on an instrument page, `AAPL ALRT` in the command bar, or **+ New alarm** on `ALRT`. For an option, click the value you care about in the chain.
2. Choose what to watch. The form shows the current value and a **use** button that copies it into the level box as a starting point.
3. Choose the condition and type the level.
4. Add a note if the level means something ("thesis broken below here").
5. Tick how to be told, then **Set alarm**.

### What an alarm can watch

| Group | Metric | Notes |
| --- | --- | --- |
| Price | Last price or level | For a FRED series this is the latest value, e.g. the 10-year yield |
| Price | Day change %, day change (absolute), day high, day low |  |
| Volume | Day volume |  |
| Volume | Relative volume | Today's volume divided by the 20-day average |
| Intraday | Session VWAP | From 5-minute bars of the latest session |
| Intraday | Price vs VWAP % |  |
| Statistical | RSI, 14-day |  |
| Statistical | Z-score vs 20-day mean | How many standard deviations price sits from its 20-day average |
| Statistical | Realized volatility, 20-day annualized % |  |
| Trend | 50-day and 200-day moving average, and price vs each in % |  |
| Trend | Distance from the 52-week high and low in % |  |
| Options | Mid price, implied vol %, delta, gamma, theta, vega, volume, open interest | For one contract: expiry, strike, call or put |

### Conditions

| Condition | Fires when |
| --- | --- |
| At or above | The value is at or above the level. Fires at once if that is already true |
| At or below | The value is at or below the level. Fires at once if that is already true |
| Crosses above | The value was below the level at one check and at or above it at the next |
| Crosses below | The value was above the level at one check and at or below it at the next |

Use a cross when you care about the moment of the break, and at-or-above or at-or-below when you care about the state.

### How you are told

| Channel | What happens | Needs |
| --- | --- | --- |
| In-app | A pop-up that stays until dismissed, three beeps, and a count on the ALARMS button | The terminal open in a tab |
| Browser notification | A desktop notification even when the tab is in the background | Enable once under `ACCT` |
| Email | A message to your account email | Admin sets SMTP in `.env`; you set your email under `ACCT` |
| Discord or Slack | A post in the team channel, tagged with your username | Admin sets the webhook URL in `.env` |

A channel the admin has not configured is greyed out in the form.

### One-shot and repeating alarms

- By default an alarm fires once and its status changes to **triggered**. Press **Re-arm** on `ALRT` to use it again.
- Tick **Keep firing** to leave it active. It then waits at least the number of minutes you set between alerts, so a price sitting on your level does not flood the channel.
- **Pause** stops checking without deleting. **Delete** removes it.
- Each analyst sees and manages only their own alarms, up to 300.

### Reading the alarms table

**Now** is the value at the last check and **Checked** is how long ago that was. A red warning under an alarm means the data source could not supply the metric at the last check (market closed for that option, symbol delisted, provider down); the alarm stays active and retries.

### Limits worth knowing

- Alarms see what the data source sees. On a 15-minute delayed feed, an alarm fires about 15 minutes after the move. Check the badge on the instrument before relying on an alarm for entry timing.
- Checks run every 15 seconds (`ALERT_POLL_SECONDS`), so a spike that reverses inside that window can be missed by a cross condition.
- Volume and VWAP alarms need an instrument with reported volume; spot FX has none.

## 7. Historical data and export

Every chart has a **CSV** button that downloads exactly the range on screen, and `AAPL HP` downloads the full daily history in one step.

| Range | Bar size | Typical depth |
| --- | --- | --- |
| 1D | 5 minutes | Latest session |
| 5D | 15 minutes | Last five sessions |
| 1M, 3M, 6M, YTD, 1Y, 5Y | Daily | As labelled |
| MAX | Daily | Back to the first trade Yahoo holds, often decades for US stocks and indices |

- CSV columns are `datetime_utc, open, high, low, close, volume`. Daily prices are as traded, not adjusted for dividends.
- FRED series download the same way from their page (`FRED DGS10`, then CSV). Many run back to the 1960s or earlier.
- Any FRED series ID works, not only the ones on the screens: CPI (`CPIAUCSL`), unemployment (`UNRATE`), the 10-year minus 2-year spread (`T10Y2Y`), breakevens (`T10YIE`), the trade-weighted dollar (`DTWEXBGS`).
- For bulk pulls across many tickers, have a Python script sign in and call the same address the CSV button uses `/api/history?symbol=AAPL&range=MAX&format=csv`.

## 8. Administration and security

Admins manage accounts and data keys; analysts cannot see the Admin screen or each other's alarms and watchlists.

### Accounts (`ADMIN`)

| Task | How |
| --- | --- |
| Add an analyst | Fill in username, email and role, press **Create**. The temporary password is shown once |
| Forgotten password or locked account | **Reset password** issues a new temporary password and signs the person out everywhere |
| Someone leaves | **Disable**. Their alarms stop and their sessions end at once; nothing is deleted |
| Promote or demote | **Make admin** / **Make analyst**. The last active admin cannot be removed |
| The only admin is locked out | On the server: `python manage.py reset <username>` |

### Data sources and notifications

The Admin screen lists each provider, whether its key is set and whether its last call succeeded, and has a **Send test** button for email, Discord and Slack. Keys and webhook addresses live only in `.env` on the server; edit the file and restart to change them.

For email alarms, set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM`. With Gmail, use `smtp.gmail.com`, port 587 and an app password, not the account password.

### How access is protected

- Passwords are stored only as bcrypt hashes. Nobody, including the admin, can read one back.
- There is no self sign-up. Accounts exist only if an admin creates them.
- Sessions last at most 12 hours and end sooner on **EXIT**, on a password change and on a reset.
- The login cookie cannot be read by scripts, and requests that change anything are rejected unless they come from the terminal's own pages.
- The pages load no outside scripts or fonts, so nothing about what you look at leaves the server except the data requests to the providers.

### Housekeeping

- Back up `data/augusta.db` regularly. Restoring is copying the file back.
- Never email or commit `.env`; it holds the API keys and the mail password.
- To update the terminal, replace the `app` and `static` folders and restart. The database and `.env` carry over.
- `python -m pytest -q` runs the built-in tests on simulated data; run it after any code change.

## 9. Paper portfolio and the firm book

Every account, the CIO's included, starts with $1,000,000 of paper capital and can take long or short positions in stocks, ETFs, options, futures, FX, commodities, metals, crypto and US Treasuries, marked to market every few seconds. Existing accounts get theirs automatically the first time the updated terminal starts.

### Placing an order

1. Open the ticket: **Trade** on any price row or instrument page, **+ New order** on `PORT`, or a command such as `NVDA BUY 100`, `ES=F SELL 2` or `UST 10Y`. In an options chain, click a contract's Last, Bid or Ask.
2. Choose **BUY** or **SELL / SHORT** and enter the quantity. The label beside the box says what one unit is for that instrument.
3. Pick the order type and, for limit and stop orders, the price.
4. Write the thesis: why the trade, and what would prove it wrong. It is stored on the order and the CIO can read it.
5. Read the preview (estimated fill, trade value, commission, position after, leverage after) and place the order.

To exit, press **Close** on the position's row; the ticket opens pre-filled with the opposite side and the full size. **Add** opens it on the same side.

### What you can trade

| Asset | Symbol example | One unit of quantity | Notes |
| --- | --- | --- | --- |
| Stocks and ETFs, US and foreign | `AAPL`, `TLT`, `7203.T` | 1 share | Foreign shares are converted to USD at the live FX rate |
| Options | click a price in `AAPL OPT` | 1 contract = 100 shares | Cash-settled at intrinsic value after the close on expiry day |
| Equity index, rate and currency futures | `ES=F`, `ZN=F`, `6E=F` | 1 contract | Contract sizes are built in for the 31 futures on the `FUT` and `CMDTY` screens |
| Commodities and metals | `CL=F`, `NG=F`, `GC=F`, `HG=F` | 1 contract | Front-month future |
| Spot FX | `EURUSD=X`, `USDJPY=X` | 1 unit of the base currency | `EURUSD=X BUY 300000` is long 300,000 euros |
| Crypto | `BTC-USD` | 1 coin, fractions allowed |  |
| US Treasuries | `UST:2Y`, `UST:10Y`, `UST:30Y` | $1 of face value | Tenors from 1Y to 30Y. Simulated, see below |
| Corporate and EM credit | `LQD`, `HYG`, `EMB` | 1 share | Through ETFs; single bonds are not available |

Indices and FRED series cannot be traded directly. Trade the future, the ETF or the option instead.

### Order types

| Type | What it does |
| --- | --- |
| Market | Fills at once. Buys pay the ask and sells receive the bid when the feed has both; otherwise the last price |
| Limit | Rests until the price is at your limit or better, then fills at the market price at that moment |
| Stop | Rests until the last price trades through your stop, then fills as a market order |

Resting orders show as **working** in the blotter and can be cancelled there. The server checks them every 15 seconds whether or not you are signed in, and a pop-up tells you when one fills or is rejected.

### How the money works

- **Cash products** (stocks, ETFs, options, crypto, bonds): buying spends cash and selling raises it. Selling more than you hold opens a short; the proceeds sit in cash and the position shows a negative value.
- **Margin products** (futures, spot FX): opening a position moves no cash. The open profit or loss counts in your NAV and moves into cash when you close.
- **NAV** = cash + value of cash products + open P&L of margin products, all in US dollars.
- **Leverage limit**: gross exposure may not exceed 5 times NAV. An order that would breach it is rejected with the reason; orders that reduce exposure always go through. Options count at their delta-adjusted exposure.
- **Commissions**: 1 basis point of trade value on stocks, ETFs, crypto and bonds; $0.65 per option contract; $2.50 per futures contract; none on spot FX.

### The portfolio screen (`PORT`)

- **Tiles**: NAV and cash, day P&L, total P&L, gross and net exposure, leverage and remaining buying power.
- **Positions**: side, quantity, average price, last price, day change, value, exposure, P&L in dollars and percent, and share of NAV. For futures and FX, Value is the open P&L, because no cash was paid for them.
- **Equity curve**: NAV recorded every 15 minutes.
- **Exposure by asset class**: long, short, net and gross for equity, equity index, rates, FX, energy, metals, agriculture, options and crypto.
- **Risk and performance**: unrealized and realized P&L, commissions, max drawdown, win rate, annualized volatility and Sharpe ratio (after five trading days), aggregate option delta, gamma, theta and vega, and bond DV01.
- **Orders and blotter**: every order with status, fill, fee, realized P&L and the thesis.

### The firm book (`FIRM`, CIO only)

Admins see every active account ranked by return: NAV, return, day P&L, gross and net exposure, leverage, max drawdown, Sharpe, win rate and largest position, with firm totals and firm exposure by asset class. Click an analyst to open their book read-only, including the thesis on each trade. **Reset** returns an account to $1,000,000 and erases its positions, orders and history.

Analysts see only their own book. Starting capital, the leverage limit and the commission rate are set in `.env` (`PAPER_START_CASH`, `PAPER_MAX_LEVERAGE`, `PAPER_COMMISSION_BPS`).

### Simplifications to know

- **Fills use the same feed as the screens.** On a delayed source you trade at a 15-minute-old price, and orders fill in full at the quoted price with no market impact. Orders also fill when the real market is closed, at the last price.
- **Treasuries are simulated.** A bond is bought at par with a coupon equal to that day's FRED yield for the tenor, then repriced off the FRED curve at its remaining maturity, plus accrued interest. Prices change once per business day and Treasuries take market orders only.
- **Not modelled**: dividends, coupon payments, stock splits, borrow cost on shorts, interest on cash, margin calls and futures rolls. After a split, the position's average price will be wrong until it is closed and reopened.
- **Options settle in cash** at intrinsic value; there is no exercise or assignment.
- **If a price is unavailable**, the position is carried at cost with a warning until the feed returns.

## 10. Troubleshooting and known limits

Most problems are a missing key, a delayed source or a symbol in the wrong notation; the Admin screen's data-source table is the first place to look.

| What you see | Likely cause | Fix |
| --- | --- | --- |
| Dashes instead of prices | The provider did not answer, or the symbol is wrong | Check `ADMIN` for a FAILED provider; check the notation in section 4 |
| "No data for ..." after typing a ticker | Unknown symbol | Pick from the suggestions listed, or type the company name in the command bar |
| Amber DELAYED badge on a US stock | No Twelve Data key, or the free key's request limit was hit | Add the key; raise `QUOTE_TTL` on the free plan |
| Prices never change | Market closed, or the tab was in the background | Quotes pause while a tab is hidden and resume when you return |
| A row on `RATES` says unavailable | FRED renamed or retired that series | Replace its ID in `app/universe.py` |
| Greeks are blank for a strike | No bid and ask to solve implied vol from | Normal for illiquid strikes and outside market hours |
| Alarm shows a red warning | The metric had no data at the last check | It retries every cycle; delete it if the instrument is gone |
| No alarm email | SMTP not set, or no email on your account | Admin: **Send test** on `ADMIN`. You: add your email under `ACCT` |
| No alarm sound | Browsers block sound until you click the page once | Click anywhere after opening the terminal |
| Locked out | Five wrong passwords | Wait 15 minutes or ask an admin to reset |
| Yahoo data stops for everyone | Yahoo throttled the server or changed its site | Run `pip install -U yfinance` and restart; Twelve Data and FRED screens keep working |

### Known limits

- **Live vendor calls were not exercised during the build.** The terminal was built and tested on simulated data and on recorded samples of each vendor's response format, because the build environment had no route to Yahoo, Twelve Data, Massive, Finnhub or FRED. Expect to fix small things on the first live run; `ADMIN` shows which provider failed and why.
- **"Live" means polled every few seconds**, or streamed with Twelve Data Pro. It is not an exchange feed and is not suitable for execution timing.
- **Model greeks are Black-Scholes approximations** (section 5).
- **Central bank dates** were taken from each bank's published schedule on 10 November 2026 and run through December 2027. Banks label future dates tentative; add 2028 to `data/central_banks.json` when the schedules come out.
- **One server, one file database.** This comfortably serves a team of analysts. It is not built for hundreds of users.
- **Demo mode is fiction.** Simulated prices are for learning the screens and testing alarms, never for a decision.

### Sources for the central bank calendar

- [Federal Reserve: FOMC meeting calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- [Bank of Japan: Monetary Policy Meetings](https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm)
- [European Central Bank: indicative calendars for 2027](https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260630~9f54a0a4fb.en.html)
- [Bank of England: MPC dates for 2027](https://www.bankofengland.co.uk/news/2026/september/mpc-dates-for-2027)
- [Bank of Canada: 2027 schedule](https://www.bankofcanada.ca/2026/07/bank-canada-publishes-2027-schedule-policy-interest-rate-announcements-other-major-publications/)

## 11. For the software team

Python 3.10+, FastAPI, SQLite, and a dependency-free browser app (no build step, no external scripts). `DATA_MODE=demo` runs everything on simulated data with no keys or internet, which is the quickest way to develop. Never commit `.env` or `data/augusta.db`; both are in `.gitignore`.

### Accounts from the command line

```bash
python manage.py list
python manage.py add priya --email priya@example.com
python manage.py reset priya        # use this if the only admin is locked out
python manage.py deactivate priya
```

### Tests

```bash
python -m pytest -q
```

The tests use simulated data and canned vendor responses; they do not call any paid API.

### Layout

```
app/main.py        web server and API routes
app/auth.py        logins, sessions, lockout
app/market.py      provider routing, caching, option greeks
app/providers.py   Yahoo, Twelve Data, Massive, Finnhub, FRED, RSS
app/alerts.py      alarm metrics, engine, email / Discord / Slack
app/paper.py       paper trading: accounts, orders, fills, margin, risk, firm view
app/greeks.py      Black-Scholes price, greeks, implied vol
app/universe.py    instruments on each screen (edit to add your own)
app/synthetic.py   simulated data for demo mode and tests
data/              central_banks.json, news_feeds.json, augusta.db (created on first run)
static/            the browser app (no external scripts)
```
