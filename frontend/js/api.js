// ============================================================================
// BACKEND BOUNDARY
// ----------------------------------------------------------------------------
// Every call the frontend makes to "the server" lives in this file and nowhere
// else. Today it is backed by an in-browser mock (localStorage + simulated
// latency + optimistic-concurrency versioning). To go live, replace the bodies
// of the exported `api.*` functions with real `fetch` calls — the signatures and
// return shapes are the contract the UI depends on.
//
// Conventions:
//   * Money is always integer minor units.
//   * Mutations bump a record `version`; passing a stale version throws
//     ApiError('version_conflict').
//   * Auth is a `sessionToken` string, persisted by the app layer.
// ============================================================================

import { computeAllocation } from './lib/split.js';
import { computeBalances } from './lib/balances.js';
import { minimizedTransfers, relationshipPreservingTransfers } from './lib/settle.js';

const STORAGE_KEY = 'viloq.mock.db.v1';
const LATENCY_MS = 140;
const MAGIC_LINK_TTL_MIN = 15;

export class ApiError extends Error {
  constructor(code, message, extra = {}) {
    super(message || code);
    this.code = code;
    Object.assign(this, extra);
  }
}

// ---------------------------------------------------------------------------
// Mock persistence
// ---------------------------------------------------------------------------

let seededFresh = false;

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* fall through to seed */
  }
  seededFresh = true;
  return seed();
}

