// Group dashboard: balances, settlement suggestions, recent activity.

import api, { ApiError } from '../api.js';
import { store, navigate, render as rerender } from '../app.js';
import { esc, setHTML, qs, qsa, on, toast, copyToClipboard, formatDate } from '../lib/dom.js';
import { formatMoney } from '../lib/money.js';

const SPLIT_LABELS = { equal: 'Equal', percentage: 'Percentage', share: 'Shares', exact: 'Exact' };
let settlementStrategy = 'minimized';

export async function render(ctx) {
  const [groupId] = ctx.params;
  const snap = await api.getGroupSnapshot(store.sessionToken, groupId);
  const nameOf = (memberId) => snap.members.find((m) => m.id === memberId)?.displayName || '(unknown)';
  const c = snap.group.currency;

  const settlement = await api.getSettlement(store.sessionToken, groupId, settlementStrategy);

  setHTML(
    ctx.root,
    `<div class="page stack">
       <div class="page__head">
         <div>
           <a class="back" href="#/">← Groups</a>
           <h1>${esc(snap.group.name)}</h1>
           <p class="muted small">${esc(String(snap.members.length))} members · ${esc(c)}
             · you are <strong>${esc(snap.me.displayName)}</strong></p>
         </div>
         <div class="page__actions">
           <a class="btn btn--primary" href="#/groups/${esc(groupId)}/expenses/new">Add expense</a>
           <a class="btn" href="#/groups/${esc(groupId)}/payments/new">Record payment</a>
           <button class="btn btn--ghost" data-action="invite">Invite</button>
           <a class="btn btn--ghost" href="#/groups/${esc(groupId)}/admin">Members &amp; settings</a>
         </div>
       </div>

       ${demoBar(snap)}

       <div class="grid-2">
         <section class="card card--pad stack">
           <h2>Balances</h2>
           <p class="muted small">Positive means the member is owed money. Negative means they owe.</p>
           <ul class="balances">
             ${snap.balances
               .slice()
               .sort((a, b) => b.netMinor - a.netMinor)
               .map((b) => balanceRow(b, nameOf, c))
               .join('')}
           </ul>
         </section>

         <section class="card card--pad stack">
           <div class="row row--between">
             <h2>Settle up</h2>
             <div class="toggle" role="tablist">
               <button role="tab" data-strategy="minimized"
                 class="${settlementStrategy === 'minimized' ? 'is-active' : ''}">Fewest transfers</button>
               <button role="tab" data-strategy="relationship"
                 class="${settlementStrategy === 'relationship' ? 'is-active' : ''}">Keep relationships</button>
             </div>
           </div>
           <p class="muted small">Suggestions only — recording a payment is a separate, explicit step.</p>
           ${
             settlement.transfers.length === 0
               ? `<p class="empty">Everyone is settled up. 🎉</p>`
               : `<ul class="transfers">
                   ${settlement.transfers.map((t) => transferRow(t, nameOf, c, snap, groupId)).join('')}
                 </ul>`
           }
         </section>
       </div>

       <section class="card card--pad stack">
         <div class="row row--between">
           <h2>Expenses</h2>
           <a class="btn btn--sm" href="#/groups/${esc(groupId)}/expenses/new">Add</a>
         </div>
         ${
           snap.expenses.length === 0
             ? `<p class="empty">No expenses yet.</p>`
             : `<ul class="txlist">${snap.expenses.map((e) => expenseRow(e, nameOf, c, groupId)).join('')}</ul>`
         }
       </section>

       <section class="card card--pad stack">
         <div class="row row--between">
           <h2>Payments</h2>
           <a class="btn btn--sm" href="#/groups/${esc(groupId)}/payments/new">Record</a>
         </div>
         ${
           snap.payments.length === 0
             ? `<p class="empty">No payments recorded yet.</p>`
             : `<ul class="txlist">${snap.payments.map((p) => paymentRow(p, nameOf, c, groupId)).join('')}</ul>`
         }
       </section>
     </div>`,
  );

  wire(ctx.root, snap, groupId);
}

