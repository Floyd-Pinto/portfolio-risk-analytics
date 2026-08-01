"""
risk_metrics.py — Compute risk metrics from stored price data.

Metrics computed per ticker:

  1. Daily returns          — already computed in data_loader, re-used here.
  2. Historical VaR (95/99) — non-parametric; uses actual observed return distribution.
  3. Parametric VaR (95/99) — assumes returns are normally distributed.
  4. Rolling 30-day vol     — annualised standard deviation of returns.
  5. Maximum Drawdown       — worst peak-to-trough loss, expressed as a fraction.

All results are stored in the `risk_metrics` table (one row per ticker × date).

Interview talking points
------------------------
* Historical vs Parametric VaR:
    - Historical VaR makes NO distributional assumption — it simply finds the
      5th percentile of the actual observed returns.  It naturally captures fat
      tails, skewness, and kurtosis.  Weakness: entirely backward-looking; a
      once-in-20-years crash not in your window won't show up.
    - Parametric VaR assumes returns follow a Normal distribution parameterised
      by (μ, σ) estimated from the sample.  It's analytically tractable and
      fast, but underestimates tail risk when the real distribution has fat tails
      (which equity returns almost always do — this is called excess kurtosis or
      leptokurtosis).

* Why annualise volatility?
    Daily σ × √252 converts to an annualised figure (252 = trading days/year).
    This lets you compare volatility across assets regardless of whether you
    measured it over 30 or 252 days.

* Maximum Drawdown:
    MDD = (peak_value - trough_value) / peak_value
    It answers "how much would an investor have lost if they bought at the worst
    possible time?"  Unlike VaR, it doesn't depend on a confidence level —
    it's an extreme worst-case realised loss.
"""

import numpy as np
import pandas as pd
from scipy import stats   # for the normal distribution inverse CDF (ppf)

from db import get_engine, load_df, save_df


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TRADING_DAYS_PER_YEAR = 252   # standard convention for annualising
ROLLING_WINDOW = 30           # days for rolling volatility
Z_95 = stats.norm.ppf(0.05)  # -1.6449 — the 5th percentile of N(0,1)
Z_99 = stats.norm.ppf(0.01)  # -2.3263 — the 1st percentile of N(0,1)


# ---------------------------------------------------------------------------
# 1. Historical VaR (non-parametric)
# ---------------------------------------------------------------------------
def historical_var(returns: pd.Series, confidence: float) -> float:
    """
    Historical simulation VaR at a given confidence level.

    Method:
        Sort all observed daily returns in ascending order.
        The VaR is the return at the (1 - confidence) quantile.
        e.g. for 95% confidence -> 5th percentile of losses.

    Result is expressed as a NEGATIVE number (it's a loss).
    e.g. VaR = -0.032 means "in 95% of days, we don't lose more than 3.2%".

    Why use this?  Zero assumptions about the distribution shape.  Ideal when
    you suspect fat tails or asymmetric returns (common in equities).
    """
    quantile = 1 - confidence
    return float(np.percentile(returns.dropna(), quantile * 100))


# ---------------------------------------------------------------------------
# 2. Parametric VaR (variance-covariance)
# ---------------------------------------------------------------------------
def parametric_var(returns: pd.Series, confidence: float) -> float:
    """
    Parametric (variance-covariance) VaR at a given confidence level.

    Method:
        VaR = μ + z * σ
        where z is the inverse CDF of the standard normal at (1 - confidence).
        For 95%: z = -1.6449,  for 99%: z = -2.3263.

    Since daily mean returns (μ) are very close to zero, this simplifies
    approximately to z * σ in practice.

    Why use this?  Computationally cheap; easy to explain to management.
    Weakness: assumes normality.  Equity returns exhibit:
        - Fat tails (kurtosis > 3) -> parametric VaR understates tail risk.
        - Negative skew -> left tail is worse than the model predicts.
    Comparing parametric vs historical VaR reveals how non-normal a stock is.
    """
    mu = returns.dropna().mean()
    sigma = returns.dropna().std()

    if confidence == 0.95:
        z = Z_95
    elif confidence == 0.99:
        z = Z_99
    else:
        z = stats.norm.ppf(1 - confidence)

    return float(mu + z * sigma)


# ---------------------------------------------------------------------------
# 3. Rolling 30-day annualised volatility
# ---------------------------------------------------------------------------
def rolling_volatility(returns: pd.Series, window: int = ROLLING_WINDOW) -> pd.Series:
    """
    Compute the rolling standard deviation of returns, annualised.

    Formula:  σ_annual = σ_daily × √T  where T = 252 trading days/year.

    This √T scaling assumes daily returns are i.i.d. (independently and
    identically distributed) — a simplification that works reasonably well
    for short horizons but breaks down in trending/autocorrelated markets.

    Returns a Series of the same length as `returns` (NaN for the first
    `window - 1` rows where there's insufficient history).
    """
    daily_std = returns.rolling(window=window).std()
    return daily_std * np.sqrt(TRADING_DAYS_PER_YEAR)


