"""Interactive options P&L visualizer (Streamlit).

Run:
  streamlit run options_pnl/app.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

# Allow running as `streamlit run options_pnl/app.py` from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from options_pnl.io import load_position
from options_pnl.pnl import analyze_position, pnl_curve, pnl_surface
from options_pnl.position import OptionLeg, Position

st.set_page_config(
    page_title="Options P&L Lab",
    page_icon="◎",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Libre+Franklin:wght@500;700;800&display=swap');

  :root {
    --ink: #14201c;
    --muted: #5c6b64;
    --line: #c9d4ce;
    --panel: rgba(255,255,255,0.72);
    --gain: #0f7a4c;
    --loss: #b42318;
    --accent: #2f6f57;
  }

  .stApp {
    background:
      radial-gradient(1200px 600px at 10% -10%, #d7ebe1 0%, transparent 55%),
      radial-gradient(900px 500px at 100% 0%, #e7efd8 0%, transparent 50%),
      linear-gradient(180deg, #eef3ee 0%, #e3ebe4 45%, #dfe8e2 100%);
    color: #14201c;
    font-family: 'DM Sans', sans-serif;
  }

  h1, h2, h3, .brand {
    font-family: 'Libre Franklin', sans-serif !important;
    letter-spacing: -0.03em;
  }

  .brand-wrap {
    animation: rise 0.7s ease both;
    margin-bottom: 0.25rem;
  }
  .brand {
    font-size: 2.6rem;
    font-weight: 800;
    margin: 0;
    color: #14201c;
  }
  .brand-sub {
    color: #5c6b64;
    font-size: 1.05rem;
    max-width: 52rem;
    margin-top: 0.35rem;
  }

  .metric-strip {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
    margin: 1rem 0 1.25rem;
    animation: rise 0.8s ease both;
  }
  .metric {
    background: var(--panel);
    border: 1px solid #c9d4ce;
    border-radius: 10px;
    padding: 0.9rem 1rem;
    backdrop-filter: blur(6px);
  }
  .metric .k {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #5c6b64;
    font-weight: 600;
  }
  .metric .v {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.35rem;
    font-weight: 500;
    margin-top: 0.25rem;
  }
  .pos { color: #0f7a4c; }
  .neg { color: #b42318; }

  .note {
    border-left: 3px solid #2f6f57;
    background: rgba(255,255,255,0.55);
    padding: 0.75rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.95rem;
  }

  @keyframes rise {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }

  @media (max-width: 900px) {
    .metric-strip { grid-template-columns: 1fr 1fr; }
    .brand { font-size: 2rem; }
  }
</style>
"""


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _cls(x: float) -> str:
    return "pos" if x >= 0 else "neg"


def default_calendar() -> Position:
    example = Path(__file__).parent / "examples" / "call_calendar.json"
    return load_position(example)


