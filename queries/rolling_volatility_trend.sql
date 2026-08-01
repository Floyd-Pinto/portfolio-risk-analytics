-- queries/rolling_volatility_trend.sql
--
-- Question: What is the 30-day rolling volatility trend for a given ticker?
--
-- This is useful for identifying "volatility regimes" — periods where a stock
-- became significantly more or less volatile (e.g. during earnings, macro events).
-- Rolling vol is annualised (×√252) so it's directly comparable to standard
-- published volatility figures (e.g. implied vol from options).
--
-- Change the WHERE clause to analyse a different ticker.
-- Usage:   sqlite3 risk_data.db < queries/rolling_volatility_trend.sql

SELECT
    date,
    ticker,
    ROUND(rolling_vol_30d, 4)           AS rolling_vol_30d,
    ROUND(rolling_vol_30d * 100, 2)     AS rolling_vol_pct    -- human-readable percentage
FROM risk_metrics
WHERE
    ticker = 'TCS.NS'          -- ← change ticker here
    AND rolling_vol_30d IS NOT NULL
ORDER BY date ASC;
