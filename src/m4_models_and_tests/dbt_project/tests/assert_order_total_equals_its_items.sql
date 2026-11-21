-- An order's total must equal the sum of its item lines that were not out of stock.

select o.order_id, o.total_cents, sum(i.line_total_cents) as items_cents
from {{ ref('orders') }} as o
join {{ ref('order_items') }} as i using (order_id)
group by o.order_id, o.total_cents
having o.total_cents <> sum(i.line_total_cents)
