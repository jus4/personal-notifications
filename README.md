# Notification Service

A personal project for data scraping web sources and notifications service.
Build with FastAPI, SQLModel and Alembic.

## Features
- Job scaping support for duunitori and Jobly
- Product scaping support for the following webshops
    - Varuste.net
    - Bergfreunde
    - Aminopörssi
    - EpicTV
    - Tradeinn
    - Hardloop
- Websocket support for admin user for showing notifications on new jobs. `scripts` folder includes a sample script for websocket client that can be used as a running service and send notifications via `dunst`
- Admin management for managing sources, products and webshop sources
- Basic cli for managing sources
- curl_cffi tp bypass Cloudflare on source configs
- External proxy support to configs if Cloudflare has blocked by IP
- BeautifulSoup for HTML scraping
- Nix-shell for development


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


## Manage sources

Sources can also be managed from the web UI at `/sources`, linked in the header
navigation. The page supports adding, editing, enabling/disabling, and deleting
both job and product sources. Scraper config is entered as a JSON object in a
text field. Deleting a source is blocked while product offers are linked to it.

Source configs

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

Create or update a source by slug with the CLI:

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
