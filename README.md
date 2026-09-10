# viloq

Collaborative expense splitting with flexible splits, live balances, and simple settlement suggestions.

## Layout

| Path            | What it is                                                         |
| --------------- | ------------------------------------------------------------------ |
| `openapi.yaml`  | The API contract. Source of truth for both sides.                   |
| `app/`          | FastAPI backend implementing that contract, and serving `frontend/`.|
| `frontend/`     | The single-page app. All server calls live in `js/api.js`.          |
| `tests/`        | Endpoint, domain, and frontend-wiring tests.                        |
| `_docs/specs.md`| V1 product specification.                                           |

## Run it

```sh
uv sync                                   # install dependencies
uv run uvicorn app.main:app --reload      # serve on http://127.0.0.1:8000
uv run pytest                             # run the suite
```

Open http://127.0.0.1:8000/ — the app and its API are one origin, so the browser
calls `/api/v1/...` relatively and nothing needs configuring. Interactive docs
are at `/api/v1/docs`; the generated schema at `/api/v1/openapi.json`.

Data goes into `viloq.db` beside where you started the server, and survives a
restart; delete the file to start over, or set `VILOQ_DATABASE_URL` to put it
elsewhere (see [Storage](#storage)).

To host the page elsewhere instead, see [`frontend/README.md`](frontend/README.md);
`VILOQ_CORS_ORIGINS` controls which origins may call the API cross-origin
(defaulting to `localhost:5173` and `127.0.0.1:5173` for the static-server dev
flow).

## Backend

### How it is organised

```
app/
  main.py       FastAPI app, error handlers ({code, message}), frontend mount
  config.py     environment-driven settings
  deps.py       session auth, membership checks, If-Match parsing
  db.py         engine setup and the repository the routers use
  models.py     storage records, mapped to tables
  schemas.py    wire schemas, one per schema in openapi.yaml
  views.py      storage record -> wire shape
  domain/       pure logic: split allocation, balances, settlement
  routers/      one module per tag in openapi.yaml
```

`app/domain/` is a port of `frontend/js/lib/` (`split.js`, `balances.js`,
`settle.js`), so the allocation the form previews as you type is the one the
server stores. Money is always integer minor units; the split allocation uses
`Fraction` internally so it reconciles to the total exactly.

### Storage

A SQL database, reached through SQLAlchemy. `VILOQ_DATABASE_URL` chooses which
one; it defaults to `sqlite+pysqlite:///./viloq.db`, a file beside wherever the
server was started. Missing tables are created at startup — enough while the
schema only grows; one that changes shape will want migrations.

Nothing above `app/db.py` knows the dialect. The routers see only `Database`, a
repository of named queries over one session, and the columns in
`app/models.py` are the portable SQLAlchemy types. Pointing this at Postgres is
meant to be a driver install and a URL:

```sh
uv add psycopg
VILOQ_DATABASE_URL='postgresql+psycopg://user:pw@localhost/viloq' uv run uvicorn app.main:app
```

The two places that do care about the backend are `_sqlite_options` and
`_configure_sqlite` in `app/db.py`, which are skipped for any other dialect.

`Database.transaction()` groups a read-modify-write into one atomic unit,
committing on the way out and rolling back if the body raises — so a request
rejected halfway (a `409`, say) leaves nothing behind. A write outside such a
block commits on its own. Each request gets its own session.

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
