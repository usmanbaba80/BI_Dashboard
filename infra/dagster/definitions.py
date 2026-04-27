"""
Extend this module with dagster-dbt and dagster-airbyte assets when your
dbt project and Airbyte instance are ready (see Solution_Stack_Guide.html).
"""

from dagster import Definitions, asset


@asset
def stack_healthcheck():
    """Placeholder asset; replace with dbt / Airbyte assets as you wire the stack."""
    return {"status": "ok"}


defs = Definitions(assets=[stack_healthcheck])
