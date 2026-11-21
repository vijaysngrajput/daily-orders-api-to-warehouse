-- Core: the clean orders table. One row per order, with its latest state.
-- It is rebuilt from the raw data on every run. That is possible because raw keeps everything.

select
    o.order_id,
    o.customer_id,
    o.city,
    c.city_name,                                  -- enrichment: a column added from another table
    o.status,
    o.placed_at,
    o.order_date,
    o.updated_at,
    o.delivered_at,
    o.cancelled_at,
    o.total_cents,
    o.refunded_cents,
    o.total_cents - o.refunded_cents              as net_cents,
    (d.order_id is not null)                      as is_deleted,     -- soft delete: the row stays, marked
    d.deleted_at,
    o.landing_id,
    o.run_id,
    o.loaded_at
from {{ ref('stg_orders') }} as o
left join {{ ref('cities') }} as c                -- left join: an unknown city must not lose the order
    on c.city = o.city
left join {{ ref('stg_order_deletes') }} as d
    on d.order_id = o.order_id
