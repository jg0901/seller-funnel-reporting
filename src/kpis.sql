-- Weekly seller funnel KPIs, cohort-based.
--
-- Definitions (state these in the README / interview):
--   * Cohort        = sellers grouped by the ISO week of their sourced_date.
--   * Onboarded     = seller_id present in onboarding (distinct), regardless of KYC status.
--   * Activated     = seller_id present in activation (distinct) AND activated within
--                     :window_days of sourced_date. Later activations count as not-activated
--                     for that cohort, so the rate is comparable across weeks.
--   * Rates are cohort rates (numerator sellers belong to the same cohort as the denominator),
--     NOT period rates (events in week / events in week). Period rates inflate when
--     onboarding lags sourcing.
--   * Recent cohorts are "immature": a cohort younger than :window_days cannot have
--     reached its full activation rate yet. is_mature flags this.
--
-- Parameters: :window_days (int), :as_of (ISO date string)

WITH seller_funnel AS (
    SELECT
        s.seller_id,
        s.sourced_date,
        s.channel,
        s.region,
        o.onboarded_date,
        a.activated_date,
        CASE WHEN a.activated_date IS NOT NULL
              AND julianday(a.activated_date) - julianday(s.sourced_date) <= :window_days
             THEN 1 ELSE 0 END AS activated_in_window,
        julianday(a.activated_date) - julianday(s.sourced_date) AS days_to_activation
    FROM sourcing s
    LEFT JOIN onboarding o ON o.seller_id = s.seller_id
    LEFT JOIN activation a ON a.seller_id = s.seller_id
),
cohorts AS (
    SELECT
        strftime('%Y-W%W', sourced_date)                    AS cohort_week,
        MIN(sourced_date)                                   AS cohort_start,
        COUNT(DISTINCT seller_id)                           AS sourced,
        COUNT(DISTINCT CASE WHEN onboarded_date IS NOT NULL THEN seller_id END) AS onboarded,
        SUM(activated_in_window)                            AS activated,
        AVG(CASE WHEN activated_in_window = 1 THEN days_to_activation END) AS avg_days_to_activation,
        CASE WHEN julianday(:as_of) - julianday(MAX(sourced_date)) >= :window_days
             THEN 1 ELSE 0 END                              AS is_mature
    FROM seller_funnel
    GROUP BY cohort_week
)
SELECT
    cohort_week,
    cohort_start,
    sourced,
    onboarded,
    activated,
    ROUND(1.0 * onboarded / NULLIF(sourced, 0), 4)    AS onboarding_conversion,
    ROUND(1.0 * activated / NULLIF(onboarded, 0), 4)  AS activation_conversion,
    ROUND(1.0 * activated / NULLIF(sourced, 0), 4)    AS end_to_end_conversion,
    ROUND(avg_days_to_activation, 1)                  AS avg_days_to_activation,
    is_mature
FROM cohorts
ORDER BY cohort_week;
