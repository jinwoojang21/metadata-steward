-- One row per web session.
SELECT
    e.sess_id                                  AS session_id,
    max(e.contact_id)                          AS contact_id,
    min(e.evt_ts)                              AS session_start,
    max(e.evt_ts)                              AS session_end,
    count(*)                                   AS page_views,
    any_value(e.ip)                            AS ip_address,
    any_value(e.device_id)                     AS device_id
FROM raw.web_events AS e
GROUP BY e.sess_id
