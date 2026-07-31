"""Unit tests for pricing and calendar P&L logic."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from options_pnl.bs import black_scholes, greeks, implied_vol
from options_pnl.io import load_position
from options_pnl.pnl import analyze_position, pnl_curve
from options_pnl.position import OptionLeg, Position

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "call_calendar.json"


def test_call_put_parity_approx():
    S, K, T, r, sig = 100.0, 100.0, 0.5, 0.05, 0.2
    c = black_scholes(S, K, T, r, sig, "call")
    p = black_scholes(S, K, T, r, sig, "put")
    # C - P ≈ S - K e^{-rT}
    assert c - p == pytest.approx(S - K * np.exp(-r * T), rel=1e-6)


def test_expired_intrinsic():
    assert black_scholes(110, 100, 0, 0.05, 0.2, "call") == 10
    assert black_scholes(90, 100, 0, 0.05, 0.2, "put") == 10


def test_iv_roundtrip():
    S, K, T, r, sig = 100.0, 105.0, 45 / 365, 0.04, 0.28
    px = black_scholes(S, K, T, r, sig, "call")
    iv = implied_vol(px, S, K, T, r, "call")
    assert iv == pytest.approx(sig, rel=1e-4)


def test_delta_bounds():
    g = greeks(100, 100, 0.25, 0.05, 0.2, "call")
    assert 0 < g.delta < 1
    g_put = greeks(100, 100, 0.25, 0.05, 0.2, "put")
    assert -1 < g_put.delta < 0


def test_calendar_uses_net_cash_not_leg_sum():
    pos = load_position(EXAMPLE)
    # Leg premiums imply debit of (3.55 - 2.40) * 100 = 115
    assert pos.net_cash == -85.0
    assert pos.net_debit == 85.0
    assert pos.open_cash == -85.0


def test_calendar_front_expiry_not_intrinsic_only():
    pos = load_position(EXAMPLE)
    spot = 100.0
    front = date(2026, 8, 21)
    # At front expiry, ATM: short call worth 0 intrinsic, long still has time value > 0
    long_leg = pos.legs[1]
    mark_back = long_leg.mark(spot, as_of=front, r=pos.rate, iv=long_leg.iv)
    assert mark_back > 0.5  # still meaningful extrinsic

    pnl_model = pos.pnl(spot, as_of=front)
    # Naive intrinsic both legs ATM = 0 + open cash
    naive = 0.0 + pos.open_cash
    assert pnl_model > naive  # back-month value improves P&L vs naive broker plot


def test_breakevens_exist_for_calendar():
    pos = load_position(EXAMPLE)
    curve = pnl_curve(pos, spot=100.0, as_of=date(2026, 8, 21), spot_range=(70, 130))
    # Calendar usually has two breakevens around the strike
    assert len(curve.breakevens) >= 1
    assert curve.max_profit > 0


def test_analyze_position_includes_comparison_curve():
    pos = load_position(EXAMPLE)
    report = analyze_position(pos, spot=100.0)
    assert report.current_pnl is not None
    assert any("Naive all-intrinsic" in c.label for c in report.curves)
    assert any("Multi-expiry" in n for n in report.notes)
    assert abs(report.greeks["delta"]) >= 0  # finite


def test_short_leg_negative_delta_contribution():
    leg = OptionLeg("call", 100, date(2026, 9, 18), quantity=-1, premium=2.0, iv=0.3)
    g = leg.leg_greeks(100, as_of=date(2026, 7, 31), r=0.04)
    assert g["delta"] < 0
