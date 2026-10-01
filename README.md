# viloq

Collaborative expense splitting with flexible splits, live balances, and simple settlement suggestions.

## Layout

| Path            | What it is                                                         |
| --------------- | ------------------------------------------------------------------ |
| `openapi.yaml`  | The API contract. Source of truth for both sides.                   |
| `backend/`      | FastAPI backend implementing that contract, and serving `frontend/`.|
| `frontend/`     | The single-page app. All server calls live in `js/api.js`.          |
| `tests/`        | Endpoint, domain, persistence, concurrency, contract/wiring, and stack tests.|
| `_docs/specs.md`| V1 product specification.                                           |
| `Dockerfile`    | Two-stage image: Node checks the frontend, Python serves it all.    |
| `compose.yaml`  | The app's image plus Postgres; `db` alone for dev and tests.        |
| `render.yaml`   | Render Blueprint: the same image plus a managed Postgres.           |
| `.github/workflows/ci-cd.yml` | Tests every push and PR; deploys `main` to Render.    |

## Run it

```sh
docker compose up -d db                   # start Postgres on localhost:5432
uv sync                                   # install dependencies
uv run uvicorn backend.main:app --reload  # serve on http://127.0.0.1:8000
uv run pytest                             # run the suite (needs the db too)
```

Open http://127.0.0.1:8000/ — the app and its API are one origin, so the browser
calls `/api/v1/...` relatively and nothing needs configuring. Interactive docs
are at `/api/v1/docs`; the generated schema at `/api/v1/openapi.json`.

Data goes into the `viloq` database on that Postgres and lives in the `db-data`
volume, so it survives restarts; `docker compose down -v` deletes it. Set
`VILOQ_DATABASE_URL` to use a different server (see [Storage](#storage)).

To host the page elsewhere instead, see [`frontend/README.md`](frontend/README.md);
`VILOQ_CORS_ORIGINS` controls which origins may call the API cross-origin
(defaulting to `localhost:5173` and `127.0.0.1:5173` for the static-server dev
flow).

### In Docker

```sh
docker compose up --build    # app on http://localhost:8000/, plus Postgres
```

The image's first stage checks every frontend module parses under Node and
assembles the static files; the second is the backend, which serves them at
`/`. The image holds no data: `compose.yaml` runs it next to Postgres and
passes `VILOQ_DATABASE_URL`, and starts it once the database is accepting
connections. CORS is off in the image, since the page and API share an origin.

`tests/integration/` checks a running stack over real HTTP — the image's
static files, auth, groups, expenses, conflicts and settlement.
`tests/e2e/` drives the page in Chromium through Playwright: sign in, create a
group, invite a second user, split an expense, settle up. Both are skipped
unless `VILOQ_E2E_BASE_URL` points at a stack:

```sh
docker compose up -d --build --wait
uv run playwright install chromium        # once, for tests/e2e
VILOQ_E2E_BASE_URL=http://localhost:8000 uv run pytest tests/integration tests/e2e
```

`GET /healthz` is `200 {"status": "ok", "commit": ...}` while the app and its
database are up, `503` otherwise. It sits outside `/api/v1` and the contract:
it is for the host and the pipeline, not the frontend.

### On Render

`render.yaml` is a [Render Blueprint](https://render.com/docs/blueprint-spec):
the image as a web service plus a managed Postgres in the same region. In the
Render dashboard, New > Blueprint, pick this repo, and apply. Render's own
auto-deploy is off; the pipeline below deploys instead. Render's database URL is plain `postgresql://...`, which
`backend/config.py` turns into the psycopg URL SQLAlchemy needs.

The Blueprint sets `VILOQ_EXPOSE_MAGIC_LINK=0`, since a public server that
echoes the token lets anyone sign in as anyone; until there is a mailer,
nobody can sign in there. The same goes for `tests/integration`, which signs in
through the echoed token, so it cannot run against this deploy either.

### CI/CD

`.github/workflows/ci-cd.yml` runs on every push and pull request:

1. **Backend tests** (`pytest`, against a Postgres service) and **frontend
   tests** (`node --check` on every module, then `node --test` on
   `frontend/tests/`) run in parallel.
2. **Integration and e2e** builds the Compose stack and runs
   `tests/integration` and `tests/e2e` against it.
3. **Deploy**, on `main` only: calls the Render deploy hook for that commit,
   follows that deploy through Render's API until it is `live` (failing on
   any failed or cancelled state, or after 20 minutes), then checks that
   `/healthz` reports `"status": "ok"` with that commit.

The deploy job needs repository secrets `RENDER_DEPLOY_HOOK_URL` (the web
service's Settings > Deploy Hook) and `RENDER_API_KEY` (Render's Account
Settings > API Keys), and a variable `RENDER_SERVICE_URL` (its public URL). It
runs in the `production` environment, where approvals can be required.

Frontend unit tests alone: `node --test 'frontend/tests/**/*.test.js'` (Node 22+).

## Backend

### How it is organised

```
backend/
  main.py       FastAPI app, exception handlers, frontend mount
  errors.py     the one error shape ({code, message}) and its constructors
  config.py     environment-driven settings
  deps.py       session auth, membership checks, If-Match parsing
  db.py         engine setup and the repository the routers use
  models.py     storage records, mapped to tables
  schemas.py    wire schemas, one per schema in openapi.yaml
  views.py      storage record -> wire shape
  domain/       pure logic: split allocation, balances, settlement
  routers/      one module per tag in openapi.yaml
```

`backend/domain/` is a port of `frontend/js/lib/` (`split.js`, `balances.js`,
`settle.js`), so the allocation the form previews as you type is the one the
server stores. Money is always integer minor units; the split allocation uses
`Fraction` internally so it reconciles to the total exactly.

### Storage

Postgres, reached through SQLAlchemy and the psycopg driver.
`VILOQ_DATABASE_URL` names the database; it defaults to
`postgresql+psycopg://viloq:viloq@localhost:5432/viloq`, the `db` service from
`compose.yaml`. Missing tables are created at startup — enough while the schema
only grows; one that changes shape will want migrations.

The routers see only `Database` in `backend/db.py`, a repository of named
queries over one session, so nothing above it writes SQL.

`Database.transaction()` groups a read-modify-write into one atomic unit,
committing on the way out and rolling back if the body raises — so a request
rejected halfway (a `409`, say) leaves nothing behind. Every read inside one
locks the rows it returns (`SELECT ... FOR UPDATE`) until the commit: under
Postgres's default isolation, two requests could otherwise both read version 1,
both pass the `If-Match` check, and both write. With the lock, the second waits
and then sees the first one's change. A write outside such a block commits on
its own. Each request gets its own session.

The tests run against Postgres too, in a separate `viloq_test` database on the
same server (created on first run, emptied after every test). Point
`VILOQ_TEST_DATABASE_URL` elsewhere to override; its name must end in `_test`.

### Conventions the contract fixes

- **Auth** — opaque bearer token from `POST /api/v1/auth/magic-links/verify`.
  While `VILOQ_EXPOSE_MAGIC_LINK` is on (the default, since there is no mailer),
  the magic-link response echoes the token so a local UI can follow the link.
  A real deployment emails it and sets `VILOQ_EXPOSE_MAGIC_LINK=0`.
- **Optimistic concurrency** — `group`, `expense` and `payment` carry a
  `version`. Mutating an existing record requires `If-Match: "<version>"`;
  omitting it is `412`, a stale value is `409` with the current record attached.
  Responses that return one versioned record also send an `ETag`.
- **Errors** — always `{code, message}`, including for malformed requests
  (`400 validation`, never a `422`).
