-- queries/var_vs_realised_breach.sql
--
-- Question: On which dates did the ACTUAL daily return breach (exceed) the
--           predicted 95% Historical VaR for each ticker?
--
-- This is a VaR back-test query.  A correctly calibrated 95% VaR should be
-- breached roughly 5% of trading days (≈12-13 days/year).
-- More breaches → model is under-estimating risk (common with parametric VaR
-- during volatile regimes due to the normality assumption).
-- Fewer breaches → model is overly conservative (over-estimates risk).
--
-- We join the `prices` table (for actual returns) with `risk_metrics` (for VaR)
-- on (ticker, date).
--
-- Usage:   sqlite3 risk_data.db < queries/var_vs_realised_breach.sql

SELECT
    p.ticker,
    p.date,
    ROUND(p.daily_return, 4)                    AS actual_return,
    ROUND(m.hist_var_95, 4)                     AS hist_var_95,

    -- Breach flag: 1 if actual loss is worse than VaR threshold
    CASE WHEN p.daily_return < m.hist_var_95
         THEN 'BREACH' ELSE 'OK'
    END                                          AS breach_flag

FROM prices  AS p
JOIN risk_metrics AS m
    ON p.ticker = m.ticker
    AND p.date  = m.date

WHERE
    p.daily_return IS NOT NULL

ORDER BY
    p.ticker ASC,
    p.date   ASC;
