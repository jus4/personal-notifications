# Notification Service

A job and product data scraping service built with FastAPI, SQLModel, and
Alembic. Scraping can run from the CLI/cron or from in-process schedulers, and
scrape failures are isolated per source.

Sources are stored in the database. Site-specific extraction logic lives in
Python adapters, so adding a new source means adding an adapter module and a
`Source` row, not changing the orchestration code.

## Features

- Pluggable job source adapters with a shared registry.
- Source configuration stored in the `sources` table.
- Idempotent job upserts on `(source, external_id)`.
- JSON scraping via `httpx` and HTML scraping via `curl_cffi` (Chrome
  impersonation with BeautifulSoup) with timeouts and retries.
- CLI for managing sources and running scrapes.
- Pluggable product source adapters with a Bergfreunde HTML adapter.
- Canonical products linked to multiple shop-specific offers.
- Daily product scraping plus a manual UI scrape button.
- Shared scrape lock so job and product scrapes do not run concurrently.
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
| `SCRAPER_USER_AGENT` | `notification-service/0.1` | `User-Agent` header used for JSON scrape requests |
| `SCRAPER_IMPERSONATE` | `chrome` | Browser TLS fingerprint impersonated for HTML scrape requests; empty disables impersonation |
| `SCRAPER_HTML_USER_AGENT` | empty | `User-Agent` used for plain HTML requests (falls back to `SCRAPER_USER_AGENT`) |
| `SCRAPER_PROXY_URL` | empty | Optional HTTP(S) proxy for all scrape requests |
| `SCRAPER_HTML_PROXY_URL` | empty | Optional HTTP(S) proxy for HTML sources (falls back to `SCRAPER_PROXY_URL`) |
| `SCRAPER_TIMEOUT_SECONDS` | `15` | Timeout for scrape requests in seconds |
| `PRODUCT_SCRAPE_INTERVAL_SECONDS` | `86400` | Minimum age before a product offer is scraped again; `0` disables the product scheduler |

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
- `/products` — canonical products with their lowest price, linked shop count,
  last scrape time, and a **Scrape products** button.
- `/products/{id}` — product detail with shop offers, prices, sizes,
  availability, scrape errors, and forms to link or unlink shop offers.

The **Scrape jobs** button runs all enabled `jobs` sources synchronously and
shows per-source fetched/inserted/updated/skipped/failed counts, plus any source
errors. The job link and posted date are read from each job's `raw_data`
(`link`, `date_posted` with a `date_created` fallback), so no extra database
columns are required.

The **Scrape products** button force-scrapes all linked product offers. Job and
product scrapes share an in-process lock: scheduled runs skip while another
scrape is active, and manual runs wait for it to finish.

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

For products, a `Source` row represents a webshop. The shop base URL is
optional; product URLs are linked per product in the UI.

```bash
uv run python -m app.cli sources add \
  --slug bergfreunde \
  --kind products \
  --adapter bergfreunde_html \
  --url 'https://www.bergfreunde.fi/' \
  --config-json '{"http": {"impersonate": "chrome"}}'
```

Per-source scraper settings live in the `sources.config` JSON column:

```bash
uv run python -m app.cli sources add \
  --slug jobly \
  --kind jobs \
  --adapter jobly_html \
  --url '<jobly-url>' \
  --config-json '{
    "http": {
      "client": "curl_cffi",
      "impersonate": "none",
      "user_agent": "curl/8.14.1",
      "proxy_env": "SCRAPER_HTML_PROXY_URL",
      "timeout_seconds": 30
    }
  }'
```

`config.http` overrides the global `SCRAPER_*` settings. Supported fields are
`client` (`curl_cffi` or `httpx`), `impersonate` (`chrome`, `safari`, `none`),
`user_agent`, `proxy_env`, `timeout_seconds`, and `http_version` (`v1.1` or
`v2`). `proxy_env` names an environment variable containing the proxy URL so
credentials are not stored in the database. If it is set but the variable is
missing, the scrape fails with a clear error.

List sources:

```bash
uv run python -m app.cli sources list
uv run python -m app.cli sources list --kind jobs
```

`sources add` is create-or-update: running it again with the same slug updates
the existing row. Omit `--config-json` to keep an existing source's config.

## Run scraping

Run one source:

```bash
uv run python -m app.cli scrape --source duunitori
```

Run all enabled sources of a kind:

```bash
uv run python -m app.cli scrape --kind jobs
uv run python -m app.cli scrape --kind products
```

Job scrape behavior:

- Jobs are upserted on `(source, external_id)`.
- Existing jobs have `title`, `raw_data`, and `scraped_at` updated.
- New jobs are inserted.
- Duplicate `external_id` values in one payload are skipped.
- One failing source is logged and does not stop other sources.
- The command exits with code `1` if any source fails.
- Failed HTTP responses log the status, useful headers (`server`, `cf-ray`,
  `cf-mitigated`, `content-type`, `location`, `retry-after`), and a truncated
  body so blocked responses can be diagnosed from production logs.
- `429` and `5xx` responses are retried; other `4xx` responses fail immediately.

Product scrape behavior:

- Each linked product URL is fetched by its shop adapter.
- Price, currency, availability, image, sizes, raw data, and scraped time are
  updated on the product-source link.