def build_position_from_ui() -> tuple[Position, float]:
    st.sidebar.header("Position")
    source = st.sidebar.radio(
        "Input source",
        ["Example call calendar", "Edit legs", "Paste JSON"],
        index=0,
    )

    spot = st.sidebar.number_input("Underlying spot", min_value=0.01, value=100.0, step=0.5)

    if source == "Paste JSON":
        raw = st.sidebar.text_area(
            "Position JSON",
            value=json.dumps(default_calendar().to_dict(), indent=2),
            height=360,
        )
        pos = Position.from_dict(json.loads(raw))
        return pos, float(spot)

    if source == "Example call calendar":
        pos = default_calendar()
        st.sidebar.caption(pos.notes or "Loaded example calendar.")
        # Allow overriding actual fill debit — the IBKR pain point
        debit = st.sidebar.number_input(
            "Actual net debit paid ($)",
            min_value=0.0,
            value=float(pos.net_debit),
            step=5.0,
            help="Dollars you actually paid to open. Overrides leg mid/ask math.",
        )
        pos.net_cash = -abs(debit)
        return pos, float(spot)

    # Edit legs
    name = st.sidebar.text_input("Name", "My position")
    underlying = st.sidebar.text_input("Underlying", "TICKER")
    n_legs = st.sidebar.number_input("Number of legs", min_value=1, max_value=8, value=2)
    use_net = st.sidebar.checkbox("Override with actual net debit/credit", value=True)
    net_debit = st.sidebar.number_input("Net debit paid ($)", min_value=0.0, value=85.0, step=5.0)

    legs: list[OptionLeg] = []
    today = date.today()
    for i in range(int(n_legs)):
        st.sidebar.markdown(f"**Leg {i+1}**")
        c1, c2 = st.sidebar.columns(2)
        with c1:
            otype = st.selectbox(f"Type {i+1}", ["call", "put"], key=f"t{i}")
            side = st.selectbox(f"Side {i+1}", ["long", "short"], key=f"s{i}", index=1 if i == 0 else 0)
        with c2:
            strike = st.number_input(f"Strike {i+1}", value=100.0, key=f"k{i}")
            qty = st.number_input(f"Contracts {i+1}", min_value=1, value=1, key=f"q{i}")
        expiry = st.sidebar.date_input(
            f"Expiry {i+1}",
            value=today + timedelta(days=21 if i == 0 else 49),
            key=f"e{i}",
        )
        premium = st.sidebar.number_input(f"Fill premium {i+1}", value=2.40 if i == 0 else 3.55, key=f"p{i}")
        iv = st.sidebar.number_input(f"IV {i+1}", value=0.32 if i == 0 else 0.30, min_value=0.01, max_value=3.0, key=f"iv{i}")
        signed = int(qty) if side == "long" else -int(qty)
        legs.append(
            OptionLeg(
                option_type=otype,
                strike=float(strike),
                expiry=expiry if isinstance(expiry, date) else date.fromisoformat(str(expiry)),
                quantity=signed,
                premium=float(premium),
                iv=float(iv),
            )
        )

    pos = Position(
        name=name,
        underlying=underlying,
        legs=legs,
        net_cash=-abs(net_debit) if use_net else None,
        as_of=today,
    )
    return pos, float(spot)


def plot_pnl_curves(analysis, spot: float) -> go.Figure:
    fig = go.Figure()
    colors = ["#1f4d3a", "#3d7a5f", "#8aa88f", "#b42318"]
    for i, curve in enumerate(analysis.curves):
        fig.add_trace(
            go.Scatter(
                x=curve.spots,
                y=curve.pnl,
                mode="lines",
                name=curve.label,
                line=dict(width=3 if i == 0 else 2, color=colors[i % len(colors)], dash="solid" if i < 3 else "dot"),
                hovertemplate="Spot %{x:.2f}<br>P&L %{y:$,.2f}<extra></extra>",
            )
        )
        for be in curve.breakevens:
            fig.add_vline(x=be, line_width=1, line_dash="dash", line_color="rgba(20,32,28,0.25)")

    fig.add_hline(y=0, line_width=1, line_color="rgba(20,32,28,0.35)")
    fig.add_vline(x=spot, line_width=2, line_color="#c45c26", annotation_text="spot")

    # Annotate primary curve breakevens
    primary = analysis.curves[0]
    for be in primary.breakevens:
        fig.add_annotation(
            x=be,
            y=0,
            text=f"BE {be:.2f}",
            showarrow=True,
            arrowhead=2,
            ay=-30,
            font=dict(size=11, color="#14201c"),
        )

    fig.update_layout(
        title=dict(text=f"{analysis.position.name} — P&L vs underlying", font=dict(size=18)),
        xaxis_title="Underlying price",
        yaxis_title="P&L ($)",
        template="plotly_white",
        height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=40, r=20, t=80, b=40),
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.65)",
        font=dict(family="DM Sans"),
    )
    return fig


