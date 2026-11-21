-- Staging: the orders that were deleted from the order system, one row per order.

select
    order_id,
    min(deleted_at) as deleted_at
from {{ source('raw', 'order_deletes') }}
group by order_id
