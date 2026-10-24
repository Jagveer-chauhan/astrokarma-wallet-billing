# Entity Relationship (ER) Diagram & Schema Specification

This document details the database architecture, entity relationships, constraints, and data dictionary for the **Astrokarma Wallet-Based Consultation Billing System**.

---

## 1. Visual Entity Relationship Diagrams

### A. Project Structure & App Architecture ER Diagram
This diagram illustrates the entity-relationship model and service interactions organized directly according to the project's Django apps (`apps/users/`, `apps/wallets/`, `apps/consultations/`, and `apps/common/`):

![Project Structure ER Diagram](er_diagram_project_structure.svg)

*File reference: [`er_diagram_project_structure.svg`](er_diagram_project_structure.svg)*

---

### B. Relational Database Schema ER Diagram
This diagram illustrates the physical PostgreSQL database tables, column types, primary/foreign keys, constraints, and cardinalities:

![Database Relational ER Diagram](er_diagram.svg)

*File reference: [`er_diagram.svg`](er_diagram.svg)*

---

## 2. Relational Schema Architecture (ASCII Map)

```text
===================================================================================================
                                      RELATIONAL SCHEMA MAP
===================================================================================================

 [ apps.users.User ] (Table: users_user)
   ├── id (PK, BigAutoField)
   ├── username (VarChar(150), UNIQUE)
   ├── email (VarChar(254), UNIQUE)
   ├── password (VarChar(128))
   ├── role [ USER | PROVIDER ]
   └── per_minute_rate (Decimal(10,2), Min: 0.01)
         │
         ├─── (1 : 1) ───────────► [ apps.wallets.Wallet ] (Table: wallets_wallet)
         │                           ├── id (PK, BigAutoField)
         │                           ├── user_id (FK, OneToOne, UNIQUE)
         │                           └── balance (Decimal(12,2)) [ CONSTRAINT: balance >= 0.00 ]
         │                                 │
         │                                 └─── (1 : N) ──────► [ apps.wallets.WalletTransaction ]
         │                                                        ├── id (PK, BigAutoField)
         │                                                        ├── wallet_id (FK)
         │                                                        ├── user_id (FK)
         │                                                        ├── amount (Decimal(12,2), Min: 0.01)
         │                                                        ├── transaction_type [ recharge | session_debit | refund ]
         │                                                        ├── reference (VarChar(100), UNIQUE, INDEXED)
         │                                                        └── status [ pending | success | failed, INDEXED ]
         │                                                                  ▲
         ├─── (1 : N Client) ───┐                                           │ (1 : 1 Settlement)
         │                       ▼                                          │
         └─── (1 : N Provider) ──► [ apps.consultations.ConsultationSession ]
                                     ├── id (PK, BigAutoField)              │
                                     ├── user_id (FK -> users_user)         │
                                     ├── provider_id (FK -> users_user)     │
                                     ├── status [ unassigned..completed ]   │
                                     ├── per_minute_rate (Decimal(10,2))    │
                                     ├── started_at / ended_at              │
                                     ├── total_duration_seconds             │
                                     ├── total_billed_minutes               │
                                     └── total_billed_amount                │
                                           │                                │
                                           └─── (1 : N) ────────────────────┘
                                                 │
                                                 ▼
                                     [ apps.consultations.SessionBillingRecord ]
                                       ├── id (PK, BigAutoField)
                                       ├── session_id (FK)
                                       ├── minute_number (1, 2, 3...)
                                       ├── amount (Decimal(10,2))
                                       ├── wallet_transaction_id (OneToOne, FK, UNIQUE)
                                       └── [ CONSTRAINT: UNIQUE (session_id, minute_number) ]
===================================================================================================
```

---

## 3. Data Dictionary

### Table: `users_user` (Domain App: `apps.users`)
Stores platform user accounts for both clients and providers (astrologers).

| Column | Type | Nullable | Constraints / Default | Description |
|---|---|---|---|---|
| `id` | BigAutoField | No | Primary Key | Unique user identifier |
| `username` | VarChar(150) | No | Unique | Unique username |
| `email` | VarChar(254) | Yes | Validated Unique | Email address |
| `password` | VarChar(128) | No | Hashed (PBKDF2/Argon2) | Secure password hash |
| `role` | VarChar(20) | No | Choices: `USER`, `PROVIDER` (Default: `USER`) | User role |
| `per_minute_rate` | Decimal(10,2) | No | Min: `0.01`, Default: `50.00` | Consultation fee per minute in INR (providers) |
| `is_active` | Boolean | No | Default: `True` | Account active status |
| `is_staff` | Boolean | No | Default: `False` | Staff administrative status |
| `date_joined` | DateTime | No | Auto timestamp | Timestamp when account was created |

---

### Table: `wallets_wallet` (Domain App: `apps.wallets`)
Maintains the real-time prepaid balance for each user.

| Column | Type | Nullable | Constraints / Default | Description |
|---|---|---|---|---|
| `id` | BigAutoField | No | Primary Key | Unique wallet identifier |
| `user_id` | BigInt | No | OneToOneField (`users_user.id`), Unique | The owner of the wallet |
| `balance` | Decimal(12,2) | No | Default: `0.00`, Check: `balance >= 0.00` | Current balance in INR (never negative) |
| `created_at` | DateTime | No | `auto_now_add=True` | Wallet creation timestamp |
| `updated_at` | DateTime | No | `auto_now=True` | Last balance modification timestamp |

