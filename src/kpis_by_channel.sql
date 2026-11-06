-- Same funnel, cut by sourcing channel (all mature cohorts combined).
-- Parameters: :window_days, :as_of
WITH seller_funnel AS (
    SELECT
        s.seller_id, s.channel, s.sourced_date,
        o.onboarded_date,
        CASE WHEN a.activated_date IS NOT NULL
              AND julianday(a.activated_date) - julianday(s.sourced_date) <= :window_days
             THEN 1 ELSE 0 END AS activated_in_window
    FROM sourcing s
    LEFT JOIN onboarding o ON o.seller_id = s.seller_id
    LEFT JOIN activation a ON a.seller_id = s.seller_id
    WHERE julianday(:as_of) - julianday(s.sourced_date) >= :window_days   -- mature only
)
SELECT
    channel,
    COUNT(DISTINCT seller_id)                                              AS sourced,
    COUNT(DISTINCT CASE WHEN onboarded_date IS NOT NULL THEN seller_id END) AS onboarded,
    SUM(activated_in_window)                                               AS activated,
    ROUND(1.0 * SUM(activated_in_window) / COUNT(DISTINCT seller_id), 4)   AS end_to_end_conversion
FROM seller_funnel
GROUP BY channel
ORDER BY end_to_end_conversion DESC;
