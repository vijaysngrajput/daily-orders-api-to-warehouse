-- Core: one row per item line of an order.

select
    i.order_id,
    i.line_no,
    i.product_id,
    i.product_name,
    i.quantity,
    i.unit_price_cents,
    i.item_status,
    case when i.item_status = 'ok' then i.quantity * i.unit_price_cents else 0 end as line_total_cents
from {{ ref('stg_order_items') }} as i
