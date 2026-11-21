-- Staging: the raw orders, cleaned, typed, and with ONE row per order.
-- Nothing is counted or added up here.

with landed as (

    select landing_id, run_id, loaded_at, payload
    from {{ source('raw', 'orders_landing') }}

),

typed as (   -- type casting: every value out of the JSON gets its real type

    select
        landing_id,
        run_id,
        loaded_at,
        (payload->>'order_id')::bigint                      as order_id,
        (payload->>'customer_id')::bigint                   as customer_id,
        upper(trim(payload->>'city'))                       as city,      -- cleaning: one spelling
        lower(trim(payload->>'status'))                     as status,    -- cleaning: one spelling
        (payload->>'placed_at')::timestamptz                as placed_at,
        (payload->>'updated_at')::timestamptz               as updated_at,
        (payload->>'delivered_at')::timestamptz             as delivered_at,
        (payload->>'cancelled_at')::timestamptz             as cancelled_at,
        (payload->>'total_cents')::bigint                   as total_cents,
        (payload->>'refunded_cents')::bigint                as refunded_cents,
        payload->'items'                                    as items
    from landed

),

ranked as (   -- deduplication: number the versions of each order, newest first

    select
        *,
        row_number() over (
            partition by order_id
            order by updated_at desc, landing_id desc
        ) as version_rank
    from typed

)

select
    order_id,
    customer_id,
    city,
    status,
    placed_at,
    -- the business day: the date of placed_at on Arden's clock
    (placed_at at time zone '{{ var("business_time_zone") }}')::date as order_date,
    updated_at,
    delivered_at,
    cancelled_at,
    total_cents,
    refunded_cents,
    items,
    landing_id,
    run_id,
    loaded_at
from ranked
where version_rank = 1        -- keep the newest version of each order
