-- Auto-installed on first PGDATA init by docker-entrypoint-initdb.d.
-- Runs as POSTGRES_USER (superuser) against POSTGRES_DB.
-- Re-trigger with `podman compose down -v && podman compose up -d`.

CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE EXTENSION IF NOT EXISTS vector;
