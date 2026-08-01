"""
main.py — Orchestrator: runs the full pipeline end-to-end.

Steps
-----
  1. Fetch prices from Yahoo Finance -> SQLite (data_loader)
  2. Compute risk metrics                -> SQLite (risk_metrics)
  3. Print a summary report to console  (ranked by historical 95% VaR)
  4. Save summary as risk_summary.csv
  5. Plot VaR comparison chart           (saved as var_comparison.png)

Run with:
    python main.py
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from pathlib import Path

from data_loader import run_data_loader
from risk_metrics import run_risk_metrics, max_drawdown
from db import get_engine, load_df


# ---------------------------------------------------------------------------
# 3. Summary report — one row per ticker, ranked by risk
# ---------------------------------------------------------------------------
def print_summary_report(engine) -> pd.DataFrame:
    """
    Build a one-row-per-ticker summary table with:
      - Historical VaR (95% and 99%)
      - Parametric VaR (95% and 99%)
      - Average 30-day rolling volatility
      - Full-period maximum drawdown (computed fresh from prices table)

    Ranked by hist_var_95 ascending (most negative = riskiest at top).
    """
    # Pull the scalar VaR columns — they're the same for every date per ticker,
    # so we just grab the first occurrence per ticker.
    var_sql = """
        SELECT
            ticker,
            hist_var_95,
            hist_var_99,
            param_var_95,
            param_var_99,
            AVG(rolling_vol_30d) AS avg_rolling_vol
        FROM risk_metrics
        GROUP BY ticker
        ORDER BY hist_var_95 ASC   -- most negative = highest loss = riskiest
    """
    summary = load_df(var_sql, engine)

    # Full-period max drawdown — pull from prices table and compute globally
    prices_sql = "SELECT ticker, date, adj_close FROM prices ORDER BY ticker, date"
    prices_df  = load_df(prices_sql, engine)

    mdd_rows = []
    for ticker, grp in prices_df.groupby("ticker"):
        mdd_rows.append({
            "ticker": ticker,
            "full_period_mdd": max_drawdown(grp["adj_close"].astype(float))
        })
    mdd_df = pd.DataFrame(mdd_rows)

    summary = summary.merge(mdd_df, on="ticker")

    # ---- Pretty-print -------------------------------------------------------
    print("\n" + "=" * 80)
    print("  PORTFOLIO RISK ANALYTICS — TICKER SUMMARY (ranked by 95% Historical VaR)")
    print("=" * 80)
    display = summary.copy()
    for col in ["hist_var_95", "hist_var_99", "param_var_95", "param_var_99",
                "avg_rolling_vol", "full_period_mdd"]:
        display[col] = display[col].map(lambda x: f"{x:+.4f}")

    display.columns = [
        "Ticker", "Hist VaR 95%", "Hist VaR 99%",
        "Param VaR 95%", "Param VaR 99%",
        "Avg Roll Vol", "Full-Period MDD"
    ]
    print(display.to_string(index=False))
    print("=" * 80)
    print("Note: VaR values are daily loss fractions (e.g. -0.03 = 3% loss).")
    print("      A more negative VaR means higher risk.\n")

    return summary


def save_summary_csv(summary: pd.DataFrame, path: str = "risk_summary.csv") -> None:
    summary.to_csv(path, index=False)
    print(f"[main] Summary saved -> {path}")


# ---------------------------------------------------------------------------
# 4. VaR comparison chart
# ---------------------------------------------------------------------------
def plot_var_comparison(summary: pd.DataFrame, path: str = "var_comparison.png") -> None:
    """
    Horizontal bar chart comparing Historical vs Parametric VaR at 95%
    across all tickers.

    Why horizontal bars?  Ticker names are long (RELIANCE.NS etc.) and are
    easier to read on the y-axis than rotated x-axis labels.

    The chart makes it easy to visually identify:
      - Which tickers have the widest spread between Historical and Parametric
        VaR (indicating heavier-than-normal tails).
      - Relative risk ranking of the portfolio.
    """
    df = summary.sort_values("hist_var_95", ascending=True)   # riskiest at top after flip

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    y = range(len(df))
    bar_height = 0.35
    # Convert to positive for display (loss magnitude)
    hist_vals  = (-df["hist_var_95"]).values
    param_vals = (-df["param_var_95"]).values

    bars1 = ax.barh(
        [i + bar_height / 2 for i in y], hist_vals, bar_height,
        label="Historical VaR 95%", color="#e94560", alpha=0.9
    )
    bars2 = ax.barh(
        [i - bar_height / 2 for i in y], param_vals, bar_height,
        label="Parametric VaR 95%", color="#0f3460", alpha=0.9,
        edgecolor="#e94560", linewidth=0.8
    )

    # Value labels on bars
    for bar in bars1:
        ax.text(
            bar.get_width() + 0.001, bar.get_y() + bar.get_height() / 2,
            f"{bar.get_width():.3f}", va="center", ha="left",
            color="white", fontsize=8
        )
    for bar in bars2:
        ax.text(
            bar.get_width() + 0.001, bar.get_y() + bar.get_height() / 2,
            f"{bar.get_width():.3f}", va="center", ha="left",
            color="#a8a8b3", fontsize=8
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels(df["ticker"].tolist(), color="white", fontsize=10)
    ax.set_xlabel("Daily Loss (fraction of portfolio value)", color="white", fontsize=11)
    ax.set_title(
        "95% VaR Comparison: Historical vs Parametric\n"
        "(Higher bar = riskier; gap = fat-tail effect)",
        color="white", fontsize=13, pad=15
    )
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(xmax=1, decimals=1))
    ax.tick_params(axis="x", colors="white")
    ax.spines[:].set_color("#444")
    ax.legend(
        facecolor="#0f3460", edgecolor="#e94560", labelcolor="white", fontsize=10
    )
    ax.grid(axis="x", color="#333", linestyle="--", alpha=0.5)

    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"[main] Chart saved -> {path}")


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------
def main():
    print(f"\n{'='*60}")
    print("  Portfolio Risk Analytics Pipeline")
    print(f"{'='*60}\n")

    # Step 1 — fetch and store prices
    run_data_loader()

    # Step 2 — compute and store metrics
    run_risk_metrics()

    # Step 3 + 4 — report and chart
    engine = get_engine()
    summary = print_summary_report(engine)
    save_summary_csv(summary)
    plot_var_comparison(summary)

    print("\n[main] All done! Files written:")
    print("  * risk_data.db       — SQLite database (prices + risk_metrics tables)")
    print("  * risk_summary.csv   — ticker risk ranking")
    print("  * var_comparison.png — VaR bar chart")
    print("\nTo explore the data interactively:")
    print("  sqlite3 risk_data.db")
    print("  .read queries/top_riskiest_tickers.sql")


if __name__ == "__main__":
    main()
