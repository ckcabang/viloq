# Expense Splitting Tool — V1 Product Specification

## 1. Product goal

Build a shared expense-management tool for friends, roommates, couples, trips, events, teams, and other groups who need to track shared expenses and determine who owes whom.

V1 prioritizes reliable cloud synchronization and a low-friction passwordless experience.

---

## 2. Core product model

The product is organized around four concepts:

- **User** — an authenticated person with an email address.
- **Group** — a continuously active shared space with one currency.
- **Group member** — a user participating in a group, with a group-specific display name.
- **Expense / Payment** — financial transactions that determine current balances.

There are no accounting periods and no group-level settle/reset operation in V1.

---

## 3. Authentication and identity

### Authentication

V1 uses **passwordless email magic links**.

- Users authenticate with an email address.
- No passwords are required.
- Email verification is required before the user can create or join a group.
- A magic link expires after 15 minutes.
- Users can request a new magic link.
- Signing out does not remove group memberships.

### Identity

A `User` is the canonical identity in the system.

The user's display name is editable and is separate from the email address.

Email addresses are private and are not displayed to other participants by default.

### Joining groups

Groups are shared using an **invite link or invite code**.

Joining flow:

1. User opens an invite.
2. If unauthenticated, the user enters their email.
3. User authenticates via magic link.
4. User selects an existing participant identity or creates a new group membership, depending on the invite flow.
5. User joins the group.

Because users are authenticated, the simpler recommended implementation is to automatically associate the authenticated user with the invited group and ask for a display name only when one has not already been configured.

A user may belong to multiple groups.

---

## 4. Groups

Each group contains:

- Group name
- Currency
- Members
- Expenses
- Payments
- Current balances

### Group lifecycle

Groups are **continuously active**.

V1 does not support:

- settlement/reset actions
- accounting periods
- automatic archiving
- multi-currency groups

Each group has exactly one currency.

---

## 5. Expense model

An expense contains:

- Description
- Optional note
- Date
- Amount
- One payer
- One or more participating members
- Split method and allocation

### Supported split methods

V1 supports:

1. **Equal split** — divide equally among selected participants.
2. **Percentage split** — specify percentages totaling 100%.
3. **Share split** — specify relative shares such as 1 / 2 / 1.
4. **Exact amount split** — specify an exact amount for each participant.

An expense may include **any subset of the group members**.

Each expense has **exactly one payer**.

### Money representation

Store monetary values as **integer minor units** (for example, cents) rather than floating-point numbers.

The split allocation must reconcile exactly to the expense total after applying the product's deterministic rounding rules.

---

## 6. Expense editing and deletion

V1 keeps transaction editing simple:

- Group members can edit expenses.
- Group members can delete expenses.
- Changes immediately affect balances.
- There is no immutable accounting ledger in V1.

For operational safety, records should still retain standard identifiers and timestamps such as `created_at` and `updated_at`.

A full audit history is out of scope.

---

## 7. Payments

A payment represents money actually transferred between two members.

A payment contains:

- Payer
- Recipient
- Amount
- Date
- Optional note

### Payment permissions

Only the user who made the payment can record that payment in V1.

The payer is therefore automatically the currently authenticated user when creating a payment.

Payments can be edited or deleted by their creator, with balances recalculated accordingly.

There is no separate settlement/reset mechanism.

---

## 8. Balance calculation

Balances are recalculated **live** whenever expenses or payments change.

For each member, the system derives a net balance from all current transactions.

Interpretation:

- **Positive balance** = the member is owed money.
- **Negative balance** = the member owes money.
- **Zero** = settled relative to the current transaction history.

The UI should make this sign convention explicit.

Balances should be calculated from persisted transactions rather than manually maintained as an independently editable value.

---

## 9. Settlement suggestions

The product provides suggested transfers based on current balances.

V1 supports two views:

### Minimized transfers

Reduce the number of transfers needed to bring all balances to zero.

Example:

```text
Alice   +$60
Bob     -$40
Carol   -$20
```

Suggested transfers:

```text
Bob   → Alice   $40
Carol → Alice   $20
```

### Relationship-preserving transfers

Prefer transfers that preserve the underlying debtor/creditor relationships where practical instead of minimizing transfer count alone.

The user can choose between the two views.

### Important distinction

Settlement suggestions are **recommendations only**.

They do not automatically create payment records.

A user explicitly records a payment after money has actually been transferred.

---

## 10. Permissions and collaboration

V1 deliberately uses minimal permissions.

Within a group, members can:

- Add expenses
- Edit expenses
- Delete expenses
- View all group transactions
- View balances and settlement suggestions
- Record payments they personally made

There are no owner/admin/member roles in V1.

### Membership management

V1 may use a single group creator as the administrative authority for basic group operations such as changing the group name or currency, while keeping transaction permissions shared. Advanced role management is out of scope.

