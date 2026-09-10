# viloq — frontend

A no-build, framework-free single-page app for the expense-splitting tool
described in [`../_docs/specs.md`](../_docs/specs.md). It talks to the FastAPI
backend in [`../app/`](../app/) over the contract in
[`../openapi.yaml`](../openapi.yaml).

## Run it

The backend serves this directory, so one process is the whole app:

```bash
uv run uvicorn app.main:app --reload   # from the repo root
# then open http://127.0.0.1:8000/
```

State lives on the server. The backend's store is in-memory, so restarting it
starts over.

### Serving the frontend separately

If you would rather host the page yourself, point it at the API by editing the
one line in `index.html`:

```html
<script>
  window.VILOQ_API_BASE = 'http://127.0.0.1:8000/api/v1';
</script>
```

```bash
python -m http.server 5173 --directory frontend
```

`http://localhost:5173` and `http://127.0.0.1:5173` are pre-approved CORS
origins; add your own with `VILOQ_CORS_ORIGINS` (comma-separated) on the server.

## The backend boundary

**All server communication lives in [`js/api.js`](js/api.js) and nowhere else.**
One exported function per `operationId` in the contract; the views never see a
URL, a header or a status code. It also:

- attaches the bearer session token,
- sends `If-Match: "<version>"` on every mutation of an existing record,
- turns any error body into `ApiError(code, message)` — views branch on `code`
  (`version_conflict`, `unauthorized`, `forbidden`, …) and show `message`
  verbatim.

`tests/test_frontend.py` checks that every path this file builds is one the
contract declares, and vice versa.

## Layout

```
index.html            entry point (and the API base URL)
styles.css            all styling (light/dark, responsive)
js/
  app.js              shell: session store, hash router, top bar, view dispatch
  api.js              >>> the backend boundary <<<
  lib/
    money.js          minor-unit parsing / formatting per currency
    split.js          resolve a split method + inputs to an exact allocation
    balances.js       derive net balances from transactions (pure)
    settle.js         minimized + relationship-preserving settlement suggestions
    dom.js            small DOM/util helpers
  views/
    auth.js           magic-link login, verify, invite join, account
    groups.js         group list + create
    dashboard.js      balances, settlement, recent expenses/payments, invite modal
    expense-form.js   add/edit expense with live allocation preview
    payment-form.js   record/edit a payment
    group-admin.js    members list + group settings + invite management
```

`js/lib/` is the client-side twin of the server's `app/domain/`: the same split,
balance and settlement rules, used only to preview an allocation while the user
types. The server resolves every stored amount itself, and its answer wins.

## Notes on the domain logic

- **Money** is always integer minor units. `split.js` allocates with a
  deterministic largest-remainder rule so every split reconciles exactly to the
  total.
- **Balances** are never stored — they are recomputed from expenses + payments
  on every read. (The spec's §15 prose inverts the payment sign; §9's settlement
  example is used as the source of truth instead.)
- **Settlement suggestions** are recommendations only. Recording a payment is a
  separate, explicit action.

## Signing in

The magic link is emailed in a real deployment. This backend has no mailer, so
while `VILOQ_EXPOSE_MAGIC_LINK` is on (the default) the response carries the
link and the sign-in screen shows it directly. Set `VILOQ_EXPOSE_MAGIC_LINK=0`
and the screen just says to check your email.

To exercise a multi-member group locally, open a second browser profile (or a
private window) and sign in as another address, then join with the group's
invite code.
