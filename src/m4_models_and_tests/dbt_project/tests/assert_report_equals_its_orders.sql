-- The report's totals must equal the sum of its orders.
-- A test passes when its query returns no rows. Each row returned is a day that is wrong.

with from_report as (

    select order_date, sum(orders) as orders, sum(revenue_cents) as revenue_cents
    from {{ ref('daily_orders') }}
    group by order_date

),

from_orders as (

    select
        order_date,
        count(*) filter (where status <> 'cancelled')                        as orders,
        coalesce(sum(net_cents) filter (where status <> 'cancelled'), 0)     as revenue_cents
    from {{ ref('orders') }}
    where not is_deleted
    group by order_date

)

select
    coalesce(r.order_date, o.order_date) as order_date,
    r.orders        as report_orders,
    o.orders        as true_orders,
    r.revenue_cents as report_cents,
    o.revenue_cents as true_cents
from from_report as r
full join from_orders as o using (order_date)
where r.orders is distinct from o.orders
   or r.revenue_cents is distinct from o.revenue_cents
