-- Mart: the manager's report. One row per business day, per city.
--
-- An incremental model. The first run builds every day. Each later run rebuilds only
-- the newest days: it deletes those days and inserts them again. Older days are not touched.

{{
    config(
        materialized = 'incremental',
        incremental_strategy = 'delete+insert',
        unique_key = 'order_date'
    )
}}

select
    order_date,
    city,
    city_name,
    count(*) filter (where status <> 'cancelled')::int                        as orders,
    count(*) filter (where status = 'cancelled')::int                         as cancelled_orders,
    coalesce(sum(net_cents) filter (where status <> 'cancelled'), 0)::bigint  as revenue_cents,
    coalesce(sum(refunded_cents) filter (where status <> 'cancelled'), 0)::bigint as refunded_cents
from {{ ref('orders') }}
where not is_deleted                              -- a deleted order is not counted

{% if is_incremental() %}
  -- Only the days from N days before the newest day already in the report.
  -- A refund, a cancel or a late update moves a recent day, and a recent day is rebuilt.
  and order_date >= (select max(order_date) from {{ this }}) - {{ var('reprocess_days') }}
{% endif %}

group by order_date, city, city_name