def plot_surface(position: Position, spot: float, mode: str, base_iv: float | None) -> go.Figure:
    lo, hi = spot * 0.75, spot * 1.25
    spots = np.linspace(lo, hi, 61)
    if mode == "IV × Spot":
        surf = pnl_surface(position, spots, mode="iv")
        fig = go.Figure(
            data=go.Heatmap(
                x=surf["spots"],
                y=surf["ivs"] * 100,
                z=surf["pnl"],
                colorscale="RdYlGn",
                zmid=0,
                colorbar=dict(title="P&L $"),
                hovertemplate="Spot %{x:.2f}<br>IV %{y:.1f}%<br>P&L %{z:$,.0f}<extra></extra>",
            )
        )
        fig.update_layout(
            title="P&L heatmap — spot vs IV (flat vol shock)",
            xaxis_title="Underlying",
            yaxis_title="IV (%)",
            height=480,
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="DM Sans"),
        )
    else:
        surf = pnl_surface(position, spots, mode="time", base_iv=base_iv)
        y = [d.isoformat() for d in surf["dates"]]
        fig = go.Figure(
            data=go.Heatmap(
                x=surf["spots"],
                y=y,
                z=surf["pnl"],
                colorscale="RdYlGn",
                zmid=0,
                colorbar=dict(title="P&L $"),
                hovertemplate="Spot %{x:.2f}<br>Date %{y}<br>P&L %{z:$,.0f}<extra></extra>",
            )
        )
        fig.update_layout(
            title="P&L heatmap — spot vs calendar date (to front expiry)",
            xaxis_title="Underlying",
            yaxis_title="As-of date",
            height=480,
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="DM Sans"),
        )
    return fig


def plot_greeks_bars(greeks: dict[str, float]) -> go.Figure:
    labels = ["Delta (shares)", "Gamma", "Theta / day", "Vega / vol-pt", "Rho / rate-pt"]
    keys = ["delta", "gamma", "theta", "vega", "rho"]
    vals = [greeks[k] for k in keys]
    colors = ["#0f7a4c" if v >= 0 else "#b42318" for v in vals]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=vals,
            marker_color=colors,
            text=[f"{v:,.2f}" for v in vals],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="Position greeks (scaled by contracts × multiplier)",
        height=360,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.65)",
        font=dict(family="DM Sans"),
        margin=dict(t=60, b=40),
        yaxis_title="Value",
    )
    return fig


