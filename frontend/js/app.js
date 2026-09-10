// App shell: session store, hash router, top bar, and view dispatch.

import api, { ApiError } from './api.js';
import { esc, setHTML, qs, on, toast } from './lib/dom.js';

import * as authView from './views/auth.js';
import * as groupsView from './views/groups.js';
import * as dashboardView from './views/dashboard.js';
import * as expenseFormView from './views/expense-form.js';
import * as paymentFormView from './views/payment-form.js';
import * as groupAdminView from './views/group-admin.js';

const SESSION_KEY = 'viloq.session.v1';

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const store = {
  realSessionToken: null, // the signed-in user's token
  sessionToken: null, // active token (may be a demo actor)
  actingAs: null, // { label } when impersonating a demo actor
  user: null,

  get isAuthed() {
    return Boolean(this.realSessionToken && this.user);
  },

  loadSession() {
    try {
      this.realSessionToken = localStorage.getItem(SESSION_KEY);
      this.sessionToken = this.realSessionToken;
    } catch {
      this.realSessionToken = null;
      this.sessionToken = null;
    }
  },

  setSession(token) {
    this.realSessionToken = token;
    this.sessionToken = token;
    this.actingAs = null;
    try {
      if (token) localStorage.setItem(SESSION_KEY, token);
      else localStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
  },

  actAs(token, label) {
    if (!token || token === this.realSessionToken) {
      this.sessionToken = this.realSessionToken;
      this.actingAs = null;
    } else {
      this.sessionToken = token;
      this.actingAs = { label };
    }
  },
};

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

const routes = [
  { pattern: /^\/?$/, view: () => (store.isAuthed ? groupsView : authView), name: 'home' },
  { pattern: /^\/login$/, view: () => authView, name: 'login' },
  { pattern: /^\/auth\/verify$/, view: () => authView, name: 'verify' },
  { pattern: /^\/join$/, view: () => authView, name: 'join' },
  { pattern: /^\/account$/, view: () => authView, name: 'account' },
  { pattern: /^\/groups\/new$/, view: () => groupsView, name: 'group-new' },
  { pattern: /^\/groups\/([^/]+)$/, view: () => dashboardView, name: 'dashboard' },
  { pattern: /^\/groups\/([^/]+)\/expenses\/new$/, view: () => expenseFormView, name: 'expense-new' },
  { pattern: /^\/groups\/([^/]+)\/expenses\/([^/]+)\/edit$/, view: () => expenseFormView, name: 'expense-edit' },
  { pattern: /^\/groups\/([^/]+)\/payments\/new$/, view: () => paymentFormView, name: 'payment-new' },
  { pattern: /^\/groups\/([^/]+)\/payments\/([^/]+)\/edit$/, view: () => paymentFormView, name: 'payment-edit' },
  { pattern: /^\/groups\/([^/]+)\/admin$/, view: () => groupAdminView, name: 'group-admin' },
];

export function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, queryString = ''] = raw.split('?');
  const query = Object.fromEntries(new URLSearchParams(queryString));
  for (const route of routes) {
    const m = path.match(route.pattern);
    if (m) return { name: route.name, view: route.view(), params: m.slice(1).map(decodeURIComponent), query };
  }
  return { name: 'not-found', view: null, params: [], query: {} };
}

