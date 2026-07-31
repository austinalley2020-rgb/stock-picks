# IBKR API setup for Options P&L Lab

If your options book lives in Interactive Brokers, **yes — use the IBKR API**.  
Manual JSON is fine for one-offs; API import gives you **avgCost fills**, live spot, and both legs without retyping.

## Which IBKR API?

| API | When to use |
|-----|-------------|
| **TWS / IB Gateway socket API** (`ib_insync`) | **Recommended for this lab.** Best for options contracts, positions, greeks/IV ticks, running next to TWS on your laptop. |
| Client Portal Web API | Lighter REST gateway; weaker for rich option-chain / combo workflows. |
| Flex Web Service | Good for scheduled report pulls (end-of-day), not interactive P&L. |

This project uses the **TWS socket API** via `ib_insync`.

## Important constraint

The API talks to **TWS or IB Gateway on localhost**.  
Run Streamlit / `fetch_ibkr.py` on the **same machine where you are logged into TWS** (usually your laptop). A remote cloud agent cannot see your local TWS socket unless you deliberately tunnel it (not recommended for live trading accounts).

## One-time TWS / Gateway setup

1. Install [Trader Workstation](https://www.interactivebrokers.com/en/trading/tws.php) or **IB Gateway**.
2. Log into **paper** first (`DU…` account) while you validate the pipeline.
3. In TWS: **Edit → Global Configuration → API → Settings**
   - ✅ **Enable ActiveX and Socket Clients**
   - ✅ **Download open orders on connection** (optional)
   - Socket port:
     - TWS paper: **7497**
     - TWS live: **7496**
     - IB Gateway paper: **4002**
     - IB Gateway live: **4001**
   - ✅ Allow connections from `127.0.0.1`
   - ✅ **Read-Only API** (recommended for this visualizer)
   - Uncheck “Create API message log file” unless debugging
4. Apply / restart TWS if prompted.
5. When a client connects the first time, **accept** the API connection dialog in TWS.

## Install & connect

```bash
pip install -r options_pnl/requirements.txt

# optional env overrides
export IBKR_HOST=127.0.0.1
export IBKR_PORT=7497          # paper TWS
export IBKR_CLIENT_ID=77
export IBKR_READONLY=1
# export IBKR_ACCOUNT=DU1234567

# smoke test
python -m options_pnl.fetch_ibkr --raw
python -m options_pnl.fetch_ibkr --underlying YOURTICKER
```

Then open the lab:

```bash
streamlit run options_pnl/app.py
```

In the sidebar choose **Load from IBKR**, set host/port, click **Connect & load**.

## What gets imported

For each underlying with option positions:

- Every **OPT** leg (qty, strike, expiry, call/put)
- **avgCost → premium / net_cash** (your average fill, not the ask — this fixes calendar breakevens)
- Optional **stock** hedge shares
- Best-effort **spot** and **IV** (needs market data permissions; delayed OPRA is enough for IV solve fallback)

Calendars appear as **two (or more) legs under the same underlying**, which is exactly what the P&L engine needs.

## Market data notes

- Position / avgCost work with a normal account login.
- Live IV / greeks ticks need an options market data subscription (or delayed data).
- If IV comes back empty, set IV manually in the UI — P&L at expiry still works from fills + BS marks.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Could not connect … 7497` | TWS not running, wrong port, or API not enabled |
| Connects then “no positions” | Wrong account selected; or filters hiding options |
| `clientId already in use` | Change `IBKR_CLIENT_ID` / sidebar client id |
| IV all null | Enable delayed market data or OPRA; or enter IV manually |
| Premiums look 100× too big | Set `IBKR_AVG_COST_MULT=1` (your build returns per-contract avgCost) |

## Security

- Prefer **paper** + **Read-Only API** while building.
- Never commit account IDs, passwords, or Flex tokens.
- Do not expose TWS API ports to the public internet.
