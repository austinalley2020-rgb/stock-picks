# Options P&L Lab

Interactive infrastructure to visualize **multi-leg options P&L**, breakevens, and greeks from **your actual fills** — not IBKR’s ask/mid or front-leg-only payoff plot.

## Why this exists

Broker risk graphs often fail on structures like **call calendar spreads**:

1. **Fill price** — You may open at a debit well inside the ask. Using the displayed ask shifts the whole P&L curve and misstates breakevens.
2. **Multi-expiry** — At front-month expiry the short leg dies, but the long back-month still has **extrinsic value**. Plotting only the front call’s intrinsic payoff is wrong.

This toolkit marks remaining live legs with Black–Scholes (per-leg IV), applies your **net open cash**, and plots interactive curves / heatmaps.

## Quick start

```bash
pip install -r options_pnl/requirements.txt
streamlit run options_pnl/app.py
```

### IBKR users (recommended)

If your book is in Interactive Brokers, import fills via the **TWS / IB Gateway API** instead of typing legs:

1. Follow **[IBKR_SETUP.md](./IBKR_SETUP.md)** (enable API sockets in TWS).
2. In the app sidebar choose **Load from IBKR** → Connect.
3. Or CLI: `python -m options_pnl.fetch_ibkr --underlying TICKER`

`avgCost` from IBKR becomes `net_cash`, so calendar breakevens use **your fill**, not the ask.

Or from Python:

```python
from options_pnl.io import load_position
from options_pnl import analyze_position

pos = load_position("options_pnl/examples/call_calendar.json")
# Override if your real debit differs from summed premiums:
pos.net_cash = -85.0  # dollars paid (negative = debit)

report = analyze_position(pos, spot=100.0)
print("P&L @ spot:", report.current_pnl)
print("Greeks:", report.greeks)
print("Breakevens (marked today):", report.curves[0].breakevens)
print("Breakevens @ front expiry:", report.curves[1].breakevens)
```

## Position JSON

```json
{
  "name": "Call Calendar",
  "underlying": "XYZ",
  "as_of": "2026-07-31",
  "net_cash": -85.0,
  "legs": [
    {
      "option_type": "call",
      "strike": 100,
      "expiry": "2026-08-21",
      "quantity": -1,
      "premium": 2.40,
      "iv": 0.32,
      "label": "short front"
    },
    {
      "option_type": "call",
      "strike": 100,
      "expiry": "2026-09-18",
      "quantity": 1,
      "premium": 3.55,
      "iv": 0.30,
      "label": "long back"
    }
  ]
}
```

- `quantity`: contracts; negative = short  
- `premium`: per-share fill (optional if `net_cash` is set)  
- `net_cash`: **actual dollars** at open (negative = debit). This is what fixes IBKR’s wrong breakeven when you filled inside the ask.  
- `iv`: annualized vol used to mark the leg before expiry  

## What the UI shows

| View | Purpose |
|------|---------|
| P&L curves | Marked today, at each expiry (remaining legs valued), plus naive intrinsic overlay |
| Heatmaps | Spot × IV and Spot × time into front expiry |
| Legs & greeks | Per-leg marks, Δ/Γ/Θ/ν, aggregate position greeks |
| Export | JSON + Python snippet to reproduce the book |

## Tests

```bash
pytest options_pnl/tests -q
```

## Scope / next extensions

Already covered: European BS marks, multi-leg + stock, calendars, actual net debit, greeks, breakeven finder, interactive Streamlit.

Natural follow-ons: American early-exercise adjustment, vol smile by strike/expiry, combo BAG decomposition, Flex end-of-day snapshots, earnings IV-crush scenarios.
