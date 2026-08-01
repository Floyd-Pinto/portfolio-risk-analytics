-- queries/high_drawdown_tickers.sql
--
-- Question: Which tickers experienced a drawdown exceeding 15% in any 30-day
--           rolling window during the measurement period?
--
-- Drawdown > 15% is typically a meaningful threshold for risk alerts in a
-- risk management framework — it signals a ticker may be in a sustained
-- downtrend rather than a temporary blip.
--
-- We use the rolling_max_drawdown stored in risk_metrics (computed over 30
-- days per date), not the full-period MDD, so we can pinpoint WHEN the
-- severe drawdown occurred.
--
-- Usage:   sqlite3 risk_data.db < queries/high_drawdown_tickers.sql

SELECT
    ticker,
    date,
    ROUND(max_drawdown, 4)          AS rolling_mdd,
    ROUND(max_drawdown * 100, 2)    AS rolling_mdd_pct
FROM risk_metrics
WHERE
    max_drawdown < -0.15            -- drawdown worse than -15%
ORDER BY
    max_drawdown ASC,               -- most severe first
    date ASC;
