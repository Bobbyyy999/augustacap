"""Black-Scholes-Merton price, greeks and implied volatility.

European-exercise model with a continuous dividend yield. Listed US equity options are
American, so treat these as close approximations (largest error: deep in-the-money puts
and calls just before an ex-dividend date).

Units returned by greeks():
  delta  per $1 move in the underlying
  gamma  change in delta per $1 move
  theta  option price change per calendar day
  vega   option price change per 1 volatility point (0.01)
  rho    option price change per 1 percentage point in rates
"""
import math

_SQRT2PI = math.sqrt(2 * math.pi)


def _pdf(x):
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _d1d2(S, K, T, r, q, sigma):
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S, K, T, r, q, sigma, cp):
    """cp is 'C' or 'P'. T in years, r/q/sigma as decimals."""
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if cp == "C" else max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    if cp == "C":
        return S * math.exp(-q * T) * _cdf(d1) - K * math.exp(-r * T) * _cdf(d2)
    return K * math.exp(-r * T) * _cdf(-d2) - S * math.exp(-q * T) * _cdf(-d1)


def greeks(S, K, T, r, q, sigma, cp):
    if S <= 0 or K <= 0 or T <= 0 or sigma is None or sigma <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    eq, er, st = math.exp(-q * T), math.exp(-r * T), math.sqrt(T)
    gamma = eq * _pdf(d1) / (S * sigma * st)
    vega = S * eq * _pdf(d1) * st / 100.0
    common = -S * eq * _pdf(d1) * sigma / (2 * st)
    if cp == "C":
        delta = eq * _cdf(d1)
        theta = common - r * K * er * _cdf(d2) + q * S * eq * _cdf(d1)
        rho = K * T * er * _cdf(d2) / 100.0
    else:
        delta = -eq * _cdf(-d1)
        theta = common + r * K * er * _cdf(-d2) - q * S * eq * _cdf(-d1)
        rho = -K * T * er * _cdf(-d2) / 100.0
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega, "rho": rho}


def implied_vol(price, S, K, T, r, q, cp, lo=1e-4, hi=6.0):
    """Bisection solve. Returns None when the price is outside no-arbitrage bounds."""
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    if price < bs_price(S, K, T, r, q, lo, cp) - 1e-9 or price > bs_price(S, K, T, r, q, hi, cp):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, q, mid, cp) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)
