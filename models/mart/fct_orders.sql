-- Order fact table.
SELECT
    o.order_id,
    md5(CAST(o.contact_id AS VARCHAR))         AS customer_key,
    o.order_date,
    o.amount_usd,
    o.order_ref,
    o.order_notes IS NOT NULL                  AS has_notes,
    o.promo_code
FROM staging.stg_orders AS o
