# viloq

Collaborative expense splitting with flexible splits, live balances, and simple settlement suggestions.

## Layout

| Path            | What it is                                                         |
| --------------- | ------------------------------------------------------------------ |
| `openapi.yaml`  | The API contract. Source of truth for both sides.                   |
| `backend/`      | FastAPI backend implementing that contract, and serving `frontend/`.|
| `frontend/`     | The single-page app. All server calls live in `js/api.js`.          |
| `tests/`        | Endpoint, domain, persistence, concurrency, and contract/wiring tests.|
| `_docs/specs.md`| V1 product specification.                                           |
| `Dockerfile`    | Two-stage image: Node checks the frontend, Python serves it all.    |
| `compose.yaml`  | The app's image plus Postgres; `db` alone for dev and tests.        |

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
