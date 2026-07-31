"""Black-Scholes pricing and greeks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (1%)
    rho: float  # per 1% rate move


def _d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return float("nan"), float("nan")
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def black_scholes(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
    q: float = 0.0,
) -> float:
    """European option price. T in years, sigma annualized."""
    if T <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)
    if sigma <= 0:
        fwd = S * math.exp((r - q) * T)
        disc = math.exp(-r * T)
        if option_type == "call":
            return disc * max(fwd - K, 0.0)
        return disc * max(K - fwd, 0.0)

    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    df_r = math.exp(-r * T)
    df_q = math.exp(-q * T)
    if option_type == "call":
        return S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
    return K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)


def greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
    q: float = 0.0,
) -> Greeks:
    """Analytical Black-Scholes greeks. Theta is per calendar day; vega per 1% vol."""
    if T <= 0 or sigma <= 0 or S <= 0:
        intrinsic_call = 1.0 if S > K else (0.5 if S == K else 0.0)
        if option_type == "call":
            delta = intrinsic_call
        else:
            delta = intrinsic_call - 1.0
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    df_r = math.exp(-r * T)
    df_q = math.exp(-q * T)
    pdf_d1 = norm.pdf(d1)
    sqrt_t = math.sqrt(T)

    gamma = df_q * pdf_d1 / (S * sigma * sqrt_t)
    vega = S * df_q * pdf_d1 * sqrt_t / 100.0  # per 1 vol point

    if option_type == "call":
        delta = df_q * norm.cdf(d1)
        theta = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrt_t)
            - r * K * df_r * norm.cdf(d2)
            + q * S * df_q * norm.cdf(d1)
        ) / 365.0
        rho = K * T * df_r * norm.cdf(d2) / 100.0
    else:
        delta = -df_q * norm.cdf(-d1)
        theta = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrt_t)
            + r * K * df_r * norm.cdf(-d2)
            - q * S * df_q * norm.cdf(-d1)
        ) / 365.0
        rho = -K * T * df_r * norm.cdf(-d2) / 100.0

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = "call",
    q: float = 0.0,
    *,
    low: float = 1e-4,
    high: float = 5.0,
) -> float:
    """Solve for implied volatility. Returns NaN if no solution."""
    if T <= 0 or price <= 0 or S <= 0 or K <= 0:
        return float("nan")

    intrinsic = black_scholes(S, K, 0.0, r, 0.0, option_type, q)
    if price < intrinsic - 1e-8:
        return float("nan")

    def objective(sig: float) -> float:
        return black_scholes(S, K, T, r, sig, option_type, q) - price

    try:
        if objective(low) * objective(high) > 0:
            # Expand upper bound once for deep OTM cheap options / rich prices.
            high = 10.0
            if objective(low) * objective(high) > 0:
                return float("nan")
        return float(brentq(objective, low, high))
    except ValueError:
        return float("nan")


def price_vectorized(
    S: np.ndarray,
    K: float,
    T: float,
    r: float,
    sigma: float | np.ndarray,
    option_type: OptionType = "call",
    q: float = 0.0,
) -> np.ndarray:
    """Vectorized BS over spot (and optionally vol)."""
    S = np.asarray(S, dtype=float)
    sigma_arr = np.asarray(sigma, dtype=float)
    out = np.empty_like(S, dtype=float)

    if T <= 0:
        if option_type == "call":
            return np.maximum(S - K, 0.0)
        return np.maximum(K - S, 0.0)

    # Broadcast-friendly path
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_sqrt_t = sigma_arr * np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma_arr**2) * T) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        df_r = math.exp(-r * T)
        df_q = math.exp(-q * T)
        if option_type == "call":
            out = S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
        else:
            out = K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)

        # Zero / invalid vol fallback to discounted intrinsic forward
        bad = ~np.isfinite(out) | (sigma_arr <= 0)
        if np.any(bad):
            fwd = S * math.exp((r - q) * T)
            if option_type == "call":
                out = np.where(bad, df_r * np.maximum(fwd - K, 0.0), out)
            else:
                out = np.where(bad, df_r * np.maximum(K - fwd, 0.0), out)
    return out
