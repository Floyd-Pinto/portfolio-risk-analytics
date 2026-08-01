"""
data_loader.py - Fetch historical prices from Yahoo Finance and persist them.

Responsibilities:
  - Define the list of tickers to track.
  - Pull 2 years of daily adjusted-close prices via yfinance.
  - Compute daily returns (for use in risk calculations).
  - Store the resulting DataFrame into the `prices` table via db.save_df().

Why yfinance?
  The de facto standard Python library for Yahoo Finance data.
  Uses Yahoo's chart API with automatic cookie/auth handling (v0.2.55+).
  Supports both Indian NSE tickers (.NS suffix) and US tickers out of the box.

Why adjusted close?
  Raw prices are distorted by stock splits and dividends. Adjusted close
  applies these retroactively so returns are comparable across time.

Why 2 years (~504 trading days)?
  Enough observations for 99% historical VaR (needs ~100 tail samples)
  while keeping the dataset small for a local demo.
"""

import time

import yfinance as yf
import pandas as pd
import numpy as np

from db import get_engine, create_tables, save_df


# ---------------------------------------------------------------------------
# Ticker universe - mix of Indian NSE large-caps and US equities
# ---------------------------------------------------------------------------
TICKERS = [
    "RELIANCE.NS",   # Reliance Industries - energy/petrochemicals
    "TCS.NS",        # Tata Consultancy Services - IT services
    "HDFCBANK.NS",   # HDFC Bank - India's largest private-sector bank
    "INFY.NS",       # Infosys - IT services
    "WIPRO.NS",      # Wipro - IT services
    "ICICIBANK.NS",  # ICICI Bank - private banking
    "AAPL",          # Apple - US large-cap tech
    "MSFT",          # Microsoft - US large-cap tech
    "GOOGL",         # Alphabet - US tech/advertising
]

PERIOD_YEARS  = 2     # how far back to fetch
DELAY_BETWEEN = 2     # seconds between requests (avoids rate limiting)
MAX_RETRIES   = 3     # retry attempts per ticker

# GBM fallback parameters - used only if Yahoo is completely unavailable
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


def _fetch_ticker(ticker: str, years: int, attempt: int = 1) -> "pd.DataFrame | None":
    """
    Fetch history for a single ticker via yfinance.
    Retries up to MAX_RETRIES times with exponential backoff.
    Returns DataFrame [ticker, date, adj_close] or None on failure.
    """
    try:
        # yf.Ticker.history() uses Yahoo's chart API.
        # auto_adjust=True (default) returns split/dividend-adjusted Close.
        raw = yf.Ticker(ticker).history(
            period=f"{years}y",
            interval="1d",
            auto_adjust=True,
        )

        if raw.empty:
            return None

        # Flatten to [ticker, date, adj_close]
        close = raw[["Close"]].copy()
        close.index = pd.to_datetime(close.index).date   # strip timezone -> plain date
        close.columns = ["adj_close"]
        close["ticker"] = ticker
        close = close.reset_index()
        # Index name varies across yfinance versions - rename whatever it is
        close.rename(columns={close.columns[0]: "date"}, inplace=True)
        return close[["ticker", "date", "adj_close"]]

    except KeyboardInterrupt:
        raise   # let Ctrl-C exit cleanly
    except Exception as exc:
        wait = 5 * attempt   # 5s, 10s, 15s
        if attempt < MAX_RETRIES:
            print(f"  [{ticker}] attempt {attempt} failed ({type(exc).__name__}) "
                  f"- retrying in {wait}s ...")
            time.sleep(wait)
            return _fetch_ticker(ticker, years, attempt + 1)
        print(f"  [{ticker}] FAILED after {MAX_RETRIES} attempts: {exc}")
        return None


def _gbm_fallback(ticker: str, years: int) -> pd.DataFrame:
    """
    Generate synthetic prices via Geometric Brownian Motion.
    Only used when Yahoo Finance is completely unavailable.

    GBM: S(t+dt) = S(t) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
    where Z ~ N(0,1), dt = 1/252 (one trading day).

    The (mu - sigma^2/2) Ito correction ensures E[S(t)] = S(0)*exp(mu*t).
    """
    p = GBM_PARAMS.get(ticker, {"mu": 0.12, "sigma": 0.25, "s0": 100.0})
    mu, sigma, s0 = p["mu"], p["sigma"], p["s0"]
    end_date   = pd.Timestamp.today().normalize()
    trade_days = pd.bdate_range(end=end_date, periods=252 * years)
    n, dt = len(trade_days), 1.0 / 252
    np.random.seed(abs(hash(ticker)) % (2 ** 31))
    shocks = np.random.standard_normal(n)
    prices = s0 * np.exp(np.cumsum((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * shocks))
    return pd.DataFrame({"ticker": ticker, "date": trade_days.date, "adj_close": prices})


def fetch_prices(tickers: list = TICKERS, years: int = PERIOD_YEARS) -> pd.DataFrame:
    """
    Download adjusted-close prices for every ticker via yfinance.
    Falls back to GBM synthetic data only if Yahoo is completely unavailable.

    Returns tidy long-format DataFrame: ticker | date | adj_close | daily_return
    """
    print(f"[loader] Downloading {years}y of data for {len(tickers)} tickers via yfinance ...")

    all_frames = []
    failed     = []

    for i, ticker in enumerate(tickers):
        df = _fetch_ticker(ticker, years)
        if df is not None:
            all_frames.append(df)
            print(f"  [{ticker}] OK - {len(df)} rows")
        else:
            failed.append(ticker)

        # Pause between tickers to stay within Yahoo's rate limits
        if i < len(tickers) - 1:
            time.sleep(DELAY_BETWEEN)

    if failed:
        print(f"\n[loader] Yahoo unavailable for: {failed}")
        print("[loader] Using GBM synthetic fallback for those tickers.")
        for ticker in failed:
            df = _gbm_fallback(ticker, years)
            all_frames.append(df)
            print(f"  [{ticker}] GBM synthetic ({len(df)} rows)")

    if not all_frames:
        raise RuntimeError("No data fetched at all. Check your internet connection.")

    long_df = pd.concat(all_frames, ignore_index=True)
    long_df.dropna(subset=["adj_close"], inplace=True)

    # daily_return = (P_t / P_{t-1}) - 1  (simple return, not log-return)
    # Simple returns are additive across a portfolio (portfolio_return =
    # sum of weighted asset returns). Log-returns are additive over time
    # but NOT across assets simultaneously.
    long_df.sort_values(["ticker", "date"], inplace=True)
    long_df["daily_return"] = long_df.groupby("ticker")["adj_close"].pct_change()
    long_df = long_df[["ticker", "date", "adj_close", "daily_return"]]
    long_df.reset_index(drop=True, inplace=True)

    live_count = len(tickers) - len(failed)
    print(f"\n[loader] Done: {live_count} live tickers, {len(failed)} synthetic. "
          f"Total rows: {len(long_df):,}\n")
    return long_df


def run_data_loader() -> None:
    engine = get_engine()
    create_tables(engine)
    prices_df = fetch_prices()
    save_df(prices_df, "prices", engine)
    print("[loader] Done.")


if __name__ == "__main__":
    run_data_loader()