function persist(next) {
  db = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

const nowISO = () => new Date().toISOString();
const clone = (v) => (typeof structuredClone === 'function' ? structuredClone(v) : JSON.parse(JSON.stringify(v)));
const delay = (ms = LATENCY_MS) => new Promise((r) => setTimeout(r, ms + Math.random() * 60));

function randomId(prefix) {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return `${prefix}_${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}

function highEntropyToken(bytes = 24) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
}

function inviteCode() {
  const alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
  const buf = new Uint8Array(9);
  crypto.getRandomValues(buf);
  const raw = Array.from(buf, (b) => alphabet[b % alphabet.length]).join('');
  return `${raw.slice(0, 3)}-${raw.slice(3, 6)}-${raw.slice(6, 9)}`;
}

// ---------------------------------------------------------------------------
// Seed data — a populated demo group so every feature is usable immediately.
// ---------------------------------------------------------------------------

function seed() {
  const fresh = {
    users: {},
    sessions: {},
    magicLinks: {},
    groups: {},
    members: {},
    expenses: {},
    payments: {},
    demoGroupId: null,
    demoActors: [],
    seededAt: nowISO(),
  };

  const mkUser = (name, email) => {
    const id = randomId('usr');
    fresh.users[id] = { id, email, displayName: name, emailVerified: true, createdAt: nowISO(), updatedAt: nowISO() };
    return fresh.users[id];
  };
  const mkSession = (userId, token) => {
    fresh.sessions[token] = { userId, createdAt: nowISO() };
    return token;
  };
  const mkMember = (groupId, userId, displayName) => {
    const id = randomId('mbr');
    fresh.members[id] = { id, groupId, userId, displayName, createdAt: nowISO(), updatedAt: nowISO() };
    return fresh.members[id];
  };

  const alice = mkUser('Alice Kim', 'alice@example.com');
  const bob = mkUser('Bob Ortiz', 'bob@example.com');
  const carol = mkUser('Carol Nguyen', 'carol@example.com');
  mkSession(alice.id, 'demo-session-alice');
  mkSession(bob.id, 'demo-session-bob');
  mkSession(carol.id, 'demo-session-carol');

  const groupId = randomId('grp');
  fresh.groups[groupId] = {
    id: groupId,
    name: 'Lisbon Trip',
    currency: 'EUR',
    createdByUserId: alice.id,
    inviteCode: inviteCode(),
    inviteToken: highEntropyToken(),
    inviteRevoked: false,
    createdAt: nowISO(),
    updatedAt: nowISO(),
    version: 1,
  };
  fresh.demoGroupId = groupId;

  const mA = mkMember(groupId, alice.id, 'Alice');
  const mB = mkMember(groupId, bob.id, 'Bob');
  const mC = mkMember(groupId, carol.id, 'Carol');

  fresh.demoActors = [
    { label: 'Alice', sessionToken: 'demo-session-alice', userId: alice.id },
    { label: 'Bob', sessionToken: 'demo-session-bob', userId: bob.id },
    { label: 'Carol', sessionToken: 'demo-session-carol', userId: carol.id },
  ];

  const seedExpense = (input) => {
    const id = randomId('exp');
    const { shares } = computeAllocation({
      splitType: input.splitType,
      amountMinor: input.amountMinor,
      participants: input.participants,
    });
    fresh.expenses[id] = {
      id,
      groupId,
      description: input.description,
      note: input.note || '',
      amountMinor: input.amountMinor,
      date: input.date,
      payerMemberId: input.payerMemberId,
      splitType: input.splitType,
      splitInputs: input.participants,
      shares,
      createdByUserId: input.createdByUserId,
      createdAt: input.date + 'T12:00:00.000Z',
      updatedAt: input.date + 'T12:00:00.000Z',
      version: 1,
    };
  };

  seedExpense({
    description: 'Airbnb (3 nights)',
    amountMinor: 60000,
    date: '2026-09-01',
    payerMemberId: mA.id,
    createdByUserId: alice.id,
    splitType: 'equal',
    participants: [{ memberId: mA.id }, { memberId: mB.id }, { memberId: mC.id }],
  });
  seedExpense({
    description: 'Groceries',
    amountMinor: 8730,
    date: '2026-09-02',
    payerMemberId: mB.id,
    createdByUserId: bob.id,
    splitType: 'equal',
    participants: [{ memberId: mA.id }, { memberId: mB.id }, { memberId: mC.id }],
  });
  seedExpense({
    description: 'Rental car',
    amountMinor: 21000,
    note: 'Alice drove most days',
    date: '2026-09-03',
    payerMemberId: mC.id,
    createdByUserId: carol.id,
    splitType: 'share',
    participants: [
      { memberId: mA.id, raw: 2 },
      { memberId: mB.id, raw: 1 },
      { memberId: mC.id, raw: 1 },
    ],
  });

  const payId = randomId('pay');
  fresh.payments[payId] = {
    id: payId,
    groupId,
    payerMemberId: mB.id,
    recipientMemberId: mA.id,
    amountMinor: 5000,
    date: '2026-09-04',
    note: 'Partial for the Airbnb',
    createdByUserId: bob.id,
    createdAt: '2026-09-04T09:00:00.000Z',
    updatedAt: '2026-09-04T09:00:00.000Z',
    version: 1,
  };

  return fresh;
}

// Load (or seed) the mock database now that all helpers above are defined.
let db = load();
if (seededFresh) persist(db);

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function requireUser(sessionToken) {
  const session = db.sessions[sessionToken];
  const user = session && db.users[session.userId];
  if (!user) throw new ApiError('unauthorized', 'Please sign in again.');
  return user;
}

function groupOr404(groupId) {
  const group = db.groups[groupId];
  if (!group) throw new ApiError('not_found', 'Group not found.');
  return group;
}

function membersOf(groupId) {
  return Object.values(db.members).filter((m) => m.groupId === groupId);
}

function myMembership(userId, groupId) {
  return Object.values(db.members).find((m) => m.groupId === groupId && m.userId === userId) || null;
}

function requireMembership(userId, groupId) {
  const m = myMembership(userId, groupId);
  if (!m) throw new ApiError('forbidden', 'You are not a member of this group.');
  return m;
}

function expensesOf(groupId) {
  return Object.values(db.expenses).filter((e) => e.groupId === groupId);
}

function paymentsOf(groupId) {
  return Object.values(db.payments).filter((p) => p.groupId === groupId);
}

function checkVersion(record, expected) {
  if (expected != null && record.version !== expected) {
    throw new ApiError('version_conflict', 'This record was changed by someone else. Reloading the latest version.', {
      current: clone(record),
    });
  }
}

function resolveExpensePayload(groupId, payload) {
  const group = groupOr404(groupId);
  const memberIds = new Set(membersOf(groupId).map((m) => m.id));
  const description = String(payload.description || '').trim();
  if (!description) throw new ApiError('validation', 'Description is required.');
  if (!payload.date) throw new ApiError('validation', 'Date is required.');
  if (!(payload.amountMinor > 0)) throw new ApiError('validation', 'Amount must be greater than zero.');
  if (!memberIds.has(payload.payerMemberId)) throw new ApiError('validation', 'Payer must be a group member.');

  const participants = (payload.participants || []).filter((p) => memberIds.has(p.memberId));
  if (participants.length === 0) throw new ApiError('validation', 'Select at least one participant.');

  const { ok, shares, error } = computeAllocation({
    splitType: payload.splitType,
    amountMinor: payload.amountMinor,
    participants,
  });
  if (!ok) throw new ApiError('validation', error || 'Split does not reconcile to the total.');

  return {
    groupId,
    currency: group.currency,
    description,
    note: String(payload.note || '').trim(),
    amountMinor: payload.amountMinor,
    date: payload.date,
    payerMemberId: payload.payerMemberId,
    splitType: payload.splitType,
    splitInputs: participants,
    shares,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const api = {
  // ---- Auth --------------------------------------------------------------

  async requestMagicLink(email) {
    await delay();
    const clean = String(email || '').trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(clean)) {
      throw new ApiError('validation', 'Enter a valid email address.');
    }
    const token = highEntropyToken(18);
    db.magicLinks[token] = {
      email: clean,
      expiresAt: Date.now() + MAGIC_LINK_TTL_MIN * 60_000,
      used: false,
    };
    persist(db);
    // In production the link is emailed. Here we hand it back so the UI can show it.
    return {
      email: clean,
      token,
      magicLinkPath: `#/auth/verify?token=${token}`,
      expiresInMinutes: MAGIC_LINK_TTL_MIN,
    };
  },

  async verifyMagicLink(token) {
    await delay();
    const link = db.magicLinks[token];
    if (!link) throw new ApiError('invalid_token', 'This link is not valid. Request a new one.');
    if (link.used) throw new ApiError('invalid_token', 'This link was already used. Request a new one.');
    if (Date.now() > link.expiresAt) throw new ApiError('expired_token', 'This link expired. Request a new one.');

    link.used = true;
    let user = Object.values(db.users).find((u) => u.email === link.email);
    if (!user) {
      const id = randomId('usr');
      user = {
        id,
        email: link.email,
        displayName: '',
        emailVerified: true,
        createdAt: nowISO(),
        updatedAt: nowISO(),
      };
      db.users[id] = user;
    } else {
      user.emailVerified = true;
    }

    const sessionToken = highEntropyToken(20);
    db.sessions[sessionToken] = { userId: user.id, createdAt: nowISO() };

    // First-time users land in the populated demo group.
    let joinedDemoGroupId = null;
    if (db.demoGroupId && db.groups[db.demoGroupId] && !myMembership(user.id, db.demoGroupId)) {
      const id = randomId('mbr');
      db.members[id] = {
        id,
        groupId: db.demoGroupId,
        userId: user.id,
        displayName: user.displayName || link.email.split('@')[0],
        createdAt: nowISO(),
        updatedAt: nowISO(),
      };
      joinedDemoGroupId = db.demoGroupId;
    }

    persist(db);
    return { sessionToken, user: clone(user), needsDisplayName: !user.displayName, joinedDemoGroupId };
  },

  async getCurrentUser(sessionToken) {
    await delay(60);
    const session = db.sessions[sessionToken];
    const user = session && db.users[session.userId];
    return user ? clone(user) : null;
  },

  async updateDisplayName(sessionToken, displayName) {
    await delay();
    const user = requireUser(sessionToken);
    const name = String(displayName || '').trim();
    if (!name) throw new ApiError('validation', 'Display name cannot be empty.');
    db.users[user.id].displayName = name;
    db.users[user.id].updatedAt = nowISO();
    persist(db);
    return clone(db.users[user.id]);
  },

  async signOut(sessionToken) {
    await delay(60);
    delete db.sessions[sessionToken];
    persist(db);
    return { ok: true };
  },

  // ---- Groups ----------------------------------------------------------

  async listGroups(sessionToken) {
    await delay();
    const user = requireUser(sessionToken);
    return Object.values(db.members)
      .filter((m) => m.userId === user.id)
      .map((m) => {
        const group = db.groups[m.groupId];
        if (!group) return null;
        const expenses = expensesOf(group.id);
        const payments = paymentsOf(group.id);
        const balances = computeBalances(membersOf(group.id), expenses, payments);
        const myNet = balances.find((b) => b.memberId === m.id)?.netMinor || 0;
        return {
          id: group.id,
          name: group.name,
          currency: group.currency,
          memberCount: membersOf(group.id).length,
          expenseCount: expenses.length,
          myMemberId: m.id,
          myDisplayName: m.displayName,
          myNetMinor: myNet,
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.name.localeCompare(b.name));
  },

  async createGroup(sessionToken, { name, currency, displayName }) {
    await delay();
    const user = requireUser(sessionToken);
    const groupName = String(name || '').trim();
    const memberName = String(displayName || '').trim();
    if (!groupName) throw new ApiError('validation', 'Group name is required.');
    if (!currency) throw new ApiError('validation', 'Pick a currency.');
    if (!memberName) throw new ApiError('validation', 'Your display name for this group is required.');

    const id = randomId('grp');
    db.groups[id] = {
      id,
      name: groupName,
      currency,
      createdByUserId: user.id,
      inviteCode: inviteCode(),
      inviteToken: highEntropyToken(),
      inviteRevoked: false,
      createdAt: nowISO(),
      updatedAt: nowISO(),
      version: 1,
    };
    const memberId = randomId('mbr');
    db.members[memberId] = {
      id: memberId,
      groupId: id,
      userId: user.id,
      displayName: memberName,
      createdAt: nowISO(),
      updatedAt: nowISO(),
    };
    persist(db);
    return clone(db.groups[id]);
  },

  async getGroupSnapshot(sessionToken, groupId) {
    await delay();
    const user = requireUser(sessionToken);
    const group = groupOr404(groupId);
    const me = requireMembership(user.id, groupId);

    const members = membersOf(groupId)
      .map((m) => ({
        id: m.id,
        displayName: m.displayName,
        userId: m.userId,
        isMe: m.userId === user.id,
        createdAt: m.createdAt,
      }))
      .sort((a, b) => a.displayName.localeCompare(b.displayName));

    const expenses = expensesOf(groupId)
      .map((e) => clone(e))
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.createdAt.localeCompare(a.createdAt)));

    const payments = paymentsOf(groupId)
      .map((p) => ({ ...clone(p), canManage: p.createdByUserId === user.id }))
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.createdAt.localeCompare(a.createdAt)));

    const balances = computeBalances(
      membersOf(groupId),
      expensesOf(groupId),
      paymentsOf(groupId),
    );

    return {
      group: {
        id: group.id,
        name: group.name,
        currency: group.currency,
        version: group.version,
        createdByUserId: group.createdByUserId,
        isCreator: group.createdByUserId === user.id,
        currencyLocked: expensesOf(groupId).length > 0,
        inviteCode: group.inviteCode,
        inviteRevoked: group.inviteRevoked,
        invitePath: `#/join?code=${encodeURIComponent(group.inviteCode)}`,
      },
      me: { memberId: me.id, displayName: me.displayName, userId: user.id },
      members,
      expenses,
      payments,
      balances,
    };
  },

  async updateGroup(sessionToken, groupId, patch, expectedVersion) {
    await delay();
    const user = requireUser(sessionToken);
    const group = groupOr404(groupId);
    requireMembership(user.id, groupId);
    checkVersion(group, expectedVersion);
    if (group.createdByUserId !== user.id) {
      throw new ApiError('forbidden', 'Only the group creator can change group settings.');
    }
    if (patch.name != null) {
      const name = String(patch.name).trim();
      if (!name) throw new ApiError('validation', 'Group name cannot be empty.');
      group.name = name;
    }
    if (patch.currency != null && patch.currency !== group.currency) {
      if (expensesOf(groupId).length > 0) {
        throw new ApiError('currency_locked', 'Currency is locked once the group has expenses.');
      }
      group.currency = patch.currency;
    }
    group.updatedAt = nowISO();
    group.version += 1;
    persist(db);
    return clone(group);
  },

  // ---- Members / display name ----------------------------------------

  async updateMyMemberName(sessionToken, groupId, displayName) {
    await delay();
    const user = requireUser(sessionToken);
    const me = requireMembership(user.id, groupId);
    const name = String(displayName || '').trim();
    if (!name) throw new ApiError('validation', 'Display name cannot be empty.');
    db.members[me.id].displayName = name;
    db.members[me.id].updatedAt = nowISO();
    persist(db);
    return clone(db.members[me.id]);
  },

  // ---- Invites -------------------------------------------------------

  async getInviteInfo(code) {
    await delay();
    const group = Object.values(db.groups).find((g) => g.inviteCode === code);
    if (!group) throw new ApiError('not_found', 'This invite code is not valid.');
    return {
      groupId: group.id,
      groupName: group.name,
      currency: group.currency,
      revoked: group.inviteRevoked,
      memberCount: membersOf(group.id).length,
    };
  },

  async joinGroup(sessionToken, code, displayName) {
    await delay();
    const user = requireUser(sessionToken);
    const group = Object.values(db.groups).find((g) => g.inviteCode === code);
    if (!group) throw new ApiError('not_found', 'This invite code is not valid.');
    if (group.inviteRevoked) throw new ApiError('invite_revoked', 'This invite has been revoked. Ask for a new one.');

    let member = myMembership(user.id, group.id);
    if (!member) {
      const name = String(displayName || '').trim() || user.displayName || user.email.split('@')[0];
      const id = randomId('mbr');
      member = {
        id,
        groupId: group.id,
        userId: user.id,
        displayName: name,
        createdAt: nowISO(),
        updatedAt: nowISO(),
      };
      db.members[id] = member;
      persist(db);
    }
    return { group: clone(group), member: clone(member) };
  },

  async regenerateInvite(sessionToken, groupId) {
    await delay();
    const user = requireUser(sessionToken);
    const group = groupOr404(groupId);
    requireMembership(user.id, groupId);
    group.inviteCode = inviteCode();
    group.inviteToken = highEntropyToken();
    group.inviteRevoked = false;
    group.updatedAt = nowISO();
    persist(db);
    return { inviteCode: group.inviteCode, invitePath: `#/join?code=${encodeURIComponent(group.inviteCode)}` };
  },

  async revokeInvite(sessionToken, groupId) {
    await delay();
    const user = requireUser(sessionToken);
    const group = groupOr404(groupId);
    requireMembership(user.id, groupId);
    group.inviteRevoked = true;
    group.updatedAt = nowISO();
    persist(db);
    return { ok: true };
  },

  // ---- Expenses ----------------------------------------------------

  async createExpense(sessionToken, groupId, payload) {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const resolved = resolveExpensePayload(groupId, payload);
    const id = randomId('exp');
    db.expenses[id] = {
      id,
      ...resolved,
      createdByUserId: user.id,
      createdAt: nowISO(),
      updatedAt: nowISO(),
      version: 1,
    };
    persist(db);
    return clone(db.expenses[id]);
  },

  async updateExpense(sessionToken, groupId, expenseId, payload, expectedVersion) {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const expense = db.expenses[expenseId];
    if (!expense || expense.groupId !== groupId) throw new ApiError('not_found', 'Expense not found.');
    checkVersion(expense, expectedVersion);
    const resolved = resolveExpensePayload(groupId, payload);
    Object.assign(expense, resolved, { updatedAt: nowISO(), version: expense.version + 1 });
    persist(db);
    return clone(expense);
  },

  async deleteExpense(sessionToken, groupId, expenseId, expectedVersion) {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const expense = db.expenses[expenseId];
    if (!expense || expense.groupId !== groupId) throw new ApiError('not_found', 'Expense not found.');
    checkVersion(expense, expectedVersion);
    delete db.expenses[expenseId];
    persist(db);
    return { ok: true };
  },

  // ---- Payments --------------------------------------------------

  async createPayment(sessionToken, groupId, payload) {
    await delay();
    const user = requireUser(sessionToken);
    const me = requireMembership(user.id, groupId);
    // The payer is always the authenticated user's membership.
    const payerMemberId = me.id;
    const memberIds = new Set(membersOf(groupId).map((m) => m.id));
    if (!memberIds.has(payload.recipientMemberId)) throw new ApiError('validation', 'Pick a recipient.');
    if (payload.recipientMemberId === payerMemberId) throw new ApiError('validation', 'Payer and recipient must differ.');
    if (!(payload.amountMinor > 0)) throw new ApiError('validation', 'Amount must be greater than zero.');
    if (!payload.date) throw new ApiError('validation', 'Date is required.');

    const id = randomId('pay');
    db.payments[id] = {
      id,
      groupId,
      payerMemberId,
      recipientMemberId: payload.recipientMemberId,
      amountMinor: payload.amountMinor,
      date: payload.date,
      note: String(payload.note || '').trim(),
      createdByUserId: user.id,
      createdAt: nowISO(),
      updatedAt: nowISO(),
      version: 1,
    };
    persist(db);
    return clone(db.payments[id]);
  },

  async updatePayment(sessionToken, groupId, paymentId, payload, expectedVersion) {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const payment = db.payments[paymentId];
    if (!payment || payment.groupId !== groupId) throw new ApiError('not_found', 'Payment not found.');
    if (payment.createdByUserId !== user.id) {
      throw new ApiError('forbidden', 'Only the person who recorded a payment can change it.');
    }
    checkVersion(payment, expectedVersion);
    const memberIds = new Set(membersOf(groupId).map((m) => m.id));
    if (!memberIds.has(payload.recipientMemberId)) throw new ApiError('validation', 'Pick a recipient.');
    if (payload.recipientMemberId === payment.payerMemberId) {
      throw new ApiError('validation', 'Payer and recipient must differ.');
    }
    if (!(payload.amountMinor > 0)) throw new ApiError('validation', 'Amount must be greater than zero.');
    Object.assign(payment, {
      recipientMemberId: payload.recipientMemberId,
      amountMinor: payload.amountMinor,
      date: payload.date,
      note: String(payload.note || '').trim(),
      updatedAt: nowISO(),
      version: payment.version + 1,
    });
    persist(db);
    return clone(payment);
  },

  async deletePayment(sessionToken, groupId, paymentId, expectedVersion) {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const payment = db.payments[paymentId];
    if (!payment || payment.groupId !== groupId) throw new ApiError('not_found', 'Payment not found.');
    if (payment.createdByUserId !== user.id) {
      throw new ApiError('forbidden', 'Only the person who recorded a payment can delete it.');
    }
    checkVersion(payment, expectedVersion);
    delete db.payments[paymentId];
    persist(db);
    return { ok: true };
  },

  // ---- Settlement suggestions ---------------------------------

  async getSettlement(sessionToken, groupId, strategy = 'minimized') {
    await delay();
    const user = requireUser(sessionToken);
    requireMembership(user.id, groupId);
    const members = membersOf(groupId);
    const expenses = expensesOf(groupId);
    const payments = paymentsOf(groupId);
    const balances = computeBalances(members, expenses, payments);

    const transfers =
      strategy === 'relationship'
        ? relationshipPreservingTransfers(members, expenses, payments)
        : minimizedTransfers(balances);

    return { strategy, transfers };
  },

  // ---- Demo-only helpers ------------------------------------

  /** Shadow identities you can act as, for exercising multi-member flows in one browser. */
  async getDemoActors(sessionToken, groupId) {
    await delay(50);
    const user = requireUser(sessionToken);
    const actors = [];
    const me = myMembership(user.id, groupId);
    if (me) actors.push({ label: `${me.displayName} (you)`, sessionToken, memberId: me.id, isReal: true });
    for (const actor of db.demoActors) {
      const m = myMembership(actor.userId, groupId);
      if (m) actors.push({ label: actor.label, sessionToken: actor.sessionToken, memberId: m.id, isReal: false });
    }
    return actors;
  },

  /** Wipe the mock database and reseed the demo group. */
  async resetDemoData() {
    await delay(50);
    persist(seed());
    return { ok: true };
  },
};

export default api;
