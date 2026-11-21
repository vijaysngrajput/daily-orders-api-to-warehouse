{# Use the schema name exactly as written: staging, core, marts. dbt would otherwise write public_staging. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name is not none else target.schema }}
{%- endmacro %}