// ---------------------------------------------------------------------------
// Row renderers
// ---------------------------------------------------------------------------

function balanceRow(b, nameOf, c) {
  const cls = b.netMinor > 0 ? 'pos' : b.netMinor < 0 ? 'neg' : 'zero';
  const label =
    b.netMinor > 0 ? 'is owed' : b.netMinor < 0 ? 'owes' : 'settled';
  return `<li class="balances__row">
     <span>${esc(nameOf(b.memberId))}</span>
     <span class="amount amount--${cls}">${cls === 'zero' ? 'settled' : `${label} ${esc(formatMoney(Math.abs(b.netMinor), c))}`}</span>
   </li>`;
}

function transferRow(t, nameOf, c, snap, groupId) {
  const iAmDebtor = t.fromMemberId === snap.me.memberId;
  const recordHref = `#/groups/${groupId}/payments/new?to=${encodeURIComponent(t.toMemberId)}&amount=${t.amountMinor}`;
  return `<li class="transfers__row">
     <span><strong>${esc(nameOf(t.fromMemberId))}</strong> → ${esc(nameOf(t.toMemberId))}</span>
     <span class="row row--tight">
       <span class="amount">${esc(formatMoney(t.amountMinor, c))}</span>
       ${iAmDebtor ? `<a class="btn btn--sm" href="${recordHref}">Record</a>` : ''}
     </span>
   </li>`;
}

function expenseRow(e, nameOf, c, groupId) {
  const participants = e.shares.map((s) => nameOf(s.memberId)).join(', ');
  return `<li class="txlist__row">
     <a class="txlist__main" href="#/groups/${esc(groupId)}/expenses/${esc(e.id)}/edit">
       <div>
         <div class="txlist__title">${esc(e.description)}
           <span class="tag">${esc(SPLIT_LABELS[e.splitType] || e.splitType)}</span></div>
         <div class="muted small">${esc(formatDate(e.date))} · ${esc(nameOf(e.payerMemberId))} paid · split among ${esc(participants)}${e.note ? ` · ${esc(e.note)}` : ''}</div>
       </div>
       <div class="amount">${esc(formatMoney(e.amountMinor, c))}</div>
     </a>
     <button class="iconbtn" title="Delete expense" data-del-expense="${esc(e.id)}" data-version="${e.version}">✕</button>
   </li>`;
}

function paymentRow(p, nameOf, c, groupId) {
  return `<li class="txlist__row">
     ${
       p.canManage
         ? `<a class="txlist__main" href="#/groups/${esc(groupId)}/payments/${esc(p.id)}/edit">`
         : `<div class="txlist__main txlist__main--static">`
     }
       <div>
         <div class="txlist__title">${esc(nameOf(p.payerMemberId))} → ${esc(nameOf(p.recipientMemberId))}
           <span class="tag tag--muted">payment</span></div>
         <div class="muted small">${esc(formatDate(p.date))}${p.note ? ` · ${esc(p.note)}` : ''}${p.canManage ? '' : ' · recorded by someone else'}</div>
       </div>
       <div class="amount">${esc(formatMoney(p.amountMinor, c))}</div>
     ${p.canManage ? '</a>' : '</div>'}
     ${
       p.canManage
         ? `<button class="iconbtn" title="Delete payment" data-del-payment="${esc(p.id)}" data-version="${p.version}">✕</button>`
         : '<span class="iconbtn iconbtn--empty"></span>'
     }
   </li>`;
}

// ---------------------------------------------------------------------------
// Demo "act as" bar
// ---------------------------------------------------------------------------

