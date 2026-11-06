# Notification Service

A job and product data scraping service built with FastAPI, SQLModel, and
Alembic. Scraping runs as a separate CLI/cron process so the API stays
responsive and scrape failures cannot block requests.

Sources are stored in the database. Site-specific extraction logic lives in
Python adapters, so adding a new source means adding an adapter module and a
`Source` row, not changing the orchestration code.

## Features

- Pluggable job source adapters with a shared registry.
- Source configuration stored in the `sources` table.
- Idempotent job upserts on `(source, external_id)`.
- JSON scraping via `httpx` with timeouts and retries.
- CLI for managing sources and running scrapes.
- Product scraper scaffold for future HTML scraping.
- SQLite by default, PostgreSQL supported through `DATABASE_URL`.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- SQLite (default) or PostgreSQL

## Setup

Install dependencies:

```bash
uv sync
```

Create the local environment file:

```bash
cp .env.example .env
```

Apply database migrations:

```bash
uv run alembic upgrade head
```

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy database URL |
| `ENVIRONMENT` | `development` | Environment name |
| `SQL_ECHO` | `false` | Log SQL statements when enabled |

Use `uv run` for all Python commands, or activate the project virtualenv first:

```bash
source .venv/bin/activate
```

Running bare `python` may use a different interpreter (for example, the Nix
shell Python) that does not have the project dependencies installed.

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

Health endpoints:

- `GET /health`
- `GET /health/db`

## Deploy to Koyeb

The repository includes a `Dockerfile` for a single Koyeb web service. The image
does not contain a database or an `.env` file; configuration is provided by
Koyeb.

Koyeb service settings:

- Builder: Dockerfile
- Service type: Web
- Exposed port: `8000`
- Protocol: HTTP
- Route: `/`
- Health check: HTTP `GET /health`
- Health check grace period: `30s`
- Environment variables:
  - `DATABASE_URL` as a Secret, using the external Postgres connection string
  - `ENVIRONMENT=production`
  - `SQL_ECHO=false`

The container runs `alembic upgrade head` before starting Uvicorn, so migrations
are applied automatically on each deployment. Keep the service at one instance;
concurrent instances could race while applying migrations.

Koyeb provides the `PORT` environment variable. The image uses it when present
and otherwise falls back to `8000`.

The same image can run as a Koyeb Worker service for scraping:

```bash
python -m app.cli scrape --kind jobs
```

Koyeb does not provide a built-in cron scheduler, so scheduling the scraper
requires an external scheduler or an in-process scheduler.

## Manage sources

Create or update a source by slug:

```bash
uv run python -m app.cli sources add \
  --slug duunitori \
  --kind jobs \
  --adapter duunitori_json \
  --url '<duunitori-api-url>'
```

The source URL is stored in the database and is not committed to the
repository. Use `--disabled` to save the source without enabling it.

List sources:

```bash
uv run python -m app.cli sources list
uv run python -m app.cli sources list --kind jobs
```

`sources add` is create-or-update: running it again with the same slug updates
the existing row.

## Run scraping

Run one source:

```bash
uv run python -m app.cli scrape --source duunitori
```

Run all enabled sources of a kind:

```bash
uv run python -m app.cli scrape --kind jobs
```

Scrape behavior:

- Jobs are upserted on `(source, external_id)`.
- Existing jobs have `title`, `raw_data`, and `scraped_at` updated.
- New jobs are inserted.
- Duplicate `external_id` values in one payload are skipped.
- One failing source is logged and does not stop other sources.
- The command exits with code `1` if any source fails.

### Cron example

```cron
*/30 * * * * cd /path/to/notification-service && /usr/bin/env uv run python -m app.cli scrape --kind jobs >> /tmp/notification-scrape.log 2>&1
```

Scraping is intentionally separate from the FastAPI process. A future scheduler
or worker can reuse `run_source` from `app/services/scrape.py`.

## Tests and linting

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Architecture

```text
app/
  api/          FastAPI routes
  core/         application settings
  db/           engine, session, and metadata
  models/       Job and Source models
  scrapers/     adapter protocols, HTTP helper, registry, source adapters
  services/     job upserts and scrape orchestration
  cli.py        source management and scrape CLI
migrations/     Alembic migrations
tests/          unit and service tests
```

Scrape flow:

```text
Source row
  -> adapter registry
  -> fetch and parse into ScrapedJob objects
  -> JobRepository.upsert_many
  -> jobs table
```

### Database models

`jobs`:

- `external_id`
- `source`
- `title`
- `scraped_at`
- `raw_data`

`jobs` has a unique constraint on `(source, external_id)`.

`sources`:

- `slug`
- `kind`
- `adapter`
- `url`
- `enabled`
- `config`
- `created_at`
- `updated_at`

## Adding a new job source

1. Create a module under `app/scrapers/jobs/<source>.py`.
2. Implement an adapter with a unique `key` and:

   ```python
   async def fetch(self, source: Source) -> list[ScrapedJob]: ...
   ```

3. Register it with `@register_job_adapter`.
4. Import the adapter in `app/scrapers/jobs/__init__.py` so it is registered.
5. Add the source row with `sources add`.
6. Add fixture-based tests for the mapping logic.

The current `duunitori_json` adapter expects a JSON object with a `results`
list. It maps:

- `id` -> `external_id`
- `heading` (falling back to `short_heading`) -> `title`
- the full result object -> `raw_data`

The shared HTTP helper currently provides `fetch_json`. Future HTML sources
should add a text/response fetch helper and use BeautifulSoup inside the source
adapter.

## Product scraping scaffold

Product scraping is not implemented yet. The scaffold includes:

- `ProductSourceAdapter` in `app/scrapers/base.py`.
- `app/scrapers/products/` as the future home for product adapters.

There is currently no product model, CLI command, or product adapter.

## Limitations

- Duunitori pagination is not implemented; only the first page is processed.
- The API does not trigger scrapes directly.
- Sources of kind `jobs` are the only supported kind so far.
