"""P&L curves, surfaces, and breakeven analysis for multi-leg positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from .position import Position


@dataclass
class PnLCurve:
    spots: np.ndarray
    pnl: np.ndarray
    as_of: date
    label: str
    breakevens: list[float]
    max_profit: float
    max_loss: float
    pnl_at_spot: float | None = None


@dataclass
class PositionAnalysis:
    position: Position
    spot: float
    as_of: date
    open_cash: float
    net_debit: float
    current_pnl: float
    greeks: dict[str, float]
    leg_marks: list[dict[str, Any]]
    curves: list[PnLCurve]
    nearest_expiry: date | None
    notes: list[str]


def _find_breakevens(spots: np.ndarray, pnl: np.ndarray) -> list[float]:
    """Linearly interpolate zero crossings."""
    bes: list[float] = []
    for i in range(len(pnl) - 1):
        a, b = pnl[i], pnl[i + 1]
        if a == 0:
            bes.append(float(spots[i]))
        elif a * b < 0:
            # interpolate
            t = abs(a) / (abs(a) + abs(b))
            bes.append(float(spots[i] + t * (spots[i + 1] - spots[i])))
    if len(pnl) and pnl[-1] == 0:
        bes.append(float(spots[-1]))
    # de-dupe near-equal
    out: list[float] = []
    for x in bes:
        if not out or abs(x - out[-1]) > 1e-6:
            out.append(x)
    return out


def pnl_curve(
    position: Position,
    spots: np.ndarray | None = None,
    *,
    spot: float | None = None,
    as_of: date | None = None,
    iv_override: float | None = None,
    iv_by_leg: dict[str, float] | None = None,
    spot_range: tuple[float, float] | None = None,
    n: int = 251,
    label: str | None = None,
) -> PnLCurve:
    as_of = as_of or position.as_of or date.today()
    if spots is None:
        if spot_range is not None:
            lo, hi = spot_range
        else:
            center = spot
            if center is None:
                strikes = [leg.strike for leg in position.legs]
                center = float(np.mean(strikes)) if strikes else 100.0
            lo, hi = center * 0.7, center * 1.3
        spots = np.linspace(lo, hi, n)
    else:
        spots = np.asarray(spots, dtype=float)

    pnl = np.array(
        [
            position.pnl(float(s), as_of=as_of, iv_override=iv_override, iv_by_leg=iv_by_leg)
            for s in spots
        ],
        dtype=float,
    )
    bes = _find_breakevens(spots, pnl)
    pnl_at = None
    if spot is not None:
        pnl_at = float(position.pnl(spot, as_of=as_of, iv_override=iv_override, iv_by_leg=iv_by_leg))

    return PnLCurve(
        spots=spots,
        pnl=pnl,
        as_of=as_of,
        label=label or f"P&L as of {as_of.isoformat()}",
        breakevens=bes,
        max_profit=float(np.max(pnl)),
        max_loss=float(np.min(pnl)),
        pnl_at_spot=pnl_at,
    )


def pnl_surface(
    position: Position,
    spots: np.ndarray,
    *,
    mode: str = "iv",
    ivs: np.ndarray | None = None,
    as_of_dates: list[date] | None = None,
    base_iv: float | None = None,
) -> dict[str, Any]:
    """
    2D P&L surface.

    mode='iv':  x=spot, y=IV flat override
    mode='time': x=spot, y=as_of dates (calendar decay path)
    """
    spots = np.asarray(spots, dtype=float)
    if mode == "iv":
        if ivs is None:
            ivs = np.linspace(0.10, 0.80, 36)
        ivs = np.asarray(ivs, dtype=float)
        z = np.zeros((len(ivs), len(spots)))
        as_of = position.as_of or date.today()
        for i, iv in enumerate(ivs):
            for j, s in enumerate(spots):
                z[i, j] = position.pnl(float(s), as_of=as_of, iv_override=float(iv))
        return {"mode": "iv", "spots": spots, "ivs": ivs, "pnl": z, "as_of": as_of}

    if mode == "time":
        if as_of_dates is None:
            start = position.as_of or date.today()
            end = position.nearest_expiry or (start + timedelta(days=30))
            days = max((end - start).days, 1)
            as_of_dates = [start + timedelta(days=d) for d in range(0, days + 1)]
        z = np.zeros((len(as_of_dates), len(spots)))
        for i, d in enumerate(as_of_dates):
            for j, s in enumerate(spots):
                z[i, j] = position.pnl(float(s), as_of=d, iv_override=base_iv)
        return {"mode": "time", "spots": spots, "dates": as_of_dates, "pnl": z}

    raise ValueError("mode must be 'iv' or 'time'")


def _leg_breakdown(
    position: Position,
    spot: float,
    as_of: date,
    iv_override: float | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for leg in position.legs:
        iv = iv_override if iv_override is not None else leg.iv
        mark = leg.mark(spot, as_of=as_of, r=position.rate, q=position.dividend_yield, iv=iv)
        value = leg.signed_contracts * mark * leg.multiplier
        entry = (leg.premium or 0.0) * leg.signed_contracts * leg.multiplier
        # For a long leg, entry cash is negative; pnl = value + cash_flow
        rows.append(
            {
                "label": leg.label,
                "type": leg.option_type,
                "strike": leg.strike,
                "expiry": leg.expiry.isoformat(),
                "dte": leg.dte(as_of),
                "qty": leg.signed_contracts,
                "fill": leg.premium,
                "iv": iv,
                "mark": mark,
                "value": value,
                "leg_pnl": value + leg.cash_flow(),
                "greeks": leg.leg_greeks(
                    spot, as_of=as_of, r=position.rate, q=position.dividend_yield, iv=iv
                ),
            }
        )
    return rows


def analyze_position(
    position: Position,
    spot: float,
    *,
    iv_override: float | None = None,
    as_of: date | None = None,
    spot_range: tuple[float, float] | None = None,
    n: int = 251,
) -> PositionAnalysis:
    """Full analysis package for UI / reporting."""
    as_of = as_of or position.as_of or date.today()
    notes: list[str] = []

    if position.net_cash is not None:
        notes.append(
            f"Using actual net open cash of ${position.open_cash:,.2f} "
            f"({'debit' if position.open_cash < 0 else 'credit'}). "
            "This overrides summed leg fills — critical when you filled inside the ask."
        )
    else:
        missing = [leg.label for leg in position.legs if leg.premium is None]
        if missing:
            notes.append(
                "Some legs are missing fill premiums; open cash / breakevens may be wrong: "
                + ", ".join(missing or [])
            )

    expiries = sorted({leg.expiry for leg in position.legs})
    if len(expiries) > 1:
        front, back = expiries[0], expiries[-1]
        notes.append(
            "Multi-expiry position detected (e.g. calendar). "
            f"The critical slice is at front expiry ({front.isoformat()}): "
            "the short leg settles to intrinsic while the back-month leg is still marked "
            "with time value. IBKR often only plots the front leg."
        )
        notes.append(
            f"If held to final expiry ({back.isoformat()}), same-strike calendars "
            "collapse to ~flat loss of the debit (long and short intrinsics cancel). "
            "Calendar edge is realized around front expiry / when you unwind the spread."
        )

    curves: list[PnLCurve] = []
    # Today / as-of mark curve
    curves.append(
        pnl_curve(
            position,
            spot=spot,
            as_of=as_of,
            iv_override=iv_override,
            spot_range=spot_range,
            n=n,
            label=f"Marked P&L ({as_of.isoformat()})",
        )
    )

    # Front expiry first (the calendar money slice), then later expiries
    for exp in expiries:
        is_front = exp == expiries[0] and len(expiries) > 1
        label = (
            f"At front expiry {exp.isoformat()} (back legs still marked) — use this"
            if is_front
            else f"At {exp.isoformat()} (expired legs → intrinsic at scenario spot)"
        )
        curves.append(
            pnl_curve(
                position,
                spot=spot,
                as_of=exp,
                iv_override=iv_override,
                spot_range=spot_range,
                n=n,
                label=label,
            )
        )

    # What many brokers show at front expiry: intrinsic-only on the plotted leg(s),
    # ignoring back-month extrinsic — contrast curve for calendars.
    if len(expiries) > 1:
        front = expiries[0]
        spots = curves[0].spots
        naive = []
        for s in spots:
            # Settle every leg as if intrinsic at front expiry (wrong for live back legs).
            val = sum(leg.intrinsic_value(float(s)) for leg in position.legs)
            val += sum(st.value(float(s)) for st in position.stock_legs)
            naive.append(val + position.open_cash)
        naive_arr = np.array(naive, dtype=float)
        curves.append(
            PnLCurve(
                spots=spots,
                pnl=naive_arr,
                as_of=front,
                label=f"Naive all-intrinsic @ {front.isoformat()} (broker-style miss)",
                breakevens=_find_breakevens(spots, naive_arr),
                max_profit=float(np.max(naive_arr)),
                max_loss=float(np.min(naive_arr)),
            )
        )

    greeks = position.aggregate_greeks(spot, as_of=as_of, iv_override=iv_override)
    leg_marks = _leg_breakdown(position, spot, as_of, iv_override=iv_override)

    return PositionAnalysis(
        position=position,
        spot=spot,
        as_of=as_of,
        open_cash=position.open_cash,
        net_debit=position.net_debit,
        current_pnl=position.pnl(spot, as_of=as_of, iv_override=iv_override),
        greeks=greeks,
        leg_marks=leg_marks,
        curves=curves,
        nearest_expiry=position.nearest_expiry,
        notes=notes,
    )