Recommended default: once a group has expenses, its currency should become immutable to avoid recalculating historical money values.

---

## 11. Synchronization and backend behavior

The application is **cloud-backed with a shared source of truth**.

V1 prioritizes reliable shared synchronization over offline-first behavior.

Expected behavior:

- Multiple users can access the same group concurrently.
- Changes propagate to other connected clients.
- The server is authoritative.
- Clients reconcile local state after mutations and refresh as needed.
- Transaction writes should be atomic.

### Concurrency

Use optimistic concurrency or server-side versioning for records that can be edited concurrently.

The system should avoid silent data loss from two users editing the same transaction.

Sophisticated offline conflict resolution is out of scope for V1.

---

## 12. Invite security

Invite links/codes should use high-entropy, non-guessable tokens.

Treat an invite token as a bearer credential:

- Possession of the token grants access to the group-join flow.
- Do not encode sensitive group data directly in the token.
- Support invite revocation/regeneration as a basic safety mechanism.

V1 does not require granular invite permissions.

---

## 13. Core user flows

### Create a group

1. User signs in via email magic link.
2. User creates a group.
3. User enters the group name.
4. User selects the currency.
5. User sets their display name for the group.
6. App creates the group and membership.
7. App provides an invite link/code.

### Join a group

1. User opens an invite link/code.
2. User authenticates via magic link if necessary.
3. User is associated with the invited group.
4. User selects or confirms their group display name.
5. Group becomes available in their account.

### Add an expense

1. Select a group.
2. Enter description, amount, and date.
3. Select payer.
4. Select participating members.
5. Select the split method.
6. Enter split details.
7. Validate that the allocation equals the total.
8. Save.
9. Recalculate balances.

### Record a payment

1. Open the payment flow.
2. Current authenticated user is the payer.
3. Select recipient.
4. Enter amount and date.
5. Save.
6. Recalculate balances.

### Review balances

The group dashboard should show:

- Current balance for every member
- Who owes whom
- Settlement suggestions
- Recent expenses
- Recent payments

---

## 14. Recommended domain model

```text
User
  id
  email
  display_name
  created_at
  updated_at

Group
  id
  name
  currency
  created_by_user_id
  created_at
  updated_at

GroupMember
  id
  group_id
  user_id
  display_name
  created_at
  updated_at

Expense
  id
  group_id
  description
  note
  amount_minor
  date
  payer_member_id
  split_type
  created_at
  updated_at

ExpenseShare
  id
  expense_id
  member_id
  value

Payment
  id
  group_id
  payer_member_id
  recipient_member_id
  amount_minor
  date
  note
  created_at
  updated_at
```

### Modeling recommendation

Persist the **resolved expense allocation** in `ExpenseShare` rather than relying solely on the original percentage/share inputs. This makes balance calculation deterministic and preserves the exact financial allocation that was recorded.

If the UI needs to reconstruct the original split mode, persist `split_type` and the original values as well.

---

## 15. Balance calculation model

At a conceptual level, for each member:

```text
net_balance
  = amounts_paid_for_others
  - member's own allocated expense share
  + payments_received
  - payments_made
```

The implementation should derive this from transactions and allocations rather than maintain mutable running balances as the primary source of truth.

All calculations must use integer minor units.

---

## 16. Explicit V1 exclusions

Do not build these into V1:

- Anonymous users
- Password authentication
- Social login
- Multiple currencies within a group
- Multi-payer expenses
- Item-level receipt splitting
- Automatic tax/tip/discount allocation
- Recurring expenses
- Expense categories/tags
- Receipt/file uploads
- Budgets
- Advanced reports/analytics
- Push notifications
- Email notifications beyond authentication
- Offline-first synchronization
- Complex conflict resolution
- Admin/member role systems
- Accounting periods
- Settle/reset group actions
- Immutable financial audit ledger
- Automatic settlement payments

---

## 17. V1 acceptance criteria

A V1 release is functionally complete when:

1. A user can authenticate using an email magic link.
2. A verified user can create a group with one currency.
3. A verified user can invite another person using a link/code.
4. The invited person can authenticate and join the group.
5. Group members can add, edit, and delete expenses.
6. Expenses support equal, percentage, share, and exact-amount splits.
7. Expenses can include any subset of group members.
8. Each expense has exactly one payer.
9. A user can record a payment they personally made.
10. Balances update immediately after transaction changes.
11. The system displays settlement suggestions using both supported strategies.
12. Multiple connected clients see shared group changes reliably.
13. Group data persists across sessions and devices.
14. All monetary calculations are deterministic and avoid floating-point arithmetic.
15. Invite tokens are non-guessable and can be revoked/regenerated.

---

## 18. Product definition in one sentence

**A passwordless, cloud-synchronized expense sharing app where authenticated users create continuously active groups, record flexible single-payer expenses and payments, maintain live balances, and choose between minimized or relationship-preserving settlement suggestions.**
