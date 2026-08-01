-- queries/avg_var_by_month.sql
--
-- Question: How does the average VaR of the entire portfolio evolve month by month?
--
-- Since historical VaR is constant per ticker in our implementation (computed
-- over the full 2-year window), this query instead averages ROLLING VOLATILITY
-- by month as a time-varying risk proxy across the whole portfolio.
-- This answers: "Were there months where the whole portfolio was significantly
-- more risky than usual?" — useful for correlating with macro events.
--
-- For a production system with a rolling VaR window, you'd replace
-- rolling_vol_30d with rolling hist_var_95 and this query becomes even richer.
--
-- Usage:   sqlite3 risk_data.db < queries/avg_var_by_month.sql

SELECT
    -- strftime extracts YYYY-MM from the date column (SQLite date function)
    strftime('%Y-%m', date)                         AS month,

    -- Equal-weight average across all tickers — treats each ticker as 1 unit
    ROUND(AVG(hist_var_95),      4)                 AS avg_hist_var_95,
    ROUND(AVG(hist_var_99),      4)                 AS avg_hist_var_99,
    ROUND(AVG(rolling_vol_30d),  4)                 AS avg_rolling_vol,

    -- Count of distinct tickers in the portfolio that month (sanity check)
    COUNT(DISTINCT ticker)                           AS num_tickers
FROM risk_metrics
WHERE rolling_vol_30d IS NOT NULL
GROUP BY strftime('%Y-%m', date)
ORDER BY month ASC;
