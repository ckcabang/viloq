// Groups list + create-group flow.

import api, { ApiError } from '../api.js';
import { store, navigate } from '../app.js';
import { esc, setHTML, qs, on, toast } from '../lib/dom.js';
import { formatMoney, currencyList } from '../lib/money.js';

export async function render(ctx) {
  if (ctx.routeName === 'group-new') return renderCreate(ctx);
  return renderList(ctx);
}

async function renderList(ctx) {
  const { root } = ctx;
  const groups = await api.listGroups(store.sessionToken);

  setHTML(
    root,
    `<div class="page stack">
       <div class="page__head">
         <h1>Your groups</h1>
         <a class="btn btn--primary" href="#/groups/new">New group</a>
       </div>
       ${
         groups.length === 0
           ? `<div class="card card--pad empty">
                <p>You're not in any groups yet.</p>
                <a class="btn btn--primary" href="#/groups/new">Create your first group</a>
              </div>`
           : `<ul class="grouplist">
               ${groups.map(groupRow).join('')}
             </ul>`
       }
       <div class="card card--pad stack">
         <h2>Have an invite code?</h2>
         <form data-form="join" class="row">
           <input name="code" placeholder="ABC-DEF-GHJ" required />
           <button class="btn" type="submit">Open invite</button>
         </form>
       </div>
     </div>`,
  );

  on(root, 'submit', '[data-form="join"]', (e) => {
    e.preventDefault();
    const code = String(new FormData(e.target).get('code') || '').trim().toUpperCase();
    if (code) navigate(`/join?code=${encodeURIComponent(code)}`);
  });
}

function groupRow(g) {
  const net = g.myNetMinor;
  const status =
    net > 0
      ? `<span class="amount amount--pos">you're owed ${esc(formatMoney(net, g.currency))}</span>`
      : net < 0
        ? `<span class="amount amount--neg">you owe ${esc(formatMoney(-net, g.currency))}</span>`
        : `<span class="amount amount--zero">settled up</span>`;
  return `<li>
     <a class="grouplist__item" href="#/groups/${esc(g.id)}">
       <div>
         <div class="grouplist__name">${esc(g.name)}</div>
         <div class="muted small">${esc(String(g.memberCount))} members · ${esc(String(g.expenseCount))} expenses · ${esc(g.currency)}</div>
       </div>
       <div class="grouplist__status">${status}</div>
     </a>
   </li>`;
}

// ---------------------------------------------------------------------------

async function renderCreate(ctx) {
  const { root } = ctx;
  setHTML(
    root,
    `<div class="page-narrow stack">
       <a class="back" href="#/">← Groups</a>
       <div class="card card--pad stack">
         <h1>New group</h1>
         <form data-form="create" class="stack">
           <label>Group name
             <input name="name" required placeholder="Roommates, Ski trip, …" />
           </label>
           <label>Currency
             <select name="currency" required>
               ${currencyList.map((c) => `<option value="${c.code}">${esc(c.code)} — ${esc(c.label)}</option>`).join('')}
             </select>
           </label>
           <label>Your display name in this group
             <input name="displayName" required value="${esc(store.user.displayName || '')}" />
           </label>
           <p class="muted small">Currency is locked once the group has its first expense.</p>
           <button class="btn btn--primary" type="submit">Create group</button>
         </form>
       </div>
     </div>`,
  );

  on(root, 'submit', '[data-form="create"]', async (e) => {
    e.preventDefault();
    const btn = qs(e.target, 'button[type="submit"]');
    btn.disabled = true;
    const fd = new FormData(e.target);
    try {
      const group = await api.createGroup(store.sessionToken, {
        name: fd.get('name'),
        currency: fd.get('currency'),
        displayName: fd.get('displayName'),
      });
      toast('Group created');
      navigate(`/groups/${group.id}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not create group', 'error');
      btn.disabled = false;
    }
  });
}
