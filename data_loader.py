"""
data_loader.py — Fetch historical prices from Yahoo Finance and persist them.

Responsibilities:
  - Define the list of tickers to track.
  - Pull 2 years of daily adjusted-close prices via yfinance.
  - Fall back to Geometric Brownian Motion simulation if Yahoo rate-limits us.
  - Compute daily returns (for use in risk calculations).
  - Store the resulting DataFrame into the `prices` table via db.save_df().

Why adjusted close?  Raw prices are distorted by stock splits and dividend
payments.  Adjusted close applies these corporate actions retroactively so
that returns are comparable across time.

Why GBM as a fallback?
  Geometric Brownian Motion is the stochastic process underlying the
  Black-Scholes model.  It models price as:
      S(t) = S(0) * exp((mu - sigma^2/2)*t + sigma*sqrt(t)*Z)
  where Z ~ N(0,1).  The parameters (mu, sigma) are calibrated to
  historically reasonable values for each stock.  This lets the project run
  end-to-end even when Yahoo Finance rate-limits the environment.
"""

import time

import numpy as np
import yfinance as yf
import pandas as pd

from db import get_engine, create_tables, save_df


# ---------------------------------------------------------------------------
# Ticker universe — mix of Indian large-caps (.NS suffix = NSE) and US equities
# ---------------------------------------------------------------------------
TICKERS = [
    "RELIANCE.NS",   # Reliance Industries — energy/petrochemicals conglomerate
    "TCS.NS",        # Tata Consultancy Services — IT services
    "HDFCBANK.NS",   # HDFC Bank — India's largest private-sector bank
    "INFY.NS",       # Infosys — IT services (different beta to TCS)
    "WIPRO.NS",      # Wipro — IT, adds diversification within sector
    "ICICIBANK.NS",  # ICICI Bank — second large private bank
    "AAPL",          # Apple — large-cap US tech (USD-denominated)
    "MSFT",          # Microsoft — large-cap US tech, lower beta than AAPL
    "GOOGL",         # Alphabet — US tech with ad-revenue exposure
]

PERIOD_YEARS = 2      # how far back to fetch data
DELAY_BETWEEN = 3     # seconds between ticker requests (polite to Yahoo)
MAX_RETRIES   = 2     # retry attempts per ticker before giving up


# ---------------------------------------------------------------------------
# GBM parameters — calibrated to approximate historical values per stock
# Used only when Yahoo Finance is unavailable.
# ---------------------------------------------------------------------------
GBM_PARAMS = {
    "RELIANCE.NS":  {"mu": 0.12, "sigma": 0.25, "s0": 2800.0},
    "TCS.NS":       {"mu": 0.15, "sigma": 0.22, "s0": 3900.0},
    "HDFCBANK.NS":  {"mu": 0.10, "sigma": 0.20, "s0": 1700.0},
    "INFY.NS":      {"mu": 0.14, "sigma": 0.23, "s0": 1800.0},
    "WIPRO.NS":     {"mu": 0.09, "sigma": 0.26, "s0":  450.0},
    "ICICIBANK.NS": {"mu": 0.16, "sigma": 0.24, "s0": 1200.0},
    "AAPL":         {"mu": 0.22, "sigma": 0.28, "s0":  185.0},
    "MSFT":         {"mu": 0.20, "sigma": 0.24, "s0":  375.0},
    "GOOGL":        {"mu": 0.17, "sigma": 0.26, "s0":  140.0},
}


# ---------------------------------------------------------------------------
# Live data fetch (yfinance)
# ---------------------------------------------------------------------------
def _fetch_one_live(ticker: str, years: int, attempt: int = 1) -> "pd.DataFrame | None":
    """
    Fetch history for a single ticker using yf.Ticker.history().
    Returns a DataFrame with [ticker, date, adj_close] or None on failure.
    """
    try:
        raw = yf.Ticker(ticker).history(
            period=f"{years}y",
            interval="1d",
            auto_adjust=True,
        )

        if raw.empty:
            return None

        close = raw[["Close"]].copy()
        close.index = pd.to_datetime(close.index).date
        close.columns = ["adj_close"]
        close["ticker"] = ticker
        close = close.reset_index()
        close.rename(columns={close.columns[0]: "date"}, inplace=True)
        return close[["ticker", "date", "adj_close"]]

    except Exception as exc:
        if attempt < MAX_RETRIES:
            backoff = 5 * attempt
            print(f"  [{ticker}] attempt {attempt} failed ({exc.__class__.__name__}) "
                  f"— retrying in {backoff}s ...")
            time.sleep(backoff)
            return _fetch_one_live(ticker, years, attempt + 1)
        return None   # signal failure silently; caller decides what to do


