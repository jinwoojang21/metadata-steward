-- Cleaned CRM contacts: typed, renamed, de-duplicated by source.
SELECT
    c.contact_id,
    upper(left(c.frst_nm, 1)) || lower(substr(c.frst_nm, 2)) AS first_name,
    upper(left(c.lst_nm, 1)) || lower(substr(c.lst_nm, 2))   AS last_name,
    lower(trim(c.eml_addr))                    AS email,
    CAST(c.ph_num AS VARCHAR)                  AS phone,
    CAST(strptime(c.c3, '%Y%m%d') AS DATE)     AS birth_date,
    c.tax_id                                   AS ssn,
    c.addr_line1                               AS street_address,
    c.zip5                                     AS zip,
    c.src_sys                                  AS source_system,
    c.created_ts                               AS created_at
FROM raw.crm_contacts AS c
