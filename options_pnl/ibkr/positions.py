"""Pull IBKR option positions and convert into Options P&L Lab Position objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from options_pnl.bs import implied_vol
from options_pnl.position import OptionLeg, Position, StockLeg

from .client import IbkrConfig, IbkrConnectionError, resolve_account


@dataclass
class IbkrOptionRow:
    """Normalized IBKR option (or stock) row before grouping."""

    underlying: str
    sec_type: str  # OPT / STK
    quantity: float
    avg_cost: float  # raw from IB
    premium_per_share: float | None
    strike: float | None
    expiry: date | None
    right: str | None  # C / P
    currency: str
    exchange: str
    local_symbol: str
    con_id: int
    market_price: float | None = None
    implied_vol: float | None = None
    account: str = ""


def _parse_expiry(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    if len(s) >= 10 and s[4] == "-":
        return date.fromisoformat(s[:10])
    return None


def _premium_from_avg_cost(
    avg_cost: float,
    *,
    multiplier: int,
    includes_multiplier: bool,
) -> float:
    """Return positive per-share premium magnitude from IB avgCost."""
    mag = abs(float(avg_cost))
    if includes_multiplier:
        return mag / float(multiplier or 100)
    # Heuristic: per-contract dollars are usually >> typical premiums
    if mag > 80:
        return mag / float(multiplier or 100)
    return mag


def _iter_raw_positions(ib: Any, account: str) -> list[Any]:
    # portfolio() often has richer fields than positions() after account sync
    try:
        ib.reqAccountUpdates(True, account)
        ib.sleep(1.0)
    except Exception:
        pass

    items = list(getattr(ib, "portfolio", lambda: [])() or [])
    if items:
        return items

    # Fallback: Position objects (contract, position, avgCost)
    return list(ib.positions(account) or ib.positions())


def _row_from_portfolio_item(item: Any, config: IbkrConfig) -> IbkrOptionRow | None:
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    sec = (getattr(contract, "secType", "") or "").upper()
    if sec not in ("OPT", "STK"):
        return None

    qty = float(getattr(item, "position", 0) or 0)
    if qty == 0:
        return None

    avg = float(getattr(item, "averageCost", getattr(item, "avgCost", 0.0)) or 0.0)
    mult = int(getattr(contract, "multiplier", 0) or 100)
    underlying = (
        getattr(contract, "symbol", None)
        or getattr(contract, "localSymbol", "")
        or ""
    )
    premium = None
    strike = None
    expiry = None
    right = None
    if sec == "OPT":
        premium = _premium_from_avg_cost(
            avg, multiplier=mult, includes_multiplier=config.avg_cost_includes_multiplier
        )
        strike = float(contract.strike)
        expiry = _parse_expiry(getattr(contract, "lastTradeDateOrContractMonth", None))
        right = (getattr(contract, "right", "") or "").upper()[:1]

    mkt = getattr(item, "marketPrice", None)
    return IbkrOptionRow(
        underlying=str(underlying).upper(),
        sec_type=sec,
        quantity=qty,
        avg_cost=avg,
        premium_per_share=premium,
        strike=strike,
        expiry=expiry,
        right=right,
        currency=getattr(contract, "currency", "USD") or "USD",
        exchange=getattr(contract, "exchange", "") or getattr(contract, "primaryExchange", "") or "",
        local_symbol=getattr(contract, "localSymbol", "") or "",
        con_id=int(getattr(contract, "conId", 0) or 0),
        market_price=float(mkt) if mkt not in (None, 0, -1) else None,
        account=getattr(item, "account", "") or "",
    )


def _row_from_position(pos: Any, config: IbkrConfig) -> IbkrOptionRow | None:
    # ib_insync.Position namedtuple-like: account, contract, position, avgCost
    contract = pos.contract
    sec = (contract.secType or "").upper()
    if sec not in ("OPT", "STK"):
        return None
    qty = float(pos.position)
    if qty == 0:
        return None
    avg = float(pos.avgCost)
    mult = int(contract.multiplier or 100)
    premium = None
    strike = None
    expiry = None
    right = None
    if sec == "OPT":
        premium = _premium_from_avg_cost(
            avg, multiplier=mult, includes_multiplier=config.avg_cost_includes_multiplier
        )
        strike = float(contract.strike)
        expiry = _parse_expiry(contract.lastTradeDateOrContractMonth)
        right = (contract.right or "").upper()[:1]
    return IbkrOptionRow(
        underlying=str(contract.symbol).upper(),
        sec_type=sec,
        quantity=qty,
        avg_cost=avg,
        premium_per_share=premium,
        strike=strike,
        expiry=expiry,
        right=right,
        currency=contract.currency or "USD",
        exchange=contract.exchange or contract.primaryExchange or "",
        local_symbol=contract.localSymbol or "",
        con_id=int(contract.conId or 0),
        account=getattr(pos, "account", "") or "",
    )


def list_option_rows(ib: Any, config: IbkrConfig | None = None) -> list[IbkrOptionRow]:
    config = config or getattr(ib, "_options_pnl_config", None) or IbkrConfig.from_env()
    account = resolve_account(ib, config.account)
    raw = _iter_raw_positions(ib, account)
    rows: list[IbkrOptionRow] = []
    for item in raw:
        if hasattr(item, "contract") and hasattr(item, "averageCost"):
            row = _row_from_portfolio_item(item, config)
        elif hasattr(item, "contract") and hasattr(item, "avgCost"):
            row = _row_from_position(item, config)
        else:
            row = _row_from_portfolio_item(item, config) or _row_from_position(item, config)
        if row:
            rows.append(row)
    return rows


def fetch_underlyings(ib: Any, config: IbkrConfig | None = None) -> list[str]:
    rows = list_option_rows(ib, config)
    underlyings = sorted({r.underlying for r in rows if r.sec_type == "OPT"})
    return underlyings


def _try_attach_ivs(ib: Any, rows: list[IbkrOptionRow], spot_by_und: dict[str, float]) -> None:
    """Best-effort implied vol from model / market data. Requires OPRA (or delayed)."""
    try:
        from ib_insync import Option
    except ImportError:
        return

    opt_rows = [r for r in rows if r.sec_type == "OPT" and r.expiry and r.strike and r.right]
    if not opt_rows:
        return

    contracts = []
    for r in opt_rows:
        contracts.append(
            Option(
                r.underlying,
                r.expiry.strftime("%Y%m%d"),
                float(r.strike),
                r.right,
                "SMART",
                currency=r.currency or "USD",
            )
        )
    try:
        qualified = ib.qualifyContracts(*contracts)
        tickers = []
        for c in qualified:
            tickers.append(ib.reqMktData(c, genericTickList="106", snapshot=True))
        ib.sleep(2.0)
        for r, t in zip(opt_rows, tickers):
            iv = getattr(t, "impliedVolatility", None) or getattr(t, "modelGreeks", None)
            if hasattr(iv, "impliedVol"):
                iv = iv.impliedVol
            if iv and iv > 0:
                r.implied_vol = float(iv)
            elif r.market_price and r.underlying in spot_by_und and r.expiry:
                # Fallback: solve IV from mid/mark if we have spot
                T = max((r.expiry - date.today()).days, 0) / 365.0
                otype = "call" if r.right == "C" else "put"
                solved = implied_vol(
                    float(r.market_price),
                    spot_by_und[r.underlying],
                    float(r.strike),
                    T,
                    0.045,
                    otype,
                )
                if solved == solved:  # not NaN
                    r.implied_vol = float(solved)
            try:
                ib.cancelMktData(t.contract)
            except Exception:
                pass
    except Exception:
        # Market data permissions vary; IV is optional for P&L if user supplies it.
        return


def _spot_for_underlying(ib: Any, symbol: str, currency: str = "USD") -> float | None:
    try:
        from ib_insync import Stock

        contract = Stock(symbol, "SMART", currency)
        ib.qualifyContracts(contract)
        t = ib.reqMktData(contract, snapshot=True)
        ib.sleep(1.5)
        px = t.marketPrice()
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
        if px and px == px and px > 0:
            return float(px)
        if t.last and t.last > 0:
            return float(t.last)
        if t.close and t.close > 0:
            return float(t.close)
    except Exception:
        return None
    return None


def position_from_ibkr_legs(
    rows: list[IbkrOptionRow],
    *,
    underlying: str,
    name: str | None = None,
    include_stock: bool = True,
    rate: float = 0.045,
) -> Position:
    """Build a Position for one underlying from normalized IBKR rows."""
    und = underlying.upper()
    opt_rows = [r for r in rows if r.underlying == und and r.sec_type == "OPT"]
    if not opt_rows:
        raise IbkrConnectionError(f"No option positions found for underlying {und}")

    legs: list[OptionLeg] = []
    for r in opt_rows:
        if not r.expiry or r.strike is None or not r.right:
            continue
        otype = "call" if r.right == "C" else "put"
        qty = int(round(r.quantity))
        legs.append(
            OptionLeg(
                option_type=otype,
                strike=float(r.strike),
                expiry=r.expiry,
                quantity=qty,
                premium=r.premium_per_share,
                iv=r.implied_vol,
                label=r.local_symbol or f"{qty}x {r.expiry} {r.strike:g} {otype}",
            )
        )

    stock_legs: list[StockLeg] = []
    if include_stock:
        for r in rows:
            if r.underlying == und and r.sec_type == "STK":
                stock_legs.append(
                    StockLeg(
                        quantity=int(round(r.quantity)),
                        entry_price=abs(r.avg_cost) if r.avg_cost else None,
                    )
                )

    # Net cash from IB avg costs (actual fills / average) — this is the key fix vs ask.
    net_cash = 0.0
    for leg in legs:
        net_cash += leg.cash_flow()
    for s in stock_legs:
        net_cash += s.cash_flow()

    return Position(
        name=name or f"IBKR {und}",
        underlying=und,
        legs=legs,
        stock_legs=stock_legs,
        net_cash=net_cash,
        as_of=date.today(),
        rate=rate,
        notes=(
            f"Imported from IBKR avgCost for {und}. "
            f"net_cash={net_cash:.2f} uses your average fills, not the current ask."
        ),
    )


def fetch_option_books(
    ib: Any,
    *,
    underlying: str | None = None,
    config: IbkrConfig | None = None,
    fetch_iv: bool = True,
    fetch_spot: bool = True,
) -> tuple[list[Position], dict[str, float]]:
    """
    Return Positions grouped by underlying, plus a spot dict.

    If ``underlying`` is set, only that book is returned.
    """
    config = config or getattr(ib, "_options_pnl_config", None) or IbkrConfig.from_env()
    rows = list_option_rows(ib, config)
    if not rows:
        raise IbkrConnectionError(
            "Connected to IBKR, but no OPT/STK positions were returned. "
            "Confirm the account has open options and API is not filtered."
        )

    unds = sorted({r.underlying for r in rows if r.sec_type == "OPT"})
    if underlying:
        unds = [underlying.upper()]
        if unds[0] not in {r.underlying for r in rows if r.sec_type == "OPT"}:
            raise IbkrConnectionError(
                f"No option positions for {underlying}. Available: "
                + ", ".join(sorted({r.underlying for r in rows if r.sec_type == "OPT"}) or ["(none)"])
            )

    spots: dict[str, float] = {}
    if fetch_spot:
        for u in unds:
            currency = next((r.currency for r in rows if r.underlying == u), "USD")
            px = _spot_for_underlying(ib, u, currency)
            if px is not None:
                spots[u] = px

    if fetch_iv:
        _try_attach_ivs(ib, rows, spots)

    books = [position_from_ibkr_legs(rows, underlying=u) for u in unds]
    return books, spots


def summarize_rows(rows: list[IbkrOptionRow]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "underlying": r.underlying,
                "sec": r.sec_type,
                "qty": r.quantity,
                "localSymbol": r.local_symbol,
                "strike": r.strike,
                "expiry": r.expiry.isoformat() if r.expiry else None,
                "right": r.right,
                "avgCost_raw": r.avg_cost,
                "premium/share": r.premium_per_share,
                "iv": r.implied_vol,
                "conId": r.con_id,
            }
        )
    return out
