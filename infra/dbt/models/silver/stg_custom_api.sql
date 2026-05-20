-- Silver layer: clean/type raw rows from your custom HTTP API connection.
-- After the first Airbyte sync, rename the source table in models/sources.yml to match:
--   psql ... -c "\dt raw.*"
{{ config(
    materialized='table',
    schema='silver',
    tags=['silver'],
) }}

select
    *
from {{ source('raw', 'test') }}
