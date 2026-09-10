// Auth flows: magic-link login, link verification, invite join, account page.

import api, { ApiError } from '../api.js';
import { store, navigate, render as rerender } from '../app.js';
import { esc, setHTML, qs, on, toast, copyToClipboard } from '../lib/dom.js';

export async function render(ctx) {
  switch (ctx.routeName) {
    case 'verify':
      return renderVerify(ctx);
    case 'join':
      return renderJoin(ctx);
    case 'account':
      return renderAccount(ctx);
    default:
      return renderLogin(ctx);
  }
}

// ---------------------------------------------------------------------------
// Login — request a magic link
// ---------------------------------------------------------------------------

function renderLogin(ctx, { heading = 'Sign in', intro = 'We email you a one-time link. No password.' } = {}) {
  const { root, query } = ctx;
  const nextHash = query.next || '#/';

  if (store.isAuthed) {
    navigate(query.next ? decodeURIComponent(query.next).replace(/^#/, '') : '/');
    return;
  }

  setHTML(
    root,
    `<div class="auth">
       <div class="card card--pad">
         <h1>${esc(heading)}</h1>
         <p class="muted">${esc(intro)}</p>
         <form data-form="request" class="stack">
           <label>Email address
             <input type="email" name="email" required autocomplete="email" placeholder="you@example.com" />
           </label>
           <button class="btn btn--primary" type="submit">Email me a link</button>
         </form>
         <div data-sent hidden class="notice notice--ok stack">
           <strong>Magic link ready</strong>
           <p class="muted small">In a real deployment this arrives by email and expires in
             <span data-ttl></span> minutes. For this mock, use the link below.</p>
           <a class="btn btn--primary" data-link href="#">Open my magic link</a>
           <button class="btn btn--ghost" data-action="resend" type="button">Send another link</button>
         </div>
       </div>
       <p class="auth__foot muted small">Signing out never removes you from a group.</p>
     </div>`,
  );

  root.dataset.next = nextHash;
  const sent = qs(root, '[data-sent]');
  const form = qs(root, '[data-form="request"]');

  on(form, 'submit', 'form', async (e) => {
    e.preventDefault();
    const btn = qs(form, 'button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api.requestMagicLink(new FormData(form).get('email'));
      const linkHash = query.next
        ? `${res.magicLinkPath}&next=${encodeURIComponent(query.next)}`
        : res.magicLinkPath;
      qs(sent, '[data-link]').setAttribute('href', linkHash);
      qs(sent, '[data-ttl]').textContent = res.expiresInMinutes;
      form.hidden = true;
      sent.hidden = false;
      toast(`Link sent to ${res.email}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not send link', 'error');
    } finally {
      btn.disabled = false;
    }
  });

  on(sent, 'click', '[data-action="resend"]', () => {
    sent.hidden = true;
    form.hidden = false;
  });
}

// ---------------------------------------------------------------------------
// Verify — consume the token in the URL
// ---------------------------------------------------------------------------

async function renderVerify(ctx) {
  const { root, query } = ctx;
  setHTML(root, `<div class="auth"><div class="card card--pad"><p class="muted">Verifying your link…</p></div></div>`);

  if (!query.token) {
    setHTML(root, authError('This link is missing its token.'));
    return;
  }

  try {
    const { sessionToken, user, needsDisplayName } = await api.verifyMagicLink(query.token);
    store.setSession(sessionToken);
    store.user = user;

    if (needsDisplayName) {
      renderNameSetup(root, query.next);
      return;
    }
    toast(`Signed in as ${user.displayName}`);
    finishAuth(query.next);
  } catch (err) {
    setHTML(
      root,
      authError(err instanceof ApiError ? err.message : 'Could not verify this link.', true),
    );
    on(root, 'click', '[data-action="back-to-login"]', () => navigate('/login'));
  }
}

function renderNameSetup(root, next) {
  setHTML(
    root,
    `<div class="auth"><div class="card card--pad">
       <h1>Pick a display name</h1>
       <p class="muted">This is how other people see you. Your email stays private.</p>
       <form data-form="name" class="stack">
         <label>Display name
           <input name="displayName" required autocomplete="name" placeholder="e.g. Sam" />
         </label>
         <button class="btn btn--primary" type="submit">Continue</button>
       </form>
     </div></div>`,
  );
  on(root, 'submit', '[data-form="name"]', async (e) => {
    e.preventDefault();
    const name = new FormData(e.target).get('displayName');
    try {
      store.user = await api.updateDisplayName(store.sessionToken, name);
      toast('Welcome!');
      finishAuth(next);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save name', 'error');
    }
  });
}

function finishAuth(next) {
  if (next) navigate(decodeURIComponent(next).replace(/^#/, ''));
  else navigate('/');
}

// ---------------------------------------------------------------------------
// Join — open an invite
// ---------------------------------------------------------------------------

async function renderJoin(ctx) {
  const { root, query } = ctx;
  const code = query.code;
  if (!code) {
    setHTML(root, authError('This invite link is missing its code.'));
    return;
  }

  setHTML(root, `<div class="auth"><div class="card card--pad"><p class="muted">Looking up invite…</p></div></div>`);

  let info;
  try {
    info = await api.getInviteInfo(code);
  } catch (err) {
    setHTML(root, authError(err instanceof ApiError ? err.message : 'Invalid invite.'));
    return;
  }

  if (info.revoked) {
    setHTML(root, authError('This invite has been revoked. Ask the group for a fresh link.'));
    return;
  }

  if (!store.isAuthed) {
    renderLogin(
      { ...ctx, query: { ...query, next: `#/join?code=${encodeURIComponent(code)}` } },
      {
        heading: `Join "${info.groupName}"`,
        intro: 'Sign in with your email to join this group.',
      },
    );
    return;
  }

  setHTML(
    root,
    `<div class="auth"><div class="card card--pad">
       <h1>Join "${esc(info.groupName)}"</h1>
       <p class="muted">${esc(String(info.memberCount))} member(s) · currency ${esc(info.currency)}</p>
       <form data-form="join" class="stack">
         <label>Your display name in this group
           <input name="displayName" required value="${esc(store.user.displayName || '')}" />
         </label>
         <button class="btn btn--primary" type="submit">Join group</button>
       </form>
     </div></div>`,
  );

  on(root, 'submit', '[data-form="join"]', async (e) => {
    e.preventDefault();
    const displayName = new FormData(e.target).get('displayName');
    try {
      const { group } = await api.joinGroup(store.sessionToken, code, displayName);
      toast(`Joined ${group.name}`);
      navigate(`/groups/${group.id}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not join', 'error');
    }
  });
}

// ---------------------------------------------------------------------------
// Account — global display name
// ---------------------------------------------------------------------------

async function renderAccount(ctx) {
  const { root } = ctx;
  const user = store.user;
  setHTML(
    root,
    `<div class="page-narrow stack">
       <a class="back" href="#/">← Groups</a>
       <div class="card card--pad stack">
         <h1>Account</h1>
         <p class="muted small">Email <strong>${esc(user.email)}</strong> — private, never shown to other members.</p>
         <form data-form="name" class="stack">
           <label>Display name
             <input name="displayName" required value="${esc(user.displayName || '')}" />
           </label>
           <p class="muted small">This is the default name suggested when you join new groups.
             Each group can also override your name locally.</p>
           <button class="btn btn--primary" type="submit">Save</button>
         </form>
       </div>
     </div>`,
  );

  on(root, 'submit', '[data-form="name"]', async (e) => {
    e.preventDefault();
    try {
      store.user = await api.updateDisplayName(store.sessionToken, new FormData(e.target).get('displayName'));
      toast('Saved');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save', 'error');
    }
  });
}

// ---------------------------------------------------------------------------

function authError(message, withBack = false) {
  return `<div class="auth"><div class="card card--pad stack">
     <h1>Link problem</h1>
     <p class="muted">${esc(message)}</p>
     ${withBack ? '<button class="btn" data-action="back-to-login">Back to sign in</button>' : '<a class="btn" href="#/login">Back to sign in</a>'}
   </div></div>`;
}

// exported for potential reuse
export { copyToClipboard };
