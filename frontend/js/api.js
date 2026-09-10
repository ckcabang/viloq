// ============================================================================
// BACKEND BOUNDARY
// ----------------------------------------------------------------------------
// Every call the frontend makes to the server lives in this file and nowhere
// else. It speaks the HTTP contract in `../../openapi.yaml` — one function per
// `operationId` — and its signatures and return shapes are what the views
// depend on.
//
// Conventions:
//   * Money is always integer minor units.
//   * Mutating an existing record sends `If-Match: "<version>"`; a stale value
//     comes back as ApiError('version_conflict') carrying `current`.
//   * Auth is an opaque bearer `sessionToken`, persisted by the app layer.
// ============================================================================

/**
 * Where the API lives. `index.html` sets `window.VILOQ_API_BASE`; the default
 * assumes the backend also serves this page, so the path is same-origin.
 */
const API_BASE = String(globalThis.VILOQ_API_BASE || '/api/v1').replace(/\/+$/, '');

export class ApiError extends Error {
  constructor(code, message, extra = {}) {
    super(message || code);
    this.code = code;
    Object.assign(this, extra);
  }
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/** Fallback `code` for an error body the server could not shape itself. */
const STATUS_CODES = {
  400: 'validation',
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  409: 'version_conflict',
  412: 'precondition_required',
};

function url(path, query) {
  const target = new URL(`${API_BASE}${path}`, location.href);
  for (const [key, value] of Object.entries(query || {})) {
    if (value != null) target.searchParams.set(key, value);
  }
  return target;
}

/**
 * One request against the contract.
 *
 * @param {string} path path below the API base, already encoded.
 * @param {object} [options]
 * @param {string} [options.method] default GET.
 * @param {string} [options.token] bearer session token.
 * @param {object} [options.body] JSON request body.
 * @param {number} [options.version] record version, sent as `If-Match`.
 * @param {object} [options.query] query-string parameters.
 * @returns {Promise<any>} the parsed body, or null for a 204.
 */
async function request(path, { method = 'GET', token, body, version, query } = {}) {
  const headers = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (version != null) headers['If-Match'] = `"${version}"`;

  let response;
  try {
    response = await fetch(url(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ApiError('network', 'Could not reach the server. Check your connection.', { cause });
  }

  const payload = await readBody(response);
  if (!response.ok) throw toApiError(response.status, payload);
  return payload;
}

async function readBody(response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function toApiError(status, payload) {
  if (!payload || typeof payload.code !== 'string') {
    return new ApiError(STATUS_CODES[status] || 'internal', 'Something went wrong.');
  }
  // Anything beyond {code, message} — a conflict's `current` — rides along.
  const { code, message, ...extra } = payload;
  return new ApiError(code, message || 'Something went wrong.', extra);
}

/** What the 204 endpoints resolve to; the views expect a value, not undefined. */
const OK = { ok: true };

// ---------------------------------------------------------------------------
// Public API — one function per operationId in openapi.yaml
// ---------------------------------------------------------------------------

export const api = {
  // ---- Auth --------------------------------------------------------------

  async requestMagicLink(email) {
    return request('/auth/magic-links', {
      method: 'POST',
      body: { email: String(email ?? '').trim() },
    });
  },

  async verifyMagicLink(token) {
    return request('/auth/magic-links/verify', { method: 'POST', body: { token } });
  },

  /** The signed-in user, or null when the token is missing or no longer valid. */
  async getCurrentUser(sessionToken) {
    if (!sessionToken) return null;
    try {
      return await request('/auth/me', { token: sessionToken });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'unauthorized') return null;
      throw err;
    }
  },

  async updateDisplayName(sessionToken, displayName) {
    return request('/auth/me', {
      method: 'PATCH',
      token: sessionToken,
      body: { displayName },
    });
  },

  async signOut(sessionToken) {
    await request('/auth/logout', { method: 'POST', token: sessionToken });
    return OK;
  },

  // ---- Groups ----------------------------------------------------------

  async listGroups(sessionToken) {
    return request('/groups', { token: sessionToken });
  },

  async createGroup(sessionToken, { name, currency, displayName }) {
    return request('/groups', {
      method: 'POST',
      token: sessionToken,
      body: { name, currency, displayName },
    });
  },

  async getGroupSnapshot(sessionToken, groupId) {
    return request(`/groups/${encodeURIComponent(groupId)}`, { token: sessionToken });
  },

  async updateGroup(sessionToken, groupId, patch, expectedVersion) {
    return request(`/groups/${encodeURIComponent(groupId)}`, {
      method: 'PATCH',
      token: sessionToken,
      version: expectedVersion,
      body: patch,
    });
  },

  // ---- Members / display name ----------------------------------------

  async updateMyMemberName(sessionToken, groupId, displayName) {
    return request(`/groups/${encodeURIComponent(groupId)}/me`, {
      method: 'PATCH',
      token: sessionToken,
      body: { displayName },
    });
  },

  // ---- Invites -------------------------------------------------------

  async getInviteInfo(code) {
    return request(`/invites/${encodeURIComponent(code)}`);
  },

  async joinGroup(sessionToken, code, displayName) {
    return request(`/invites/${encodeURIComponent(code)}/join`, {
      method: 'POST',
      token: sessionToken,
      body: { displayName },
    });
  },

  async regenerateInvite(sessionToken, groupId) {
    return request(`/groups/${encodeURIComponent(groupId)}/invite`, {
      method: 'POST',
      token: sessionToken,
    });
  },

  async revokeInvite(sessionToken, groupId) {
    await request(`/groups/${encodeURIComponent(groupId)}/invite`, {
      method: 'DELETE',
      token: sessionToken,
    });
    return OK;
  },

  // ---- Expenses ----------------------------------------------------

  async createExpense(sessionToken, groupId, payload) {
    return request(`/groups/${encodeURIComponent(groupId)}/expenses`, {
      method: 'POST',
      token: sessionToken,
      body: expenseBody(payload),
    });
  },

  async updateExpense(sessionToken, groupId, expenseId, payload, expectedVersion) {
    return request(
      `/groups/${encodeURIComponent(groupId)}/expenses/${encodeURIComponent(expenseId)}`,
      {
        method: 'PUT',
        token: sessionToken,
        version: expectedVersion,
        body: expenseBody(payload),
      },
    );
  },

  async deleteExpense(sessionToken, groupId, expenseId, expectedVersion) {
    await request(
      `/groups/${encodeURIComponent(groupId)}/expenses/${encodeURIComponent(expenseId)}`,
      { method: 'DELETE', token: sessionToken, version: expectedVersion },
    );
    return OK;
  },

  // ---- Payments --------------------------------------------------
  // The payer is always the caller's own membership: the server reads it from
  // the session, so it is never sent.

  async createPayment(sessionToken, groupId, payload) {
    return request(`/groups/${encodeURIComponent(groupId)}/payments`, {
      method: 'POST',
      token: sessionToken,
      body: paymentBody(payload),
    });
  },

  async updatePayment(sessionToken, groupId, paymentId, payload, expectedVersion) {
    return request(
      `/groups/${encodeURIComponent(groupId)}/payments/${encodeURIComponent(paymentId)}`,
      {
        method: 'PUT',
        token: sessionToken,
        version: expectedVersion,
        body: paymentBody(payload),
      },
    );
  },

  async deletePayment(sessionToken, groupId, paymentId, expectedVersion) {
    await request(
      `/groups/${encodeURIComponent(groupId)}/payments/${encodeURIComponent(paymentId)}`,
      { method: 'DELETE', token: sessionToken, version: expectedVersion },
    );
    return OK;
  },

  // ---- Settlement suggestions ---------------------------------

  async getSettlement(sessionToken, groupId, strategy = 'minimized') {
    return request(`/groups/${encodeURIComponent(groupId)}/settlement`, {
      token: sessionToken,
      query: { strategy },
    });
  },
};

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------
// Form fields arrive as strings or null; the contract wants typed JSON, and an
// omitted optional note is an empty string.

function expenseBody(payload) {
  return {
    description: String(payload.description ?? '').trim(),
    note: String(payload.note ?? '').trim(),
    date: payload.date,
    amountMinor: payload.amountMinor,
    payerMemberId: payload.payerMemberId,
    splitType: payload.splitType,
    // `raw` is meaningless for an equal split and omitted there.
    participants: (payload.participants ?? []).map((p) =>
      p.raw == null ? { memberId: p.memberId } : { memberId: p.memberId, raw: p.raw },
    ),
  };
}

function paymentBody(payload) {
  return {
    recipientMemberId: payload.recipientMemberId,
    amountMinor: payload.amountMinor,
    date: payload.date,
    note: String(payload.note ?? '').trim(),
  };
}

export default api;
