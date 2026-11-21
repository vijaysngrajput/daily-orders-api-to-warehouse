-- Staging: one row per item line, taken from the newest version of each order.

select
    o.order_id,
    (item->>'line_no')::int               as line_no,
    item->>'product_id'                   as product_id,
    item->>'product_name'                 as product_name,
    (item->>'quantity')::int              as quantity,
    (item->>'unit_price_cents')::bigint   as unit_price_cents,
    lower(item->>'item_status')           as item_status
from {{ ref('stg_orders') }} as o
cross join lateral jsonb_array_elements(o.items) as item
