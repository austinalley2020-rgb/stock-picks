"""Connect to Interactive Brokers via TWS or IB Gateway (ib_insync)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class IbkrConnectionError(RuntimeError):
    """Raised when TWS / IB Gateway cannot be reached or login fails."""


@dataclass
class IbkrConfig:
    """Connection settings for the local TWS / IB Gateway socket API."""

    host: str = "127.0.0.1"
    port: int = 7497  # TWS paper. Live TWS=7496; Gateway paper=4002; Gateway live=4001
    client_id: int = 77
    account: str | None = None  # e.g. DU1234567; None = first managed account
    readonly: bool = True
    timeout: float = 8.0
    # IB avgCost for equity options is usually per-share premium. Set True if your
    # account/API build returns per-contract dollars instead.
    avg_cost_includes_multiplier: bool = False

    @classmethod
    def from_env(cls) -> IbkrConfig:
        return cls(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", "7497")),
            client_id=int(os.environ.get("IBKR_CLIENT_ID", "77")),
            account=os.environ.get("IBKR_ACCOUNT") or None,
            readonly=os.environ.get("IBKR_READONLY", "1") not in ("0", "false", "False"),
            timeout=float(os.environ.get("IBKR_TIMEOUT", "8")),
            avg_cost_includes_multiplier=os.environ.get("IBKR_AVG_COST_MULT", "0")
            in ("1", "true", "True"),
        )


def _require_ib_insync() -> Any:
    try:
        import ib_insync  # noqa: F401
        from ib_insync import IB

        return IB
    except ImportError as exc:
        raise IbkrConnectionError(
            "ib_insync is not installed. Run: pip install ib_insync"
        ) from exc


def connect(config: IbkrConfig | None = None) -> Any:
    """
    Connect to a running TWS or IB Gateway on the local machine.

    Prerequisites (on the machine running this code):
      1. TWS or IB Gateway logged in (paper or live)
      2. API enabled: Configure → API → Settings → Enable ActiveX and Socket Clients
      3. Socket port matches config.port; 127.0.0.1 trusted
      4. "Read-Only API" optional but recommended for this lab
    """
    config = config or IbkrConfig.from_env()
    IB = _require_ib_insync()
    ib = IB()
    try:
        ib.connect(
            config.host,
            config.port,
            clientId=config.client_id,
            timeout=config.timeout,
            readonly=config.readonly,
        )
    except Exception as exc:  # ib_insync raises assorted connection errors
        raise IbkrConnectionError(
            f"Could not connect to IBKR at {config.host}:{config.port} "
            f"(clientId={config.client_id}). Is TWS/IB Gateway running with "
            f"API sockets enabled?\n\nUnderlying error: {exc}"
        ) from exc

    if not ib.isConnected():
        raise IbkrConnectionError("IBKR socket connected then dropped immediately.")

    # Stash config on the client for downstream helpers
    ib._options_pnl_config = config  # type: ignore[attr-defined]
    return ib


def disconnect(ib: Any) -> None:
    if ib is None:
        return
    try:
        if getattr(ib, "isConnected", lambda: False)():
            ib.disconnect()
    except Exception:
        pass


def resolve_account(ib: Any, preferred: str | None = None) -> str:
    accounts = list(ib.managedAccounts())
    if not accounts:
        raise IbkrConnectionError("Connected, but no managed accounts returned.")
    if preferred:
        if preferred not in accounts:
            raise IbkrConnectionError(
                f"Account {preferred!r} not in managed accounts: {accounts}"
            )
        return preferred
    return accounts[0]
