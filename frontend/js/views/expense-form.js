// Add / edit an expense with live split allocation and validation.

import api, { ApiError } from '../api.js';
import { store, navigate } from '../app.js';
import { esc, setHTML, qs, qsa, on, toast, todayISO } from '../lib/dom.js';
import { formatMoney, parseMoneyToMinor, minorToInput, currencyInfo } from '../lib/money.js';
import { computeAllocation } from '../lib/split.js';

const METHODS = [
  { id: 'equal', label: 'Equal', hint: 'Split evenly among everyone selected.' },
  { id: 'percentage', label: 'Percentage', hint: 'Assign a % to each person. Must total 100%.' },
  { id: 'share', label: 'Shares', hint: 'Relative weights, e.g. 2 / 1 / 1.' },
  { id: 'exact', label: 'Exact amounts', hint: 'Type each person\'s exact amount. Must total the expense.' },
];

export async function render(ctx) {
  const [groupId, expenseId] = ctx.params;
  const snap = await api.getGroupSnapshot(store.sessionToken, groupId);
  const currency = snap.group.currency;
  const editing = snap.expenses.find((e) => e.id === expenseId) || null;
  if (expenseId && !editing) {
    setHTML(ctx.root, `<div class="card card--pad"><p>Expense not found.</p><a class="btn" href="#/groups/${esc(groupId)}">Back</a></div>`);
    return;
  }

  // Local editable state
  const state = {
    description: editing?.description || '',
    note: editing?.note || '',
    date: editing?.date || todayISO(),
    amountText: editing ? minorToInput(editing.amountMinor, currency) : '',
    payerMemberId: editing?.payerMemberId || snap.me.memberId,
    splitType: editing?.splitType || 'equal',
    // raw inputs keyed by memberId; meaning depends on splitType
    selected: new Set(
      editing ? editing.splitInputs.map((p) => p.memberId) : snap.members.map((m) => m.id),
    ),
    raw: {},
  };
  if (editing) {
    for (const p of editing.splitInputs) {
      state.raw[p.memberId] =
        editing.splitType === 'exact' ? minorToInput(Number(p.raw), currency) : String(p.raw ?? '');
    }
  }

  setHTML(
    ctx.root,
    `<div class="page-narrow stack">
       <a class="back" href="#/groups/${esc(groupId)}">← ${esc(snap.group.name)}</a>
       <div class="card card--pad stack">
         <h1>${editing ? 'Edit expense' : 'Add expense'}</h1>
         <form data-form="expense" class="stack">
           <label>Description
             <input name="description" required value="${esc(state.description)}" placeholder="Dinner, taxi, tickets…" />
           </label>
           <div class="row">
             <label class="grow">Amount (${esc(currency)})
               <input name="amount" inputmode="decimal" required value="${esc(state.amountText)}" placeholder="0${currencyInfo(currency).decimals ? '.00' : ''}" />
             </label>
             <label class="grow">Date
               <input type="date" name="date" required value="${esc(state.date)}" />
             </label>
           </div>
           <label>Paid by
             <select name="payer">
               ${snap.members.map((m) => `<option value="${esc(m.id)}" ${m.id === state.payerMemberId ? 'selected' : ''}>${esc(m.displayName)}${m.isMe ? ' (you)' : ''}</option>`).join('')}
             </select>
           </label>
           <label>Note (optional)
             <input name="note" value="${esc(state.note)}" />
           </label>

           <fieldset class="fieldset">
             <legend>Split method</legend>
             <div class="chips" data-methods>
               ${METHODS.map((m) => `<button type="button" class="chip ${m.id === state.splitType ? 'is-active' : ''}" data-method="${m.id}">${esc(m.label)}</button>`).join('')}
             </div>
             <p class="muted small" data-method-hint>${esc(METHODS.find((m) => m.id === state.splitType).hint)}</p>
           </fieldset>

           <div data-participants class="stack"></div>

           <div data-alloc class="alloc"></div>

           <div class="row">
             <button class="btn btn--primary" type="submit" data-submit>${editing ? 'Save changes' : 'Add expense'}</button>
             <a class="btn btn--ghost" href="#/groups/${esc(groupId)}">Cancel</a>
           </div>
         </form>
       </div>
     </div>`,
  );

  const form = qs(ctx.root, '[data-form="expense"]');
  const participantsEl = qs(form, '[data-participants]');
  const allocEl = qs(form, '[data-alloc]');
  const hintEl = qs(form, '[data-method-hint]');

  const currentAmountMinor = () => {
    const parsed = parseMoneyToMinor(qs(form, '[name="amount"]').value, currency);
    return parsed.ok ? parsed.minor : NaN;
  };

  function participantList() {
    return snap.members.map((m) => {
      const checked = state.selected.has(m.id);
      const rawVal = state.raw[m.id] ?? '';
      let control = '';
      if (state.splitType === 'percentage') {
        control = `<input class="split-input" data-raw="${esc(m.id)}" inputmode="decimal" placeholder="%" value="${esc(rawVal)}" ${checked ? '' : 'disabled'} /><span class="split-unit">%</span>`;
      } else if (state.splitType === 'share') {
        control = `<input class="split-input" data-raw="${esc(m.id)}" inputmode="decimal" placeholder="shares" value="${esc(rawVal)}" ${checked ? '' : 'disabled'} />`;
      } else if (state.splitType === 'exact') {
        control = `<input class="split-input" data-raw="${esc(m.id)}" inputmode="decimal" placeholder="0" value="${esc(rawVal)}" ${checked ? '' : 'disabled'} />`;
      }
      return `<label class="participant">
        <input type="checkbox" data-participant="${esc(m.id)}" ${checked ? 'checked' : ''} />
        <span class="participant__name">${esc(m.displayName)}${m.isMe ? ' (you)' : ''}</span>
        <span class="participant__control">${control}</span>
      </label>`;
    }).join('');
  }

  function renderParticipants() {
    setHTML(participantsEl, `<div class="row row--between"><strong>Participants</strong>
      <button type="button" class="btn btn--sm btn--ghost" data-toggle-all>Toggle all</button></div>
      ${participantList()}`);
  }

  function currentParticipants() {
    return snap.members
      .filter((m) => state.selected.has(m.id))
      .map((m) => {
        if (state.splitType === 'equal') return { memberId: m.id };
        if (state.splitType === 'exact') {
          const parsed = parseMoneyToMinor(state.raw[m.id] ?? '', currency);
          return { memberId: m.id, raw: parsed.ok ? parsed.minor : NaN };
        }
        return { memberId: m.id, raw: Number(state.raw[m.id] ?? '') };
      });
  }

  function renderAlloc() {
    const amountMinor = currentAmountMinor();
    const submitBtn = qs(form, '[data-submit]');
    if (!Number.isFinite(amountMinor) || amountMinor <= 0) {
      setHTML(allocEl, `<p class="muted small">Enter an amount to preview the split.</p>`);
      submitBtn.disabled = true;
      return;
    }
    const participants = currentParticipants();
    const result = computeAllocation({ splitType: state.splitType, amountMinor, participants });
    const nameOf = (id) => snap.members.find((m) => m.id === id)?.displayName || '?';

    const rows = result.shares
      .map((s) => `<li><span>${esc(nameOf(s.memberId))}</span><span>${esc(formatMoney(s.amountMinor, currency))}</span></li>`)
      .join('');

    const diff = amountMinor - result.allocatedMinor;
    let banner;
    if (result.ok) {
      banner = `<p class="alloc__ok">Allocated ${esc(formatMoney(result.allocatedMinor, currency))} — reconciles exactly.</p>`;
    } else if (result.error) {
      banner = `<p class="alloc__err">${esc(result.error)}${
        diff !== 0 && state.splitType === 'exact'
          ? ` (${diff > 0 ? 'short by' : 'over by'} ${esc(formatMoney(Math.abs(diff), currency))})`
          : ''
      }</p>`;
    } else {
      banner = `<p class="alloc__err">Split incomplete.</p>`;
    }

    setHTML(allocEl, `${banner}<ul class="alloc__list">${rows}</ul>`);
    submitBtn.disabled = !result.ok;
  }

  function refresh() {
    renderParticipants();
    renderAlloc();
  }

  // --- events -------------------------------------------------------------

  on(form, 'click', '[data-method]', (e, el) => {
    state.splitType = el.dataset.method;
    qsa(form, '[data-method]').forEach((b) => b.classList.toggle('is-active', b === el));
    hintEl.textContent = METHODS.find((m) => m.id === state.splitType).hint;
    // seed sensible defaults
    if (state.splitType === 'percentage' || state.splitType === 'share') {
      for (const id of state.selected) if (!state.raw[id]) state.raw[id] = state.splitType === 'share' ? '1' : '';
    }
    refresh();
  });

  on(form, 'change', '[data-participant]', (e, el) => {
    const id = el.dataset.participant;
    if (el.checked) state.selected.add(id);
    else state.selected.delete(id);
    refresh();
  });

  on(form, 'click', '[data-toggle-all]', () => {
    const all = snap.members.every((m) => state.selected.has(m.id));
    state.selected = all ? new Set() : new Set(snap.members.map((m) => m.id));
    refresh();
  });

  on(form, 'input', '[data-raw]', (e, el) => {
    state.raw[el.dataset.raw] = el.value;
    renderAlloc();
  });

  on(form, 'input', '[name="amount"]', () => renderAlloc());
  on(form, 'change', '[name="payer"]', (e, el) => {
    state.payerMemberId = el.value;
  });

  on(form, 'submit', 'form', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const parsedAmount = parseMoneyToMinor(fd.get('amount'), currency);
    if (!parsedAmount.ok) {
      toast(parsedAmount.error, 'error');
      return;
    }
    const payload = {
      description: fd.get('description'),
      note: fd.get('note'),
      date: fd.get('date'),
      amountMinor: parsedAmount.minor,
      payerMemberId: fd.get('payer'),
      splitType: state.splitType,
      participants: currentParticipants(),
    };
    const btn = qs(form, '[data-submit]');
    btn.disabled = true;
    try {
      if (editing) {
        await api.updateExpense(store.sessionToken, groupId, editing.id, payload, editing.version);
        toast('Expense updated');
      } else {
        await api.createExpense(store.sessionToken, groupId, payload);
        toast('Expense added');
      }
      navigate(`/groups/${groupId}`);
    } catch (err) {
      btn.disabled = false;
      if (err instanceof ApiError && err.code === 'version_conflict') {
        toast('This expense changed elsewhere. Reloading.', 'error');
        navigate(`/groups/${groupId}/expenses/${editing.id}/edit`);
        return;
      }
      toast(err instanceof ApiError ? err.message : 'Could not save expense', 'error');
    }
  });

  refresh();
}
