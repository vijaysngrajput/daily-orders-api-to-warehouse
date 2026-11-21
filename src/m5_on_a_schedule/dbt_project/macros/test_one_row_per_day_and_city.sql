{# A table test written in this project: no business day and city may appear twice in the report. #}
{% test one_row_per_day_and_city(model) %}
    select order_date, city, count(*) as times
    from {{ model }}
    group by order_date, city
    having count(*) > 1
{% endtest %}
