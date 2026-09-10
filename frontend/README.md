# viloq — frontend

A no-build, framework-free single-page app for the expense-splitting tool described
in [`../_docs/specs.md`](../_docs/specs.md). The backend is **not** implemented yet;
every server call goes through a single mocked module.

## Run it

ES modules need to be served over HTTP (not `file://`):

```bash
python -m http.server 5173 --directory frontend
# then open http://localhost:5173/
```

First sign-in seeds a populated demo group ("Lisbon Trip") so every feature is
usable immediately. Data is stored in `localStorage` for the current browser only.

## The backend boundary

**All server communication lives in [`js/api.js`](js/api.js) and nowhere else.**
It currently runs an in-browser mock: `localStorage` persistence, simulated
latency, high-entropy invite tokens, and per-record `version` fields for
optimistic-concurrency conflicts.

To connect a real backend, replace the bodies of the exported `api.*` methods
with `fetch` calls. The method signatures and return shapes are the contract the
UI depends on — keep them stable and the views need no changes.

## Layout

```
index.html            entry point
styles.css            all styling (light/dark, responsive)
js/
  app.js              shell: session store, hash router, top bar, view dispatch
  api.js              >>> the backend boundary (mocked) <<<
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

## Notes on the domain logic

- **Money** is always integer minor units. `split.js` allocates with a
  deterministic largest-remainder rule so every split reconciles exactly to the
  total.
- **Balances** are never stored — `balances.js` recomputes them from expenses +
  payments on every read. (The spec's §15 prose inverts the payment sign; §9's
  settlement example is used as the source of truth instead.)
- **Settlement suggestions** are recommendations only. Recording a payment is a
  separate, explicit action.

## Demo affordances (mock only)

- The magic link is shown in the UI instead of emailed.
- The dashboard has an **"Act as"** switch to impersonate the seeded members
  (Alice / Bob / Carol) so you can record payments as different people and test
  permissions in a single browser.
- The account menu has **"Reset demo data"** to wipe `localStorage` and reseed.
