// Record / edit a payment. The payer is always the current member.

import api, { ApiError } from '../api.js';
import { store, navigate } from '../app.js';
import { esc, setHTML, qs, on, toast, todayISO } from '../lib/dom.js';
import { parseMoneyToMinor, minorToInput, currencyInfo } from '../lib/money.js';

export async function render(ctx) {
  const [groupId, paymentId] = ctx.params;
  const snap = await api.getGroupSnapshot(store.sessionToken, groupId);
  const currency = snap.group.currency;
  const editing = paymentId ? snap.payments.find((p) => p.id === paymentId) : null;

  if (paymentId && !editing) {
    setHTML(ctx.root, `<div class="card card--pad"><p>Payment not found.</p><a class="btn" href="#/groups/${esc(groupId)}">Back</a></div>`);
    return;
  }
  if (editing && !editing.canManage) {
    setHTML(ctx.root, `<div class="card card--pad"><p>Only the person who recorded this payment can edit it.</p><a class="btn" href="#/groups/${esc(groupId)}">Back</a></div>`);
    return;
  }

  const payerId = editing ? editing.payerMemberId : snap.me.memberId;
  const payerName = snap.members.find((m) => m.id === payerId)?.displayName || 'you';
  const others = snap.members.filter((m) => m.id !== payerId);

  const preToMember = ctx.query.to;
  const preAmount = ctx.query.amount ? minorToInput(Number(ctx.query.amount), currency) : '';

  const recipientId = editing?.recipientMemberId || preToMember || others[0]?.id || '';
  const amountText = editing ? minorToInput(editing.amountMinor, currency) : preAmount;

  setHTML(
    ctx.root,
    `<div class="page-narrow stack">
       <a class="back" href="#/groups/${esc(groupId)}">← ${esc(snap.group.name)}</a>
       <div class="card card--pad stack">
         <h1>${editing ? 'Edit payment' : 'Record a payment'}</h1>
         <p class="muted small">Recording money <strong>${esc(payerName)}</strong> actually transferred.
           ${editing ? '' : 'You can only record payments you made — switch identity in the demo bar to record as someone else.'}</p>
         <form data-form="payment" class="stack">
           <label>From
             <input value="${esc(payerName)}${editing ? '' : ' (you)'}" disabled />
           </label>
           <label>To
             <select name="recipient" required>
               ${others.map((m) => `<option value="${esc(m.id)}" ${m.id === recipientId ? 'selected' : ''}>${esc(m.displayName)}</option>`).join('')}
             </select>
           </label>
           <div class="row">
             <label class="grow">Amount (${esc(currency)})
               <input name="amount" inputmode="decimal" required value="${esc(amountText)}" placeholder="0${currencyInfo(currency).decimals ? '.00' : ''}" />
             </label>
             <label class="grow">Date
               <input type="date" name="date" required value="${esc(editing?.date || todayISO())}" />
             </label>
           </div>
           <label>Note (optional)
             <input name="note" value="${esc(editing?.note || '')}" />
           </label>
           <div class="row">
             <button class="btn btn--primary" type="submit">${editing ? 'Save changes' : 'Record payment'}</button>
             <a class="btn btn--ghost" href="#/groups/${esc(groupId)}">Cancel</a>
           </div>
         </form>
       </div>
     </div>`,
  );

  const form = qs(ctx.root, '[data-form="payment"]');
  on(form, 'submit', 'form', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const parsed = parseMoneyToMinor(fd.get('amount'), currency);
    if (!parsed.ok) {
      toast(parsed.error, 'error');
      return;
    }
    const payload = {
      recipientMemberId: fd.get('recipient'),
      amountMinor: parsed.minor,
      date: fd.get('date'),
      note: fd.get('note'),
    };
    const btn = qs(form, 'button[type="submit"]');
    btn.disabled = true;
    try {
      if (editing) {
        await api.updatePayment(store.sessionToken, groupId, editing.id, payload, editing.version);
        toast('Payment updated');
      } else {
        await api.createPayment(store.sessionToken, groupId, payload);
        toast('Payment recorded');
      }
      navigate(`/groups/${groupId}`);
    } catch (err) {
      btn.disabled = false;
      if (err instanceof ApiError && err.code === 'version_conflict') {
        toast('This payment changed elsewhere. Reloading.', 'error');
        navigate(`/groups/${groupId}/payments/${editing.id}/edit`);
        return;
      }
      toast(err instanceof ApiError ? err.message : 'Could not save payment', 'error');
    }
  });
}