def main() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="brand-wrap">
          <p class="brand">Options P&L Lab</p>
          <p class="brand-sub">
            Interactive multi-leg P&amp;L, breakevens, and greeks — including calendar spreads
            where the back-month still has time value at front expiry. Uses your actual fill debit,
            not the broker ask.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    position, spot = build_position_from_ui()

    st.sidebar.header("Scenario")
    as_of = st.sidebar.date_input("Analysis as-of", value=position.as_of or date.today())
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    flat_iv = st.sidebar.checkbox("Shock all legs to one IV", value=False)
    iv_override = None
    if flat_iv:
        iv_override = st.sidebar.slider("Flat IV", 0.05, 1.5, 0.30, 0.01)
    range_pct = st.sidebar.slider("Spot chart range (±%)", 10, 50, 30)
    spot_range = (spot * (1 - range_pct / 100), spot * (1 + range_pct / 100))

    analysis = analyze_position(
        position,
        spot=spot,
        iv_override=iv_override,
        as_of=as_of,
        spot_range=spot_range,
    )

    # Metric strip
    g = analysis.greeks
    st.markdown(
        f"""
        <div class="metric-strip">
          <div class="metric"><div class="k">Open cash</div>
            <div class="v {_cls(analysis.open_cash)}">{_money(analysis.open_cash)}</div></div>
          <div class="metric"><div class="k">Marked P&L @ spot</div>
            <div class="v {_cls(analysis.current_pnl)}">{_money(analysis.current_pnl)}</div></div>
          <div class="metric"><div class="k">Delta (shares)</div>
            <div class="v">{g['delta']:,.1f}</div></div>
          <div class="metric"><div class="k">Theta / day</div>
            <div class="v {_cls(g['theta'])}">{_money(g['theta'])}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for note in analysis.notes:
        st.markdown(f'<div class="note">{note}</div>', unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["P&L curves", "Heatmap", "Legs & greeks", "Export"])

    with tab1:
        st.plotly_chart(plot_pnl_curves(analysis, spot), use_container_width=True)
        cols = st.columns(len(analysis.curves))
        for col, curve in zip(cols, analysis.curves):
            with col:
                be_txt = ", ".join(f"{b:.2f}" for b in curve.breakevens) or "—"
                st.markdown(f"**{curve.label}**")
                st.write(f"Breakevens: `{be_txt}`")
                st.write(f"Max (in range): `{_money(curve.max_profit)}`")
                st.write(f"Min (in range): `{_money(curve.max_loss)}`")

    with tab2:
        mode = st.radio("Surface", ["IV × Spot", "Time × Spot"], horizontal=True)
        base_iv = iv_override
        if base_iv is None and position.legs:
            ivs = [leg.iv for leg in position.legs if leg.iv is not None]
            base_iv = float(np.mean(ivs)) if ivs else 0.3
        st.plotly_chart(plot_surface(position, spot, mode, base_iv), use_container_width=True)
        st.caption(
            "Calendars are typically long vega / short theta into front expiry. "
            "The time surface shows how the P&L shape evolves as the front leg decays."
        )

    with tab3:
        left, right = st.columns([1.2, 1])
        with left:
            st.subheader("Legs")
            rows = []
            for row in analysis.leg_marks:
                rows.append(
                    {
                        "Leg": row["label"],
                        "Expiry": row["expiry"],
                        "DTE": row["dte"],
                        "Qty": row["qty"],
                        "Fill": row["fill"],
                        "IV": row["iv"],
                        "Mark": round(row["mark"], 4),
                        "Value $": round(row["value"], 2),
                        "Leg P&L $": round(row["leg_pnl"], 2),
                        "Δ": round(row["greeks"]["delta"], 2),
                        "Θ": round(row["greeks"]["theta"], 2),
                        "ν": round(row["greeks"]["vega"], 2),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
        with right:
            st.plotly_chart(plot_greeks_bars(g), use_container_width=True)

        # Front-expiry explanation card
        if analysis.nearest_expiry and len({leg.expiry for leg in position.legs}) > 1:
            front = analysis.nearest_expiry
            front_curve = next((c for c in analysis.curves if c.as_of == front), None)
            st.subheader("Why IBKR looks wrong on calendars")
            st.markdown(
                f"""
At **{front.isoformat()}** (front expiry), the short leg settles to intrinsic, but the long
back-month option is **still alive** and keeps extrinsic value. Plotting only the front call's
expiration payoff — or using ask instead of your fill — misstates breakevens and profit zones.

Your actual open cash of **{_money(analysis.open_cash)}** is what shifts the whole P&L vertically.
                """
            )
            if front_curve:
                be = ", ".join(f"{b:.2f}" for b in front_curve.breakevens) or "none in range"
                st.info(f"Model breakevens at front expiry (back leg marked): **{be}**")

    with tab4:
        st.subheader("Position JSON")
        st.code(json.dumps(position.to_dict(), indent=2), language="json")
        st.subheader("Python snippet")
        st.code(
            f"""from options_pnl import Position, analyze_position
from options_pnl.io import load_position

pos = Position.from_dict({json.dumps(position.to_dict(), indent=2)})
report = analyze_position(pos, spot={spot})
print(report.current_pnl, report.greeks, report.curves[0].breakevens)
""",
            language="python",
        )


if __name__ == "__main__":
    main()
