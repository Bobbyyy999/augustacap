"""Instrument lists behind each screen. Symbols use Yahoo Finance notation.

Edit freely: add a row and it shows up on the screen after a restart.
FRED series IDs can be looked up at https://fred.stlouisfed.org
"""

SCREENS = {
    "wei": {
        "title": "World Equity Indices",
        "groups": [
            ("Americas", [("^GSPC", "S&P 500"), ("^DJI", "Dow Jones"), ("^IXIC", "Nasdaq Comp"),
                          ("^RUT", "Russell 2000"), ("^VIX", "VIX"), ("^GSPTSE", "TSX Comp"),
                          ("^BVSP", "Bovespa"), ("^MXX", "IPC Mexico")]),
            ("Europe", [("^STOXX50E", "Euro Stoxx 50"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"),
                        ("^FCHI", "CAC 40"), ("^IBEX", "IBEX 35"), ("FTSEMIB.MI", "FTSE MIB"),
                        ("^SSMI", "SMI")]),
            ("Asia-Pacific", [("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("000001.SS", "Shanghai Comp"),
                              ("^KS11", "KOSPI"), ("^TWII", "Taiwan Weighted"), ("^NSEI", "Nifty 50"),
                              ("^BSESN", "Sensex"), ("^AXJO", "ASX 200"), ("^STI", "Straits Times"),
                              ("^JKSE", "Jakarta Comp")]),
        ],
    },
    "fx": {
        "title": "Foreign Exchange",
        "groups": [
            ("G10", [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("USDJPY=X", "USD/JPY"),
                     ("USDCHF=X", "USD/CHF"), ("AUDUSD=X", "AUD/USD"), ("NZDUSD=X", "NZD/USD"),
                     ("USDCAD=X", "USD/CAD"), ("USDSEK=X", "USD/SEK"), ("USDNOK=X", "USD/NOK"),
                     ("EURGBP=X", "EUR/GBP"), ("EURJPY=X", "EUR/JPY"), ("DX-Y.NYB", "Dollar Index")]),
            ("Emerging markets", [("USDCNY=X", "USD/CNY"), ("USDINR=X", "USD/INR"), ("USDBRL=X", "USD/BRL"),
                                  ("USDMXN=X", "USD/MXN"), ("USDZAR=X", "USD/ZAR"), ("USDTRY=X", "USD/TRY"),
                                  ("USDKRW=X", "USD/KRW"), ("USDIDR=X", "USD/IDR"), ("USDPLN=X", "USD/PLN"),
                                  ("USDTHB=X", "USD/THB")]),
            ("Crypto", [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ether"), ("SOL-USD", "Solana")]),
        ],
    },
    "fut": {
        "title": "Futures (front month)",
        "groups": [
            ("Equity index", [("ES=F", "E-mini S&P 500"), ("NQ=F", "E-mini Nasdaq 100"),
                              ("YM=F", "E-mini Dow"), ("RTY=F", "E-mini Russell 2000")]),
            ("Rates", [("ZT=F", "2Y T-Note"), ("ZF=F", "5Y T-Note"), ("ZN=F", "10Y T-Note"),
                       ("ZB=F", "30Y T-Bond")]),
            ("Currency", [("6E=F", "Euro FX"), ("6J=F", "Japanese Yen"), ("6B=F", "British Pound"),
                          ("6A=F", "Australian Dollar")]),
        ],
    },
    "cmdty": {
        "title": "Commodities & Metals",
        "groups": [
            ("Energy", [("CL=F", "WTI Crude"), ("BZ=F", "Brent Crude"), ("NG=F", "Natural Gas"),
                        ("RB=F", "RBOB Gasoline"), ("HO=F", "Heating Oil")]),
            ("Precious metals", [("GC=F", "Gold"), ("SI=F", "Silver"), ("PL=F", "Platinum"),
                                 ("PA=F", "Palladium")]),
            ("Base metals", [("HG=F", "Copper"), ("ALI=F", "Aluminum")]),
            ("Agriculture", [("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("ZS=F", "Soybeans"), ("KC=F", "Coffee"),
                             ("SB=F", "Sugar #11"), ("CT=F", "Cotton"), ("CC=F", "Cocoa"),
                             ("LE=F", "Live Cattle")]),
        ],
    },
    "bond_etfs": {
        "title": "Bond ETFs (live proxies)",
        "groups": [
            ("Government", [("SHY", "1-3Y Treasury"), ("IEF", "7-10Y Treasury"), ("TLT", "20Y+ Treasury"),
                            ("TIP", "TIPS"), ("BWX", "Intl Treasury ex-US")]),
            ("Corporate & EM", [("LQD", "US IG Corporate"), ("HYG", "US High Yield"), ("JNK", "US High Yield (SPDR)"),
                                ("BKLN", "Senior Loans"), ("EMB", "EM USD Sovereign"),
                                ("EMLC", "EM Local Currency"), ("CEMB", "EM Corporate"),
                                ("HYEM", "EM High Yield")]),
        ],
    },
}

TICKER_STRIP = ["^GSPC", "^IXIC", "^STOXX50E", "^N225", "EURUSD=X", "USDJPY=X", "CL=F", "GC=F", "BTC-USD"]

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "SPY", "TLT", "GC=F", "EURUSD=X", "FRED:DGS10"]

# Used for the earnings calendar when no Finnhub key is configured.
EARNINGS_FALLBACK = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "BAC", "GS",
                     "XOM", "CVX", "UNH", "LLY", "WMT", "COST", "NFLX", "AMD", "AVGO", "CRM"]

US_CURVE = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"),
            ("3Y", "DGS3"), ("5Y", "DGS5"), ("7Y", "DGS7"), ("10Y", "DGS10"), ("20Y", "DGS20"),
            ("30Y", "DGS30")]

# 10-year benchmark government yields. Monthly averages (OECD via FRED) except the US (daily).
G7_10Y = [("United States", "DGS10", "daily"), ("Germany", "IRLTLT01DEM156N", "monthly"),
          ("United Kingdom", "IRLTLT01GBM156N", "monthly"), ("Japan", "IRLTLT01JPM156N", "monthly"),
          ("France", "IRLTLT01FRM156N", "monthly"), ("Italy", "IRLTLT01ITM156N", "monthly"),
          ("Canada", "IRLTLT01CAM156N", "monthly")]

EM_10Y = [("Mexico", "IRLTLT01MXM156N", "monthly"), ("South Africa", "IRLTLT01ZAM156N", "monthly"),
          ("South Korea", "IRLTLT01KRM156N", "monthly"), ("India", "INDIRLTLT01STM", "monthly"),
          ("Poland", "IRLTLT01PLM156N", "monthly"), ("Hungary", "IRLTLT01HUM156N", "monthly"),
          ("Chile", "IRLTLT01CLM156N", "monthly"), ("Colombia", "IRLTLT01COM156N", "monthly"),
          ("Israel", "IRLTLT01ILM156N", "monthly")]

POLICY_RATES = [("Fed funds target (upper)", "DFEDTARU"), ("SOFR", "SOFR"),
                ("ECB deposit facility", "ECBDFR"), ("Effective fed funds", "DFF")]

# ICE BofA option-adjusted spreads and yields (percent), daily.
CREDIT = [("US IG corporate OAS", "BAMLC0A0CM"), ("AAA OAS", "BAMLC0A1CAAA"), ("AA OAS", "BAMLC0A2CAA"),
          ("A OAS", "BAMLC0A3CA"), ("BBB OAS", "BAMLC0A4CBBB"), ("US high yield OAS", "BAMLH0A0HYM2"),
          ("BB OAS", "BAMLH0A1HYBB"), ("B OAS", "BAMLH0A2HYB"), ("CCC & lower OAS", "BAMLH0A3HYC"),
          ("Euro high yield OAS", "BAMLHE00EHYIOAS"), ("EM corporate OAS", "BAMLEMCBPIOAS"),
          ("US IG effective yield", "BAMLC0A0CMEY"), ("US HY effective yield", "BAMLH0A0HYM2EY")]

MACRO_RELEASES = ["Employment Situation", "Consumer Price Index", "Producer Price Index",
                  "Gross Domestic Product", "Personal Income and Outlays",
                  "Advance Monthly Sales for Retail and Food Services",
                  "Job Openings and Labor Turnover Survey", "G.17 Industrial Production and Capacity Utilization"]


def label_for(symbol):
    for scr in SCREENS.values():
        for _, rows in scr["groups"]:
            for sym, name in rows:
                if sym == symbol:
                    return name
    return None
