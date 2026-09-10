// Members list + group settings (name, currency, invite management).

import api, { ApiError } from '../api.js';
import { store, navigate, render as rerender } from '../app.js';
import { esc, setHTML, qs, on, toast, formatDate } from '../lib/dom.js';
import { currencyList } from '../lib/money.js';

export async function render(ctx) {
  const [groupId] = ctx.params;
  const snap = await api.getGroupSnapshot(store.sessionToken, groupId);
  const g = snap.group;

  setHTML(
    ctx.root,
    `<div class="page-narrow stack">
       <a class="back" href="#/groups/${esc(groupId)}">← ${esc(g.name)}</a>

       <div class="card card--pad stack">
         <h1>Members</h1>
         <ul class="memberlist">
           ${snap.members
             .map(
               (m) => `<li>
                 <span>${esc(m.displayName)}${m.isMe ? ' <span class="tag">you</span>' : ''}${m.userId === g.createdByUserId ? ' <span class="tag tag--muted">creator</span>' : ''}</span>
                 <span class="muted small">joined ${esc(formatDate(m.createdAt))}</span>
               </li>`,
             )
             .join('')}
         </ul>
         <form data-form="myname" class="row">
           <input name="displayName" required value="${esc(snap.me.displayName)}" />
           <button class="btn" type="submit">Update my name</button>
         </form>
       </div>

       <div class="card card--pad stack">
         <h2>Group settings</h2>
         ${
           g.isCreator
             ? `<form data-form="settings" class="stack">
                  <label>Group name
                    <input name="name" required value="${esc(g.name)}" />
                  </label>
                  <label>Currency
                    <select name="currency" ${g.currencyLocked ? 'disabled' : ''}>
                      ${currencyList.map((c) => `<option value="${c.code}" ${c.code === g.currency ? 'selected' : ''}>${esc(c.code)} — ${esc(c.label)}</option>`).join('')}
                    </select>
                  </label>
                  ${g.currencyLocked ? '<p class="muted small">Currency is locked because the group already has expenses.</p>' : ''}
                  <button class="btn btn--primary" type="submit">Save settings</button>
                </form>`
             : `<p class="muted small">Only the group creator can change the name or currency.</p>
                <dl class="kv"><dt>Name</dt><dd>${esc(g.name)}</dd><dt>Currency</dt><dd>${esc(g.currency)}</dd></dl>`
         }
       </div>

       <div class="card card--pad stack">
         <h2>Invite</h2>
         <p class="muted small">Current code: <code>${esc(g.inviteCode)}</code>${g.inviteRevoked ? ' <span class="tag tag--muted">revoked</span>' : ''}</p>
         ${
           g.isCreator
             ? `<div class="row">
                  <button class="btn" data-action="regen">Regenerate code</button>
                  <button class="btn btn--danger-ghost" data-action="revoke">Revoke</button>
                </div>`
             : '<p class="muted small">Only the group creator can regenerate or revoke the code.</p>'
         }
       </div>
     </div>`,
  );

  on(ctx.root, 'submit', '[data-form="myname"]', async (e) => {
    e.preventDefault();
    try {
      await api.updateMyMemberName(store.sessionToken, groupId, new FormData(e.target).get('displayName'));
      toast('Name updated');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
    }
  });

  on(ctx.root, 'submit', '[data-form="settings"]', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api.updateGroup(
        store.sessionToken,
        groupId,
        { name: fd.get('name'), currency: fd.get('currency') },
        g.version,
      );
      toast('Settings saved');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
      if (err instanceof ApiError && err.code === 'version_conflict') rerender();
    }
  });

  on(ctx.root, 'click', '[data-action="regen"]', async () => {
    try {
      await api.regenerateInvite(store.sessionToken, groupId);
      toast('New code generated');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
    }
  });

  on(ctx.root, 'click', '[data-action="revoke"]', async () => {
    if (!confirm('Revoke the current invite code?')) return;
    try {
      await api.revokeInvite(store.sessionToken, groupId);
      toast('Invite revoked');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
    }
  });
}
