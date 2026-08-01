-- queries/top_riskiest_tickers.sql
--
-- Question: Which 3 tickers carry the highest daily loss risk at 95% confidence?
--
-- Method: Order by hist_var_95 ascending (most negative = worst loss).
--         We prefer historical VaR here because it doesn't assume normality,
--         making it a more honest representation of tail risk.
--
-- Usage:   sqlite3 risk_data.db < queries/top_riskiest_tickers.sql

SELECT
    ticker,
    ROUND(hist_var_95, 4)   AS hist_var_95,      -- e.g. -0.0312 = 3.12% daily loss
    ROUND(hist_var_99, 4)   AS hist_var_99,
    ROUND(param_var_95, 4)  AS param_var_95,
    ROUND(
        (hist_var_95 - param_var_95), 4
    )                       AS fat_tail_gap       -- wider gap → fatter tails than normal
FROM (
    -- Deduplicate: VaR is constant per ticker, take any one date row
    SELECT DISTINCT ticker, hist_var_95, hist_var_99, param_var_95
    FROM risk_metrics
)
ORDER BY hist_var_95 ASC   -- most negative (biggest loss) first
LIMIT 3;
