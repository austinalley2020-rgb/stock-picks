"""Multi-leg option / stock position model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from .bs import OptionType, black_scholes, greeks

Side = Literal["long", "short"]


def _parse_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass
class OptionLeg:
    """One option contract leg.

    quantity > 0 means long contracts; use side='short' or negative quantity.
    premium is the per-share fill price (debit paid if long, credit received if short).
    """

    option_type: OptionType
    strike: float
    expiry: date
    quantity: int = 1  # number of contracts (can be negative for short)
    premium: float | None = None  # fill price per share
    iv: float | None = None  # annualized implied vol used for marking
    multiplier: int = 100
    label: str | None = None

    def __post_init__(self) -> None:
        self.expiry = _parse_date(self.expiry)  # type: ignore[assignment]
        self.option_type = self.option_type.lower()  # type: ignore[assignment]
        if self.option_type not in ("call", "put"):
            raise ValueError(f"option_type must be call/put, got {self.option_type}")
        if self.label is None:
            self.label = (
                f"{'long' if self.quantity > 0 else 'short'} "
                f"{abs(self.quantity)}x {self.expiry.isoformat()} "
                f"{self.strike:g} {self.option_type}"
            )

    @property
    def signed_contracts(self) -> int:
        return int(self.quantity)

    def dte(self, as_of: date | None = None) -> int:
        as_of = as_of or date.today()
        return (self.expiry - as_of).days

    def years_to_expiry(self, as_of: date | None = None, days_in_year: float = 365.0) -> float:
        return max(self.dte(as_of), 0) / days_in_year

    def cash_flow(self) -> float:
        """Net cash at open: negative = debit paid, positive = credit received."""
        if self.premium is None:
            return 0.0
        # Long pays premium; short receives premium.
        return -self.signed_contracts * self.premium * self.multiplier

    def mark(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        r: float = 0.0,
        q: float = 0.0,
        iv: float | None = None,
        days_in_year: float = 365.0,
    ) -> float:
        """Theoretical mark per share."""
        sigma = iv if iv is not None else self.iv
        if sigma is None:
            raise ValueError(f"IV required to mark leg '{self.label}'")
        T = self.years_to_expiry(as_of, days_in_year)
        return black_scholes(spot, self.strike, T, r, sigma, self.option_type, q)

    def value(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        r: float = 0.0,
        q: float = 0.0,
        iv: float | None = None,
        days_in_year: float = 365.0,
    ) -> float:
        """Marked-to-model position value in dollars."""
        return self.signed_contracts * self.mark(spot, as_of=as_of, r=r, q=q, iv=iv, days_in_year=days_in_year) * self.multiplier

    def intrinsic_value(self, spot: float) -> float:
        if self.option_type == "call":
            per_share = max(spot - self.strike, 0.0)
        else:
            per_share = max(self.strike - spot, 0.0)
        return self.signed_contracts * per_share * self.multiplier

    def leg_greeks(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        r: float = 0.0,
        q: float = 0.0,
        iv: float | None = None,
        days_in_year: float = 365.0,
    ) -> dict[str, float]:
        sigma = iv if iv is not None else self.iv
        if sigma is None:
            raise ValueError(f"IV required for greeks on leg '{self.label}'")
        T = self.years_to_expiry(as_of, days_in_year)
        g = greeks(spot, self.strike, T, r, sigma, self.option_type, q)
        scale = self.signed_contracts * self.multiplier
        return {
            "delta": g.delta * scale,
            "gamma": g.gamma * scale,
            "theta": g.theta * scale,
            "vega": g.vega * scale,
            "rho": g.rho * scale,
            "delta_shares": g.delta * self.signed_contracts * self.multiplier,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_type": self.option_type,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "quantity": self.quantity,
            "premium": self.premium,
            "iv": self.iv,
            "multiplier": self.multiplier,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OptionLeg:
        return cls(
            option_type=data["option_type"],
            strike=float(data["strike"]),
            expiry=data["expiry"],
            quantity=int(data.get("quantity", 1)),
            premium=None if data.get("premium") is None else float(data["premium"]),
            iv=None if data.get("iv") is None else float(data["iv"]),
            multiplier=int(data.get("multiplier", 100)),
            label=data.get("label"),
        )


@dataclass
class StockLeg:
    """Optional underlying stock hedge."""

    quantity: int  # shares; negative = short
    entry_price: float | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if self.label is None:
            self.label = f"{'long' if self.quantity > 0 else 'short'} {abs(self.quantity)} shares"

    def cash_flow(self) -> float:
        if self.entry_price is None:
            return 0.0
        return -self.quantity * self.entry_price

    def value(self, spot: float, **_: Any) -> float:
        return self.quantity * spot

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "label": self.label,
            "kind": "stock",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StockLeg:
        return cls(
            quantity=int(data["quantity"]),
            entry_price=None if data.get("entry_price") is None else float(data["entry_price"]),
            label=data.get("label"),
        )


@dataclass
class Position:
    """Multi-leg options position with known net debit/credit."""

    name: str
    legs: list[OptionLeg]
    underlying: str = ""
    stock_legs: list[StockLeg] = field(default_factory=list)
    # If set, overrides sum of leg premiums for net open cash (dollars).
    # Negative = net debit paid; positive = net credit received.
    net_cash: float | None = None
    as_of: date | None = None
    rate: float = 0.045
    dividend_yield: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        self.as_of = _parse_date(self.as_of) or date.today()

    @property
    def open_cash(self) -> float:
        if self.net_cash is not None:
            return float(self.net_cash)
        return sum(leg.cash_flow() for leg in self.legs) + sum(s.cash_flow() for s in self.stock_legs)

    @property
    def net_debit(self) -> float:
        """Positive number means you paid a debit to open."""
        return max(-self.open_cash, 0.0)

    @property
    def net_credit(self) -> float:
        return max(self.open_cash, 0.0)

    @property
    def nearest_expiry(self) -> date | None:
        if not self.legs:
            return None
        return min(leg.expiry for leg in self.legs)

    @property
    def furthest_expiry(self) -> date | None:
        if not self.legs:
            return None
        return max(leg.expiry for leg in self.legs)

    def dte_by_leg(self, as_of: date | None = None) -> dict[str, int]:
        as_of = as_of or self.as_of
        return {leg.label or str(i): leg.dte(as_of) for i, leg in enumerate(self.legs)}

    def mark_value(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        iv_override: float | None = None,
        iv_by_leg: dict[str, float] | None = None,
    ) -> float:
        as_of = as_of or self.as_of
        total = 0.0
        for leg in self.legs:
            iv = None
            if iv_by_leg and leg.label in iv_by_leg:
                iv = iv_by_leg[leg.label]
            elif iv_override is not None:
                iv = iv_override
            total += leg.value(
                spot,
                as_of=as_of,
                r=self.rate,
                q=self.dividend_yield,
                iv=iv,
            )
        for stock in self.stock_legs:
            total += stock.value(spot)
        return total

    def pnl(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        iv_override: float | None = None,
        iv_by_leg: dict[str, float] | None = None,
    ) -> float:
        """Dollar P&L = mark value + open cash (cash already signed)."""
        return self.mark_value(spot, as_of=as_of, iv_override=iv_override, iv_by_leg=iv_by_leg) + self.open_cash

    def aggregate_greeks(
        self,
        spot: float,
        *,
        as_of: date | None = None,
        iv_override: float | None = None,
        iv_by_leg: dict[str, float] | None = None,
    ) -> dict[str, float]:
        as_of = as_of or self.as_of
        agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
        for leg in self.legs:
            iv = None
            if iv_by_leg and leg.label in iv_by_leg:
                iv = iv_by_leg[leg.label]
            elif iv_override is not None:
                iv = iv_override
            g = leg.leg_greeks(spot, as_of=as_of, r=self.rate, q=self.dividend_yield, iv=iv)
            for k in agg:
                agg[k] += g[k]
        for stock in self.stock_legs:
            agg["delta"] += stock.quantity
        return agg

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "underlying": self.underlying,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "rate": self.rate,
            "dividend_yield": self.dividend_yield,
            "net_cash": self.net_cash,
            "notes": self.notes,
            "legs": [leg.to_dict() for leg in self.legs],
            "stock_legs": [s.to_dict() for s in self.stock_legs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        legs = [OptionLeg.from_dict(x) for x in data.get("legs", [])]
        stock_legs = [
            StockLeg.from_dict(x)
            for x in data.get("stock_legs", [])
            if x.get("kind", "stock") == "stock" or "entry_price" in x or "quantity" in x
        ]
        # Allow mixed list under legs with kind discriminators
        raw_legs = data.get("legs", [])
        if raw_legs and any(isinstance(x, dict) and x.get("kind") == "stock" for x in raw_legs):
            legs = [OptionLeg.from_dict(x) for x in raw_legs if x.get("kind", "option") != "stock"]
            stock_legs = [StockLeg.from_dict(x) for x in raw_legs if x.get("kind") == "stock"]

        return cls(
            name=data.get("name", "Position"),
            underlying=data.get("underlying", ""),
            legs=legs,
            stock_legs=stock_legs,
            net_cash=None if data.get("net_cash") is None else float(data["net_cash"]),
            as_of=data.get("as_of"),
            rate=float(data.get("rate", 0.045)),
            dividend_yield=float(data.get("dividend_yield", 0.0)),
            notes=data.get("notes", ""),
        )
