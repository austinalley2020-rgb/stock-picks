#!/usr/bin/env python3
"""CLI: connect to local TWS/IB Gateway and print option books as JSON.

Usage (on the machine where TWS is logged in):
  export IBKR_PORT=7497   # paper TWS
  python -m options_pnl.fetch_ibkr
  python -m options_pnl.fetch_ibkr --underlying AAPL
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_pnl.ibkr.client import IbkrConfig, IbkrConnectionError, connect, disconnect
from options_pnl.ibkr.positions import fetch_option_books, list_option_rows, summarize_rows


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch IBKR option positions into Options P&L Lab JSON")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--client-id", type=int, default=None)
    p.add_argument("--account", default=None)
    p.add_argument("--underlying", default=None, help="Filter to one underlying symbol")
    p.add_argument("--no-iv", action="store_true", help="Skip market-data IV fetch")
    p.add_argument("--raw", action="store_true", help="Print raw normalized rows only")
    args = p.parse_args()

    cfg = IbkrConfig.from_env()
    if args.host:
        cfg.host = args.host
    if args.port is not None:
        cfg.port = args.port
    if args.client_id is not None:
        cfg.client_id = args.client_id
    if args.account:
        cfg.account = args.account

    ib = None
    try:
        print(f"Connecting to {cfg.host}:{cfg.port} clientId={cfg.client_id} ...", file=sys.stderr)
        ib = connect(cfg)
        accounts = ib.managedAccounts()
        print(f"Connected. Accounts: {accounts}", file=sys.stderr)

        if args.raw:
            rows = list_option_rows(ib, cfg)
            print(json.dumps(summarize_rows(rows), indent=2))
            return 0

        books, spots = fetch_option_books(
            ib,
            underlying=args.underlying,
            config=cfg,
            fetch_iv=not args.no_iv,
        )
        payload = {
            "spots": spots,
            "positions": [b.to_dict() for b in books],
        }
        print(json.dumps(payload, indent=2))
        return 0
    except IbkrConnectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nSetup checklist:\n"
            "  1. Open TWS or IB Gateway and log in\n"
            "  2. Configure → API → Settings → Enable ActiveX and Socket Clients\n"
            "  3. Trust 127.0.0.1; set socket port (7497 paper TWS / 7496 live)\n"
            "  4. Bypass order precautions for API if prompted\n"
            "  5. Re-run this command on the SAME machine as TWS\n",
            file=sys.stderr,
        )
        return 1
    finally:
        disconnect(ib)


if __name__ == "__main__":
    raise SystemExit(main())
