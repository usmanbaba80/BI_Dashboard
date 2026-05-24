-- Silver layer: clean/type raw rows from your custom HTTP API connection.
-- After the first Airbyte sync, rename the source table in models/sources.yml to match:
--   psql ... -c "\dt raw.*"
{{ config(
    materialized='table',
    tags=['silver'],
) }}

select
    test,
    meta
from {{ source('raw', 'test') }}
