FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir dbt-postgres==1.8.2

WORKDIR /dbt
COPY dbt_project.yml profiles.yml ./
COPY models ./models

EXPOSE 3001

# Lineage / project browser (guide: dbt UI on port 3001)
CMD ["sh", "-c", "dbt parse && dbt docs generate && exec dbt docs serve --host 0.0.0.0 --port 3001"]
