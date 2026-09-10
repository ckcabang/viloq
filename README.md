# viloq

Collaborative expense splitting with flexible splits, live balances, and simple settlement suggestions.

## Layout

| Path            | What it is                                                         |
| --------------- | ------------------------------------------------------------------ |
| `openapi.yaml`  | The API contract. Source of truth for both sides.                   |
| `app/`          | FastAPI backend implementing that contract.                         |
| `frontend/`     | Mocked frontend (in-browser `localStorage` backend in `js/api.js`). |
| `tests/`        | Endpoint and domain tests.                                          |
| `_docs/specs.md`| V1 product specification.                                           |

## Backend

```sh
uv sync                                   # install dependencies
uv run uvicorn app.main:app --reload      # serve on http://127.0.0.1:8000
uv run pytest                             # run the suite
```

Interactive docs are at `/api/v1/docs`; the generated schema at `/api/v1/openapi.json`.

### How it is organised

```
app/
  main.py       FastAPI app, error handlers ({code, message} for every failure)
  config.py     environment-driven settings
  deps.py       session auth, membership checks, If-Match parsing
  db.py         the mock database (in-memory; state is lost on restart)
  models.py     internal storage records
  schemas.py    wire schemas, one per schema in openapi.yaml
  views.py      storage record -> wire shape
  domain/       pure logic: split allocation, balances, settlement
  routers/      one module per tag in openapi.yaml
```

`app/domain/` is a port of `frontend/js/lib/` (`split.js`, `balances.js`,
`settle.js`), so the server resolves splits and balances exactly as the mocked
frontend does. Money is always integer minor units; the split allocation uses
`Fraction` internally so it reconciles to the total exactly.

### Storage

`app/db.py` is a deliberate placeholder: a single in-process `Database` object
holding plain dicts. Everything goes through it, so swapping in a real database
means reimplementing that one class without touching the routers. It is not safe
across multiple processes and does not persist.

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