**Check Constraints:**
- `wallet_balance_non_negative`: `CHECK (balance >= 0.00)`

---

### Table: `wallets_wallettransaction` (Domain App: `apps.wallets`)
Immutable financial ledger of all balance modifications including top-ups and per-minute session deductions.

| Column | Type | Nullable | Constraints / Default | Description |
|---|---|---|---|---|
| `id` | BigAutoField | No | Primary Key | Unique transaction identifier |
| `wallet_id` | BigInt | No | ForeignKey (`wallets_wallet.id`) | Target wallet |
| `user_id` | BigInt | No | ForeignKey (`users_user.id`) | Target user |
| `amount` | Decimal(12,2) | No | Min: `0.01` | Transaction amount in INR |
| `transaction_type` | VarChar(30) | No | Choices: `recharge`, `session_debit`, `refund` | Ledger entry type |
| `reference` | VarChar(100) | No | Unique, Indexed | Unique idempotency reference |
| `status` | VarChar(20) | No | Choices: `pending`, `success`, `failed`, Indexed | Settlement status |
| `description` | VarChar(255) | Yes | Default: `""` | Description of the transaction |
| `created_at` | DateTime | No | `auto_now_add=True` | Record creation timestamp |
| `updated_at` | DateTime | No | `auto_now=True` | Status update timestamp |

---

### Table: `consultations_consultationsession` (Domain App: `apps.consultations`)
Tracks consultation lifecycle between client and astrologer.

| Column | Type | Nullable | Constraints / Default | Description |
|---|---|---|---|---|
| `id` | BigAutoField | No | Primary Key | Unique session identifier |
| `user_id` | BigInt | No | ForeignKey (`users_user.id`) | Client who created session |
| `provider_id` | BigInt | Yes | ForeignKey (`users_user.id`), `SET_NULL` | Assigned astrologer |
| `status` | VarChar(30) | No | Choices: `unassigned`, `assigned`, `accepted_by_provider`, `in_progress`, `completed`, `cancelled` | State machine status |
| `per_minute_rate` | Decimal(10,2) | No | Default: `0.00` | Rate locked upon session start |
| `started_at` | DateTime | Yes | Nullable | Consultation start timestamp |
| `ended_at` | DateTime | Yes | Nullable | Consultation end timestamp |
| `total_duration_seconds`| Integer | No | Default: `0` | Call duration in seconds |
| `total_billed_minutes` | Integer | No | Default: `0` | Total number of minutes debited |
| `total_billed_amount` | Decimal(12,2) | No | Default: `0.00` | Cumulative amount billed in INR |
| `ended_by` | VarChar(20) | Yes | Choices: `user`, `provider`, `system` | Actor who ended the call |
| `end_reason` | VarChar(255) | Yes | Default: `""` | Reason for completion or termination |
| `created_at` | DateTime | No | `auto_now_add=True` | Session creation timestamp |
| `updated_at` | DateTime | No | `auto_now=True` | Last status modification timestamp |

---

### Table: `consultations_sessionbillingrecord` (Domain App: `apps.consultations`)
Minute-by-minute audit trail ensuring complete settlement transparency and idempotency.

| Column | Type | Nullable | Constraints / Default | Description |
|---|---|---|---|---|
| `id` | BigAutoField | No | Primary Key | Unique billing record identifier |
| `session_id` | BigInt | No | ForeignKey (`consultations_consultationsession.id`) | Associated consultation session |
| `minute_number` | Integer | No | Min: `1` | 1-based index of billed minute (1, 2, 3...) |
| `amount` | Decimal(10,2) | No | Min: `0.01` | Amount debited for this minute |
| `wallet_transaction_id` | BigInt | No | OneToOne (`wallets_wallettransaction.id`), Unique | Linked debit ledger transaction |
| `billed_at` | DateTime | No | `auto_now_add=True` | Timestamp when minute was charged |

**Unique Constraints:**
- `unique_session_minute_billing`: `UNIQUE (session_id, minute_number)`

---

## 4. Cardinality & Relationship Matrix

| Relationship | Type | Parent | Child | On Delete | Enforced Rule |
|---|---|---|---|---|---|
| **User to Wallet** | 1 : 1 | `users_user` | `wallets_wallet` | `CASCADE` | Every user has exactly one wallet. |
| **User to Client Sessions** | 1 : N | `users_user` | `consultations_consultationsession` | `CASCADE` | A user can book multiple sessions over time. |
| **User to Provider Sessions** | 1 : N | `users_user` | `consultations_consultationsession` | `SET_NULL` | A provider can attend multiple sessions over time. |
| **Wallet to Transactions** | 1 : N | `wallets_wallet` | `wallets_wallettransaction` | `CASCADE` | A wallet contains an immutable log of transactions. |
| **Session to Billing Records** | 1 : N | `consultations_consultationsession` | `consultations_sessionbillingrecord` | `CASCADE` | A session generates one record per elapsed minute. |
| **Billing Record to Transaction**| 1 : 1 | `wallets_wallettransaction` | `consultations_sessionbillingrecord` | `PROTECT` | Each billed minute corresponds to one unique wallet debit. |
