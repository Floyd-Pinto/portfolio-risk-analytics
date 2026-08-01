"""
db.py — Database setup using SQLAlchemy.

Responsible for:
  - Creating the SQLite database (risk_data.db)
  - Defining two tables: `prices` and `risk_metrics`
  - Providing helper functions to read/write DataFrames via pandas + SQLAlchemy

Why SQLite?  Zero-config, single-file, queryable with standard SQL tools.
             Perfect for a local analytics project with no concurrency needs.
Why SQLAlchemy? It gives us a clean, Pythonic interface and is trivially
                swappable to PostgreSQL/MySQL later by changing the connection
                string — no other code needs to change.
"""

import sqlalchemy as sa
import pandas as pd

# ---------------------------------------------------------------------------
# Engine — single connection string drives the whole project
# ---------------------------------------------------------------------------
DATABASE_URL = "sqlite:///risk_data.db"   # file lands in the current working directory


def get_engine() -> sa.Engine:
    """Return (and lazily create) the SQLAlchemy engine."""
    engine = sa.create_engine(DATABASE_URL, echo=False)
    return engine


# ---------------------------------------------------------------------------
# Schema — defined with SQLAlchemy Core (no ORM) to stay interview-friendly
# ---------------------------------------------------------------------------
def create_tables(engine: sa.Engine) -> None:
    """
    Create both tables if they don't already exist.

    Table 1 — prices
        Stores the raw adjusted close price pulled from yfinance per ticker per
        date.  This is our source-of-truth; all metrics are derived from it.

    Table 2 — risk_metrics
        One row per ticker × date containing every computed risk metric.
        Linked back to `prices` conceptually by (ticker, date) — we don't use a
        foreign-key constraint here to keep SQLite DDL simple, but the join
        always works because both tables share the same (ticker, date) keys.
    """
    metadata = sa.MetaData()

    # -- Table 1: raw prices --------------------------------------------------
    sa.Table(
        "prices",
        metadata,
        sa.Column("ticker",       sa.String,  nullable=False),   # e.g. "RELIANCE.NS"
        sa.Column("date",         sa.Date,    nullable=False),   # trading day
        sa.Column("adj_close",    sa.Float,   nullable=False),   # split/dividend adjusted close
        sa.Column("daily_return", sa.Float),                     # (P_t / P_{t-1}) - 1
        sa.UniqueConstraint("ticker", "date", name="uq_ticker_date"),
    )

    # -- Table 2: derived risk metrics ----------------------------------------
    sa.Table(
        "risk_metrics",
        metadata,
        sa.Column("ticker",              sa.String,  nullable=False),
        sa.Column("date",                sa.Date,    nullable=False),
        sa.Column("hist_var_95",         sa.Float),  # 95% historical VaR (negative number)
        sa.Column("hist_var_99",         sa.Float),  # 99% historical VaR
        sa.Column("param_var_95",        sa.Float),  # 95% parametric VaR
        sa.Column("param_var_99",        sa.Float),  # 99% parametric VaR
        sa.Column("rolling_vol_30d",     sa.Float),  # 30-day rolling annualised volatility
        sa.Column("max_drawdown",        sa.Float),  # maximum drawdown up to this date
        sa.UniqueConstraint("ticker", "date", name="uq_metric_ticker_date"),
    )

    metadata.create_all(engine)
    print("[db] Tables created (or already exist).")


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def save_df(df: pd.DataFrame, table_name: str, engine: sa.Engine) -> None:
    """
    Upsert a DataFrame into the given table.

    We use if_exists='replace' for simplicity in a single-run pipeline.
    In a production system you'd use INSERT OR IGNORE or ON CONFLICT DO UPDATE.
    """
    df.to_sql(table_name, con=engine, if_exists="replace", index=False)
    print(f"[db] Saved {len(df):,} rows -> {table_name}")


def load_df(sql: str, engine: sa.Engine) -> pd.DataFrame:
    """Run a SQL query and return the result as a DataFrame."""
    return pd.read_sql(sql, con=engine)
