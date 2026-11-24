-- Orders normalised to USD.
SELECT
    o.ord_id                                   AS order_id,
    o.contact_id,
    o.ord_dt                                   AS order_date,
    CASE o.cur_cd
        WHEN 'EUR' THEN o.amt * 1.08
        WHEN 'GBP' THEN o.amt * 1.27
        ELSE o.amt
    END                                        AS amount_usd,
    o.ref_code                                 AS order_ref,
    o.notes                                    AS order_notes,
    o.ip_addr                                  AS ip_address,
    o.promo_cd                                 AS promo_code
FROM raw.orders_ext AS o
