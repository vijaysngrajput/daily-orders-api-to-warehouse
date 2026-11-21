-- No order may have a negative total, a negative refund, or a refund larger than its total.

select order_id, total_cents, refunded_cents
from {{ ref('orders') }}
where total_cents < 0
   or refunded_cents < 0
   or refunded_cents > total_cents