# ---------------------------------------------------------------------------
# 4. Maximum Drawdown
# ---------------------------------------------------------------------------
def max_drawdown(prices: pd.Series) -> float:
    """
    Calculate the maximum drawdown over the full price series.

    Formula:
        running_peak = expanding max of cumulative price index
        drawdown     = (price - running_peak) / running_peak
        MDD          = min(drawdown)   <- most negative value

    Result is a negative number (e.g. -0.35 = a 35% peak-to-trough loss).

    Why track this?  VaR is a daily metric at a given confidence level.
    Max drawdown tells you the WORST real-world loss a long-only investor
    actually experienced during the measurement period — no statistical
    assumptions needed.
    """
    cumulative = prices / prices.iloc[0]                    # normalise to start = 1
    running_peak = cumulative.expanding().max()             # highest value seen so far
    drawdown = (cumulative - running_peak) / running_peak   # pct below peak each day
    return float(drawdown.min())                            # most negative point


# ---------------------------------------------------------------------------
# 5. Rolling max drawdown (for the metrics table — one value per date)
# ---------------------------------------------------------------------------
def rolling_max_drawdown(prices: pd.Series, window: int = ROLLING_WINDOW) -> pd.Series:
    """
    Max drawdown computed over a rolling window (same size as vol window).

    Each date's value is the worst drawdown in the preceding `window` days.
    This makes the `risk_metrics` table consistent: every column is "as of
    date d, looking back 30 days".

    For the summary report we'll also expose the global (full-period) MDD.
    """
    def _mdd(sub_series: pd.Series) -> float:
        if sub_series.isna().all():
            return np.nan
        peak = sub_series.expanding().max()
        dd = (sub_series - peak) / peak
        return dd.min()

    return prices.rolling(window=window).apply(_mdd, raw=False)


# ---------------------------------------------------------------------------
# Main computation loop
# ---------------------------------------------------------------------------
def compute_metrics() -> pd.DataFrame:
    """
    Load prices from the DB, compute all metrics, return a long-format
    DataFrame ready for insertion into `risk_metrics`.
    """
    engine = get_engine()
    prices_df = load_df("SELECT ticker, date, adj_close, daily_return FROM prices", engine)
    prices_df["date"] = pd.to_datetime(prices_df["date"])

    results = []   # one dict per (ticker, date) row

    for ticker, group in prices_df.groupby("ticker"):
        group = group.sort_values("date").copy()
        returns = group["daily_return"].astype(float)
        prices  = group["adj_close"].astype(float)

        # --- Scalar VaR metrics (use the full history, same value every date) ---
        # We store the same VaR value for every date of a ticker because
        # historical VaR is computed over the entire 2-year window, not
        # recalculated per day.  For a production system you'd use a rolling
        # lookback window instead.
        hvar95 = historical_var(returns, 0.95)
        hvar99 = historical_var(returns, 0.99)
        pvar95 = parametric_var(returns, 0.95)
        pvar99 = parametric_var(returns, 0.99)

        # --- Time-series metrics (one value per date) ---
        roll_vol = rolling_volatility(returns)               # Series aligned to group index
        roll_mdd = rolling_max_drawdown(prices)              # Series aligned to group index

        # Assemble a per-row dict for each date
        for i, (_, row) in enumerate(group.iterrows()):
            results.append({
                "ticker":          ticker,
                "date":            row["date"].date(),
                "hist_var_95":     hvar95,
                "hist_var_99":     hvar99,
                "param_var_95":    pvar95,
                "param_var_99":    pvar99,
                "rolling_vol_30d": roll_vol.iloc[i],
                "max_drawdown":    roll_mdd.iloc[i],
            })

        print(f"  [{ticker}] hVaR95={hvar95:.4f}  hVaR99={hvar99:.4f}  "
              f"pVaR95={pvar95:.4f}  MDD={max_drawdown(prices):.4f}")

    metrics_df = pd.DataFrame(results)
    return metrics_df


def run_risk_metrics() -> None:
    """Entry point: compute metrics and persist to database."""
    print("[metrics] Computing risk metrics ...")
    metrics_df = compute_metrics()
    engine = get_engine()
    save_df(metrics_df, "risk_metrics", engine)
    print("[metrics] Done.")


if __name__ == "__main__":
    run_risk_metrics()