function demoBar(snap) {
  return `<div class="demobar" data-demobar>
     <span class="pill pill--demo">Demo</span>
     <label>Act as
       <select data-act-as>
         <option value="">${esc(snap.me.displayName)} (you)</option>
       </select>
     </label>
     <span class="muted small">Switch identity to record payments as another member or test permissions. Mock only.</span>
   </div>`;
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

async function wire(root, snap, groupId) {
  // Settlement strategy toggle
  on(root, 'click', '[data-strategy]', (e, el) => {
    settlementStrategy = el.dataset.strategy;
    rerender();
  });

  // Invite modal
  on(root, 'click', '[data-action="invite"]', () => openInviteModal(root, groupId, snap.group));

  // Delete expense
  on(root, 'click', '[data-del-expense]', async (e, el) => {
    if (!confirm('Delete this expense? Balances update immediately.')) return;
    try {
      await api.deleteExpense(store.sessionToken, groupId, el.dataset.delExpense, Number(el.dataset.version));
      toast('Expense deleted');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not delete', 'error');
      if (err instanceof ApiError && err.code === 'version_conflict') rerender();
    }
  });

  // Delete payment
  on(root, 'click', '[data-del-payment]', async (e, el) => {
    if (!confirm('Delete this payment? Balances update immediately.')) return;
    try {
      await api.deletePayment(store.sessionToken, groupId, el.dataset.delPayment, Number(el.dataset.version));
      toast('Payment deleted');
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not delete', 'error');
      if (err instanceof ApiError && err.code === 'version_conflict') rerender();
    }
  });

  // Demo act-as
  const select = qs(root, '[data-act-as]');
  if (select) {
    const actors = await api.getDemoActors(store.realSessionToken, groupId);
    for (const actor of actors.filter((a) => !a.isReal)) {
      const opt = document.createElement('option');
      opt.value = actor.sessionToken;
      opt.textContent = actor.label;
      select.appendChild(opt);
    }
    select.value = store.actingAs ? store.sessionToken : '';
    on(select, 'change', 'select', () => {
      const token = select.value;
      const label = token ? select.options[select.selectedIndex].text : null;
      store.actAs(token, label);
      toast(token ? `Now acting as ${label}` : 'Back to your own identity');
      rerender();
    });
  }
}

function openInviteModal(root, groupId, group) {
  const inviteUrl = `${location.origin}${location.pathname}${group.invitePath}`;
  const dlg = document.createElement('div');
  dlg.className = 'modal';
  dlg.innerHTML = `
    <div class="modal__box card card--pad stack">
      <div class="row row--between">
        <h2>Invite to ${esc(group.name)}</h2>
        <button class="iconbtn" data-close>✕</button>
      </div>
      ${
        group.inviteRevoked
          ? `<p class="notice notice--warn">The current invite is revoked. Regenerate to get a working link.</p>`
          : ''
      }
      <label>Invite code
        <div class="row row--tight">
          <input readonly value="${esc(group.inviteCode)}" data-code />
          <button class="btn btn--sm" data-copy="${esc(group.inviteCode)}">Copy</button>
        </div>
      </label>
      <label>Invite link
        <div class="row row--tight">
          <input readonly value="${esc(inviteUrl)}" data-url />
          <button class="btn btn--sm" data-copy="${esc(inviteUrl)}">Copy</button>
        </div>
      </label>
      <p class="muted small">Anyone with this code can join. Treat it like a password.</p>
      <div class="row">
        <button class="btn" data-action="regen">Regenerate</button>
        <button class="btn btn--danger-ghost" data-action="revoke">Revoke</button>
      </div>
    </div>`;
  document.body.appendChild(dlg);

  const close = () => dlg.remove();
  on(dlg, 'click', '[data-close]', close);
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg) close();
  });
  on(dlg, 'click', '[data-copy]', async (e, el) => {
    const ok = await copyToClipboard(el.dataset.copy);
    toast(ok ? 'Copied' : 'Copy failed', ok ? 'ok' : 'error');
  });
  on(dlg, 'click', '[data-action="regen"]', async () => {
    try {
      await api.regenerateInvite(store.sessionToken, groupId);
      toast('New invite generated');
      close();
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
    }
  });
  on(dlg, 'click', '[data-action="revoke"]', async () => {
    if (!confirm('Revoke the current invite? Existing links stop working until you regenerate.')) return;
    try {
      await api.revokeInvite(store.sessionToken, groupId);
      toast('Invite revoked');
      close();
      rerender();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Failed', 'error');
    }
  });
}
