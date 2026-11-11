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
- JSON and HTML scraping via `httpx` (with BeautifulSoup for HTML) with
  timeouts and retries.
- CLI for managing sources and running scrapes.
- Product scraper scaffold for future HTML scraping.
- Password-protected login with a reusable API-key dependency.
- Frontpage dashboard with job/source stats and an on-demand scrape action.
- Filterable jobs listing with title, source, link, posted, and scraped dates.
- PostgreSQL storage configured through `DATABASE_URL`.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL

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
| `DATABASE_URL` | required | PostgreSQL SQLAlchemy database URL |
| `TEST_DATABASE_URL` | required for tests | PostgreSQL database URL used by the test suite |
| `ENVIRONMENT` | `development` | Environment name |
| `SQL_ECHO` | `false` | Log SQL statements when enabled |
| `APP_PASSWORD` | empty | Login password; also used as the external API key |
| `SESSION_SECRET` | `dev-insecure-change-me` | Secret used to sign session cookies |
| `SESSION_COOKIE_NAME` | `notification_session` | Name of the session cookie |
| `SESSION_MAX_AGE` | `2592000` | Session lifetime in seconds (30 days) |

`APP_PASSWORD` is required when `ENVIRONMENT=production`, and `SESSION_SECRET`
must be changed from its development default.

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

## Web UI

After signing in, the navigation links to:

- `/` — frontpage with total jobs, source counts, last scrape time, an
  enabled/disabled source breakdown, and a **Scrape jobs** button.
- `/jobs` — paginated job listing with title, source, link, date posted, and
  date scraped. Filters cover title search, source, and posted-date range.

The **Scrape jobs** button runs all enabled `jobs` sources synchronously and
shows per-source fetched/inserted/updated/skipped/failed counts, plus any source
errors. The job link and posted date are read from each job's `raw_data`
(`link`, `date_posted` with a `date_created` fallback), so no extra database
columns are required.

Displayed timestamps are converted to the browser's local timezone. Without
JavaScript they fall back to `Europe/Helsinki` (EET/EEST).

## Authentication

Open `/` to see the login page. Sign in with the value of `APP_PASSWORD`; on
success the page shows a confirmation message and stores a signed session
cookie. `/logout` clears the session.

Future API routes can reuse the same password through the `require_api_key`
dependency:

```python
from fastapi import Depends

from app.core.security import require_api_key


@app.get("/api/example", dependencies=[Depends(require_api_key)])
def example() -> dict[str, str]:
    return {"status": "ok"}
```

Clients authenticate by sending the password in the `X-API-Key` header:

```bash
curl -H "X-API-Key: $APP_PASSWORD" https://example.com/api/example
```

The health endpoints remain public. In production, session cookies are marked
`Secure`, so the service must be served over HTTPS.

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
  - `APP_PASSWORD` as a Secret
  - `SESSION_SECRET` as a Secret (a long random value)

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

For example, to add Jobly:

```bash
uv run python -m app.cli sources add \
  --slug jobly \
  --kind jobs \
  --adapter jobly_html \
  --url 'https://www.jobly.fi/tyopaikat/it?search=&job_geo_location=Uusimaa%2C%20Suomi&administrative_area_level_1=Uusimaa&lat=60.21872&lon=25.271621&country=Suomi&page=0'
```

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

Tests require a PostgreSQL server. Set `TEST_DATABASE_URL` to a dedicated test
database; the suite recreates its schema and will refuse to run against the same
database as `DATABASE_URL`.

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

The `duunitori_json` adapter expects a JSON object with a `results` list. It
maps:

- `id` -> `external_id`
- `heading` (falling back to `short_heading`) -> `title`
- the full result object -> `raw_data`

The `jobly_html` adapter scrapes Jobly search pages with BeautifulSoup. It
fetches up to 5 pages from the configured URL, replacing the `page` query
parameter, and maps:

- the numeric suffix of the job link (for example `...-2787385`) ->
  `external_id`
- the job title -> `title`
- `link`, `date_posted` (ISO date), and `location` -> `raw_data`

The shared HTTP helpers provide `fetch_json` and `fetch_text`, both with
timeouts and retries. Use `fetch_text` with BeautifulSoup for HTML sources.