def _try_live_download(tickers: list, years: int) -> dict:
    """
    Try to download all tickers from Yahoo Finance.
    Returns a dict mapping ticker -> DataFrame for successful ones.
    """
    print("[loader] Attempting live download from Yahoo Finance ...")
    results = {}
    for i, ticker in enumerate(tickers):
        df = _fetch_one_live(ticker, years)
        if df is not None:
            results[ticker] = df
            print(f"  [{ticker}] live OK — {len(df)} rows")
        else:
            print(f"  [{ticker}] live FAILED (rate-limited or unavailable)")
        if i < len(tickers) - 1:
            time.sleep(DELAY_BETWEEN)
    return results


# ---------------------------------------------------------------------------
# Synthetic data fallback (Geometric Brownian Motion)
# ---------------------------------------------------------------------------
def _generate_gbm(ticker: str, years: int) -> pd.DataFrame:
    """
    Simulate stock prices using Geometric Brownian Motion.

    GBM formula (Ito's lemma discretised to daily steps):
        S(t+dt) = S(t) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
    where:
        mu    = expected annual return (drift)
        sigma = annual volatility (diffusion coefficient)
        dt    = 1/252  (one trading day as fraction of a year)
        Z     ~ N(0,1)  (standard normal random shock)

    The mu - sigma^2/2 correction (Ito correction) ensures that the
    EXPECTED price follows e^(mu*t), not e^((mu+sigma^2/2)*t) — without
    this, log-normal GBM would systematically overshoot in expectation.
    """
    p = GBM_PARAMS.get(ticker, {"mu": 0.12, "sigma": 0.25, "s0": 100.0})
    mu, sigma, s0 = p["mu"], p["sigma"], p["s0"]

    end_date   = pd.Timestamp.today().normalize()
    trade_days = pd.bdate_range(end=end_date, periods=252 * years)  # business days only
    n  = len(trade_days)
    dt = 1.0 / 252

    np.random.seed(abs(hash(ticker)) % (2**31))  # reproducible but ticker-specific
    shocks = np.random.standard_normal(n)
    log_returns = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * shocks
    prices = s0 * np.exp(np.cumsum(log_returns))  # cumsum of log-returns = log of product

    return pd.DataFrame({
        "ticker":    ticker,
        "date":      trade_days.date,
        "adj_close": prices,
    })


# ---------------------------------------------------------------------------
# Main fetch function — live with automatic GBM fallback
# ---------------------------------------------------------------------------
def fetch_prices(tickers: list = TICKERS, years: int = PERIOD_YEARS) -> pd.DataFrame:
    """
    Download adjusted-close prices for every ticker.
    Falls back to GBM synthetic data for any ticker that Yahoo blocks.

    Returns a tidy long-format DataFrame:
        ticker | date | adj_close | daily_return
    """
    live_data  = _try_live_download(tickers, years)
    n_live     = len(live_data)
    n_missing  = len(tickers) - n_live

    if n_missing > 0:
        print(f"\n[loader] Yahoo Finance blocked {n_missing} ticker(s). "
              f"Generating GBM synthetic data for them.")
        print("[loader] NOTE: Synthetic prices use calibrated GBM parameters "
              "(mu, sigma per stock). Risk metrics are real — only source data differs.\n")

    all_frames = []
    for ticker in tickers:
        if ticker in live_data:
            all_frames.append(live_data[ticker])
        else:
            # Fallback: simulate via GBM
            df = _generate_gbm(ticker, years)
            df["_source"] = "synthetic"
            all_frames.append(df)
            print(f"  [{ticker}] synthetic GBM ({len(df)} rows, "
                  f"mu={GBM_PARAMS.get(ticker,{}).get('mu',0.12):.0%}, "
                  f"sigma={GBM_PARAMS.get(ticker,{}).get('sigma',0.25):.0%})")

    long_df = pd.concat(all_frames, ignore_index=True)
    long_df.dropna(subset=["adj_close"], inplace=True)

    # ---- Daily returns -------------------------------------------------------
    # daily_return = (P_t / P_{t-1}) - 1  (simple return, not log-return)
    # We use simple returns for VaR because they are additive across assets in
    # a portfolio (portfolio_return = sum of weighted simple returns).
    # Log-returns are additive over time but not across assets simultaneously.
    long_df.sort_values(["ticker", "date"], inplace=True)
    long_df["daily_return"] = long_df.groupby("ticker")["adj_close"].pct_change()

    long_df = long_df[["ticker", "date", "adj_close", "daily_return"]]
    long_df.reset_index(drop=True, inplace=True)

    print(f"\n[loader] Total rows: {len(long_df):,} "
          f"({n_live} live tickers, {n_missing} synthetic)")
    return long_df


def run_data_loader() -> None:
    """Entry point: fetch prices and write to database."""
    engine = get_engine()
    create_tables(engine)

    prices_df = fetch_prices()
    save_df(prices_df, "prices", engine)
    print("[loader] Done.")


if __name__ == "__main__":
    run_data_loader()
