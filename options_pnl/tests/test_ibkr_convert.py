"""Tests for IBKR → Position conversion (no live TWS required)."""

from __future__ import annotations

from datetime import date

from options_pnl.ibkr.positions import IbkrOptionRow, position_from_ibkr_legs


def test_calendar_from_ibkr_rows_uses_avg_cost_net_cash():
    rows = [
        IbkrOptionRow(
            underlying="XYZ",
            sec_type="OPT",
            quantity=-1,
            avg_cost=2.40,
            premium_per_share=2.40,
            strike=100.0,
            expiry=date(2026, 8, 21),
            right="C",
            currency="USD",
            exchange="SMART",
            local_symbol="XYZ   260821C00100000",
            con_id=1,
        ),
        IbkrOptionRow(
            underlying="XYZ",
            sec_type="OPT",
            quantity=1,
            avg_cost=3.25,
            premium_per_share=3.25,
            strike=100.0,
            expiry=date(2026, 9, 18),
            right="C",
            currency="USD",
            exchange="SMART",
            local_symbol="XYZ   260918C00100000",
            con_id=2,
        ),
    ]
    pos = position_from_ibkr_legs(rows, underlying="XYZ")
    assert len(pos.legs) == 2
    # short credit 240, long debit 325 → net cash -85
    assert pos.open_cash == -85.0
    assert pos.net_debit == 85.0
    assert pos.legs[0].quantity == -1
    assert pos.legs[1].expiry == date(2026, 9, 18)


def test_per_contract_avg_cost_heuristic():
    from options_pnl.ibkr.positions import _premium_from_avg_cost

    assert _premium_from_avg_cost(2.40, multiplier=100, includes_multiplier=False) == 2.40
    assert _premium_from_avg_cost(240.0, multiplier=100, includes_multiplier=False) == 2.40
    assert _premium_from_avg_cost(240.0, multiplier=100, includes_multiplier=True) == 2.40
