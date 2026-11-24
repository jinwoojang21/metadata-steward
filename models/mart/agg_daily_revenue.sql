-- Daily revenue rollup for finance dashboards.
SELECT
    f.order_date,
    count(*)                                   AS order_count,
    sum(f.amount_usd)                          AS revenue_usd,
    count(DISTINCT f.customer_key)             AS unique_customers
FROM mart.fct_orders AS f
GROUP BY f.order_date
