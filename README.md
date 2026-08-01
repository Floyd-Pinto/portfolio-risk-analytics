# Portfolio Risk Analytics

A Python project that fetches 2 years of daily stock price data for a mixed basket of Indian and US equities, computes key risk metrics (VaR, drawdown, volatility), stores everything in a local SQLite database, and produces a ranked report + chart.

Built to be **interview-explainable line-by-line** — every formula has a comment explaining *why* it's used, not just *what* it does.

---

## Project Structure

```
portfolio_risk_analytics/
├── db.py               # SQLAlchemy engine, table schema, read/write helpers
├── data_loader.py      # yfinance pull → prices table
├── risk_metrics.py     # VaR, volatility, drawdown calculations → risk_metrics table
├── main.py             # Pipeline orchestrator, report, chart
├── requirements.txt    # Pinned dependencies
└── queries/
    ├── top_riskiest_tickers.sql      # Top 3 by 95% historical VaR
    ├── rolling_volatility_trend.sql  # Vol time-series for a single ticker
    ├── high_drawdown_tickers.sql     # Rows where rolling drawdown > 15%
    ├── avg_var_by_month.sql          # Monthly portfolio-wide VaR/vol averages
    └── var_vs_realised_breach.sql    # Back-test: flag days actual loss > VaR
```

---

## Ticker Universe

| Ticker | Market | Sector |
|--------|--------|--------|
| RELIANCE.NS | India (NSE) | Energy / Petrochemicals |
| TCS.NS | India (NSE) | IT Services |
| HDFCBANK.NS | India (NSE) | Private Banking |
| INFY.NS | India (NSE) | IT Services |
| WIPRO.NS | India (NSE) | IT Services |
| ICICIBANK.NS | India (NSE) | Private Banking |
| AAPL | US (NASDAQ) | Consumer Tech |
| MSFT | US (NASDAQ) | Cloud / Software |
| GOOGL | US (NASDAQ) | Advertising / Cloud |

---

## Setup

**Requirements:** Python 3.10+

```bash
# Clone the repo
git clone https://github.com/<your-username>/portfolio-risk-analytics.git
cd portfolio-risk-analytics

# Create and activate a virtual environment (recommended)
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the full pipeline
python main.py
```

---

## Outputs

Running `python main.py` produces three outputs in the project directory:

### 1. `risk_data.db` — SQLite Database
Two tables queryable with any SQL client (e.g. `sqlite3`, DBeaver, DB Browser for SQLite):

```sql
-- prices table: raw adjusted-close prices + daily returns
SELECT * FROM prices WHERE ticker = 'TCS.NS' LIMIT 5;

-- risk_metrics table: one row per ticker × date
SELECT * FROM risk_metrics WHERE ticker = 'AAPL' LIMIT 5;
```

### 2. `risk_summary.csv` — Ranked Risk Report

Actual output from a live run (2-year window, Aug 2024 – Jul 2026), ranked by 95% Historical VaR (riskiest first):

| Ticker | Hist VaR 95% | Hist VaR 99% | Param VaR 95% | Param VaR 99% | Avg Roll Vol | Full-Period MDD |
|--------|-------------|-------------|--------------|--------------|-------------|----------------|
| WIPRO.NS | -0.0290 | -0.0435 | -0.0277 | -0.0390 | 0.2567 | -0.4422 |
| INFY.NS | -0.0281 | -0.0452 | -0.0292 | -0.0409 | 0.2645 | -0.4817 |
| AAPL | -0.0278 | -0.0498 | -0.0291 | -0.0416 | 0.2607 | -0.3336 |
| GOOGL | -0.0266 | -0.0475 | -0.0312 | -0.0449 | 0.3027 | -0.2981 |
| TCS.NS | -0.0257 | -0.0387 | -0.0259 | -0.0362 | 0.2264 | -0.5339 |
| MSFT | -0.0256 | -0.0397 | -0.0292 | -0.0415 | 0.2503 | -0.3450 |
| RELIANCE.NS | -0.0207 | -0.0339 | -0.0216 | -0.0305 | 0.2035 | -0.2387 |
| HDFCBANK.NS | -0.0195 | -0.0330 | -0.0204 | -0.0289 | 0.1828 | -0.2778 |
| ICICIBANK.NS | -0.0183 | -0.0299 | -0.0188 | -0.0267 | 0.1813 | -0.1837 |

> VaR values are daily loss fractions (e.g. −0.029 = a 2.9% loss on a bad day). WIPRO.NS carries the highest 95% VaR in this portfolio; ICICIBANK.NS the lowest. Note the Historical vs Parametric gap flips direction across tickers (e.g. WIPRO's historical loss is worse than its parametric estimate, while INFY's is the reverse) — a reminder that with a ~500-day sample, fat-tail effects don't move uniformly across assets.

### 3. `var_comparison.png` — VaR Bar Chart

Horizontal bar chart comparing Historical vs Parametric VaR at 95% across all tickers.  
The **gap** between the two bars reveals how much fatter a stock's tails are than a normal distribution predicts.

---

## Running SQL Queries

```bash
# Requires sqlite3 CLI (ships with most OS; on Windows: winget install SQLite.SQLite)
sqlite3 risk_data.db < queries/top_riskiest_tickers.sql
sqlite3 risk_data.db < queries/avg_var_by_month.sql
sqlite3 risk_data.db < queries/high_drawdown_tickers.sql
sqlite3 risk_data.db < queries/var_vs_realised_breach.sql

# Change the ticker inside the file first:
sqlite3 risk_data.db < queries/rolling_volatility_trend.sql
```

---

## Risk Metric Methodology

### Value at Risk (VaR)
> "What is the maximum daily loss I can expect with X% confidence?"

| Method | Formula | Assumption | Best used when |
|--------|---------|------------|----------------|
| **Historical** | 5th percentile of actual returns | None (non-parametric) | Fat tails, skewed distributions |
| **Parametric** | `μ + z·σ` (z = −1.6449 at 95%) | Returns are normally distributed | Fast approximation, normally distributed assets |

The gap between these two is a measure of **excess kurtosis** — how much heavier the real tails are vs a normal distribution. Equity returns almost always have fatter tails (leptokurtosis), so Historical VaR is generally the more honest number.

### Rolling Volatility
Daily standard deviation of returns scaled to annual: `σ_daily × √252`

The √252 scaling assumes daily returns are i.i.d. (independently and identically distributed). The 30-day window lets you track *volatility regimes* — periods where a stock became markedly more or less volatile (e.g. around earnings or macro events).

### Maximum Drawdown
`MDD = min((price - running_peak) / running_peak)`

Unlike VaR, max drawdown has **no confidence level and no distributional assumption** — it's the actual worst peak-to-trough loss a long-only investor experienced. A stock can have a modest VaR but a catastrophic MDD if the losses were sustained over many days.

---

## Tech Stack

- **yfinance** — Yahoo Finance data pull
- **pandas / numpy** — data wrangling and numerical computation
- **SQLAlchemy** — database abstraction (swap to PostgreSQL by changing one connection string)
- **SQLite** — zero-config local storage
- **matplotlib** — visualisation
- **scipy** — normal distribution inverse CDF for parametric VaR

---

## Notes

- Data is fetched live from Yahoo Finance each run. Re-running overwrites the database (`if_exists='replace'`).
- Indian tickers use the `.NS` suffix (National Stock Exchange). `.BO` would target the BSE instead.
- The database is excluded from version control (see `.gitignore`) since it's regenerated on every run.