- A failing offer records `last_error` and does not stop other offers.
- The product scheduler skips offers scraped within
  `PRODUCT_SCRAPE_INTERVAL_SECONDS` (default one day); the manual UI/CLI scrape
  forces all linked offers.
- Product adapters use no proxy unless that shop's Source config sets
  `proxy_env`, keeping optional proxy usage to the shops that need it.

### Cron example

```cron
*/30 * * * * cd /path/to/notification-service && /usr/bin/env uv run python -m app.cli scrape --kind jobs >> /tmp/notification-scrape.log 2>&1
0 6 * * * cd /path/to/notification-service && /usr/bin/env uv run python -m app.cli scrape --kind products >> /tmp/product-scrape.log 2>&1
```

The FastAPI process also runs in-process schedulers when
`SCRAPE_INTERVAL_SECONDS` and `PRODUCT_SCRAPE_INTERVAL_SECONDS` are positive.
Job and product scrapes share a lock, so scheduled runs skip and manual runs
wait when another scrape is active. Keep the API at one instance so the lock
remains effective.

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
  models/       Job, Source, Product, and ProductSource models
  scrapers/     adapter protocols, HTTP helper, registry, source adapters
  services/     job/product repositories, scrape orchestration, schedulers
  cli.py        source management and scrape CLI
migrations/     Alembic migrations
tests/          unit and service tests
```

Scrape flow:

```text
Job:      Source row -> adapter registry -> ScrapedJob -> jobs table
Product:  Source row + ProductSource URL -> adapter registry
          -> ScrapedProduct -> product_sources table
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
- `kind` (`jobs` or `products`)
- `adapter`
- `url`
- `enabled`
- `config`
- `created_at`
- `updated_at`

For products, a Source is a webshop. Its `url` is optional because individual
product URLs live on `product_sources`.

`products`:

- `title`
- `image_url`
- `created_at`
- `updated_at`

`product_sources`:

- `product_id`
- `source_id`
- `url`
- `external_id`
- `title`
- `image_url`
- `price`
- `currency`
- `availability`
- `sizes`
- `raw_data`
- `last_scraped_at`
- `last_error`
- `created_at`
- `updated_at`

`product_sources` has a unique constraint on `(source_id, url)`.

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

Set `SCRAPER_HTML_PROXY_URL` to route HTML sources (currently Jobly) through a
proxy while JSON sources stay direct. A sticky proxy is recommended so the
Cloudflare clearance cookie remains valid across pages.

To test a plain curl-like client, set `SCRAPER_IMPERSONATE=` (empty) and
optionally `SCRAPER_HTML_USER_AGENT=curl/<version>`. In that mode HTML requests
use libcurl's default fingerprint with minimal headers and no referer or
`Sec-Fetch-*` headers.

The shared HTTP helpers provide `fetch_json` (via `httpx`) and `fetch_text`
(via `curl_cffi` with browser TLS/HTTP2 impersonation), both with timeouts and
retries. Use `fetch_text` with BeautifulSoup for HTML sources. `fetch_text`
accepts an optional `session` so an adapter can reuse one `curl_cffi` session
across pages and keep cookies.

## Product aggregation and linking

A `Product` is the canonical product (for example La Sportiva Solution). Each
`product_sources` row links that product to a shop-specific URL and stores the
latest scraped price, sizes, availability, image, and raw data.

The product UI supports:

- creating canonical products with a title and optional image;
- linking and unlinking shop product URLs;
- viewing per-shop prices, sizes, availability, scrape times, and errors;
- viewing the lowest linked price and the union of sizes for a product.

Shop rows are managed with the CLI, for example:

```bash
uv run python -m app.cli sources add \
  --slug bergfreunde \
  --kind products \
  --adapter bergfreunde_html \
  --config-json '{"http": {"impersonate": "chrome"}}'
```

## Adding a new product source

Product adapters live under `app/scrapers/products/<source>.py` and use the same
registry pattern as job adapters. A product Source represents the webshop; the
adapter receives that Source plus the specific product URL.

1. Create a module under `app/scrapers/products/<source>.py`.
2. Implement an adapter with a unique `key` and:

   ```python
   async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]: ...
   ```

3. Register it with `@register_product_adapter`.
4. Import the adapter in `app/scrapers/products/__init__.py` so it is
   registered.
5. Add fixture-based tests for the mapping logic.

`ScrapedProduct` carries normalized `external_id`, `title`, `url`, `price`,
`currency`, `availability`, `image_url`, and `sizes` fields. Site-specific
details such as list price, stock text, ratings, and variant-level data stay in
`raw_data`.

Product adapters should resolve HTTP config with `allow_default_proxy=False` so
a scrape uses no proxy unless the shop Source explicitly configures
`config.http.proxy_env`.

The `bergfreunde_html` adapter fetches product pages with `curl_cffi` Chrome
impersonation (via `fetch_text` and `text_session`) and combines the page's
JSON-LD `Product` data with its embedded `window.oBfArticle` variant payload.
Bergfreunde is protected by Cloudflare, so browser impersonation must stay
enabled (`SCRAPER_IMPERSONATE=chrome`) or be set per source with
`{"http": {"impersonate": "chrome"}}`. Plain curl mode (`none`) is rejected with
HTTP 403.

Other webshop adapters (Varuste.net, Shop EpicTV, Partioaitta, Tradeinn, and
Aminoporssi) are follow-up work. Product notifications and price history are
not implemented yet.