export function navigate(hash) {
  if (location.hash === `#${hash}`) render();
  else location.hash = hash;
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

let appEl;
let currentToken = 0;

export async function render() {
  const token = ++currentToken;
  const ctx = parseHash();
  renderTopBar();

  // Auth gate: everything except the auth flows needs a signed-in user.
  const publicRoutes = new Set(['login', 'verify', 'join']);
  if (!store.isAuthed && !publicRoutes.has(ctx.name)) {
    navigate('/login');
    return;
  }

  const main = qs(appEl, '#view');
  main.setAttribute('aria-busy', 'true');

  try {
    if (ctx.name === 'not-found' || !ctx.view) {
      setHTML(main, notFoundHTML());
    } else {
      await ctx.view.render({
        root: main,
        params: ctx.params,
        query: ctx.query,
        routeName: ctx.name,
      });
    }
  } catch (err) {
    if (token !== currentToken) return; // superseded by a newer navigation
    handleRenderError(err, main);
  } finally {
    if (token === currentToken) main.removeAttribute('aria-busy');
  }
}

function handleRenderError(err, main) {
  console.error(err);
  if (err instanceof ApiError && err.code === 'unauthorized') {
    store.setSession(null);
    store.user = null;
    navigate('/login');
    return;
  }
  const message = err instanceof ApiError ? err.message : 'Something went wrong loading this view.';
  setHTML(
    main,
    `<div class="card card--pad">
       <h2>Couldn't load this page</h2>
       <p class="muted">${esc(message)}</p>
       <button class="btn" data-action="reload">Try again</button>
     </div>`,
  );
  on(main, 'click', '[data-action="reload"]', () => render());
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------

function renderTopBar() {
  const bar = qs(appEl, '#topbar');
  if (!store.isAuthed) {
    setHTML(
      bar,
      `<a class="brand" href="#/">viloq</a>
       <span class="topbar__spacer"></span>
       <span class="muted small">Split shared expenses</span>`,
    );
    return;
  }

  const name = store.user.displayName || store.user.email;
  setHTML(
    bar,
    `<a class="brand" href="#/">viloq</a>
     <nav class="topbar__nav">
       <a href="#/">Groups</a>
     </nav>
     <span class="topbar__spacer"></span>
     ${
       store.actingAs
         ? `<span class="pill pill--warn" title="You are acting as a demo member">acting as ${esc(store.actingAs.label)}</span>`
         : ''
     }
     <div class="menu" data-menu>
       <button class="menu__trigger" data-menu-trigger>${esc(name)} ▾</button>
       <div class="menu__panel" hidden>
         <a href="#/account">Account &amp; display name</a>
         <button data-action="reset-demo">Reset demo data</button>
         <button data-action="signout">Sign out</button>
       </div>
     </div>`,
  );

  const menu = qs(bar, '[data-menu]');
  on(menu, 'click', '[data-menu-trigger]', () => {
    qs(menu, '.menu__panel').hidden = !qs(menu, '.menu__panel').hidden;
  });
  document.addEventListener(
    'click',
    (e) => {
      if (!menu.contains(e.target)) qs(menu, '.menu__panel').hidden = true;
    },
    { once: true },
  );
  on(menu, 'click', '[data-action="signout"]', async () => {
    await api.signOut(store.realSessionToken).catch(() => {});
    store.setSession(null);
    store.user = null;
    toast('Signed out');
    navigate('/login');
  });
  on(menu, 'click', '[data-action="reset-demo"]', async () => {
    if (!confirm('Reset all mock data back to the demo group? This clears every group, expense and payment in this browser.')) return;
    await api.resetDemoData();
    store.setSession(null);
    store.user = null;
    toast('Demo data reset');
    navigate('/login');
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

function notFoundHTML() {
  return `<div class="card card--pad">
    <h2>Page not found</h2>
    <p class="muted">That route doesn't exist.</p>
    <a class="btn" href="#/">Back to your groups</a>
  </div>`;
}

async function boot() {
  appEl = document.getElementById('app');
  setHTML(
    appEl,
    `<header id="topbar" class="topbar"></header>
     <main id="view" class="view"></main>
     <footer class="appfoot">
       <span>Mock backend — data lives in this browser only.</span>
       <span class="dot">•</span>
       <span>All server calls run through <code>js/api.js</code></span>
     </footer>`,
  );

  store.loadSession();
  if (store.realSessionToken) {
    try {
      store.user = await api.getCurrentUser(store.realSessionToken);
      if (!store.user) store.setSession(null);
    } catch {
      store.setSession(null);
    }
  }

  window.addEventListener('hashchange', render);
  // Cross-tab: another tab mutated the mock DB or auth state.
  window.addEventListener('storage', (e) => {
    if (e.key === 'viloq.mock.db.v1') {
      toast('Updated from another tab');
      render();
    }
    if (e.key === SESSION_KEY) {
      store.loadSession();
      api.getCurrentUser(store.realSessionToken).then((u) => {
        store.user = u;
        render();
      });
    }
  });

  render();
}

boot();
