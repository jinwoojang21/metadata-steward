-- Customer dimension for analytics. Direct identifiers are reduced or hashed.
SELECT
    md5(CAST(s.contact_id AS VARCHAR))         AS customer_key,
    s.first_name || ' ' || s.last_name         AS full_name,
    split_part(s.email, '@', 2)                AS email_domain,
    sha256(s.email)                            AS email_hash,
    year(s.birth_date)                         AS birth_year,
    CASE
        WHEN date_diff('year', s.birth_date, DATE '2026-01-01') < 30 THEN 'under_30'
        WHEN date_diff('year', s.birth_date, DATE '2026-01-01') < 50 THEN '30_49'
        ELSE '50_plus'
    END                                        AS age_band,
    left(s.zip, 3)                             AS zip3,
    right(s.ssn, 4)                            AS ssn_last4,
    s.source_system
FROM staging.stg_contacts AS s
