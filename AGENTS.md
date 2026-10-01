Commands

- `uv sync` - install dependencies
- `docker compose up -d db` - start Postgres; the app and the tests need it
- `uv run pytest` - the whole suite
- `uv run pytest tests/test_expenses.py` - one test file
- `VILOQ_E2E_BASE_URL=http://localhost:8000 uv run pytest tests/integration` -
  integration tests against `docker compose up -d --build --wait`

Rules

- Dependencies are added in `pyproject.toml`. Do not add one without
  asking