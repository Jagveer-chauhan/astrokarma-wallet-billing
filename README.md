# Django Wallet-Based Per-Minute Session Billing

Backend system for a paid 1:1 consultation session built with **Django**, **Django REST Framework (DRF)**, and **PostgreSQL**.

---

## 1. System Architecture & Low-Level Design (LLD)

The codebase follows Clean Architecture with a strict domain service layer:

- **`apps.users`**: Custom `User` model with `USER` and `PROVIDER` roles and provider rates.
- **`apps.wallets`**: `Wallet` and `WalletTransaction` models with `WalletService` encapsulating atomic recharges, callbacks, and debits with database-level row locking (`select_for_update()`).
- **`apps.consultations`**: `ConsultationSession` and `SessionBillingRecord` models with `SessionService` (state machine lifecycle) and `BillingService` (per-minute calculation, idempotency, auto-termination).
- **`apps.common`**: Custom domain exceptions (`BusinessRuleViolation`, `InsufficientBalanceError`, `InvalidStateTransitionError`, `ActiveSessionExistsError`) mapped automatically to `HTTP 400` with clean error payloads.

---

## 2. Entity Relationship (ER) Diagram

```mermaid
erDiagram
    User ||--|| Wallet : owns
    User ||--o{ ConsultationSession : "books as client"
    User ||--o{ ConsultationSession : "attends as provider"
    Wallet ||--o{ WalletTransaction : records
    ConsultationSession ||--o{ SessionBillingRecord : generates
    WalletTransaction ||--o| SessionBillingRecord : links
```

---

## 3. Session State Machine

```mermaid
stateDiagram-v2
    [*] --> unassigned : create_session(provider=None)
    [*] --> assigned : create_session(provider)
    unassigned --> assigned : assign_provider(provider)
    assigned --> accepted_by_provider : accept_session() [by provider]
    accepted_by_provider --> in_progress : start_session() [wallet >= 5x rate & no active session]
    in_progress --> completed : end_session() [by user or provider]
    in_progress --> completed : auto_terminated [by system on insufficient balance]
    completed --> [*]
```

### State Transition Rules
- Transitions outside the allowed path raise `InvalidStateTransitionError` returning `HTTP 400`.
- Only the assigned provider can accept the session.
- Only the session owner can start the session.
- A user cannot start a session if they already have an `in_progress` session.
- Minimum balance to start: `5 × provider.per_minute_rate`.
- Once started, `per_minute_rate` is locked on the session object.

---

## 4. Per-Minute Billing & Insufficient Balance Logic

1. **Minute-Level Idempotency**:
   - `SessionBillingRecord` enforces a unique constraint on `(session_id, minute_number)`.
   - Repeated billing ticks for the same minute number will never double-debit the user.
2. **Atomic Balance Checks**:
   - Every billing tick locks the user's wallet row using `select_for_update()` inside `transaction.atomic()`.
3. **Graceful Auto-Termination on Insufficient Funds**:
   - If `wallet.balance < session.per_minute_rate` during any minute tick:
     - The wallet is **never deducted** into negative balance.
     - The session is immediately marked as `COMPLETED`.
     - `ended_by = "system"`.
     - `end_reason = "Insufficient Balance"`.
     - Actual duration and already billed minutes are safely persisted.

---

## 5. Important Implementation Decisions

| Decision | Rationale |
|---|---|
| **Pessimistic Locking (`select_for_update`)** | Prevents race conditions during concurrent wallet recharges, callbacks, and periodic billing ticks. |
| **Database Constraints** | `wallet_balance_non_negative` (`balance >= 0.00`) and unique `(session, minute_number)` guarantee integrity at the DB layer, independent of application code. |
| **Separation of Services & Views** | Business logic (state transitions, balance checks, mathematical ceiling) resides strictly in `services.py`, keeping serializers and views clean and focused on HTTP transport. |
| **Domain Exceptions -> DRF 400 Handler** | Domain exceptions (`BusinessRuleViolation`) bubble up to a unified exception handler, standardizing `{"error": "...", "code": "..."}` responses across all endpoints. |
| **Idempotent Webhooks** | Recharges use unique `reference` IDs. If a duplicate successful webhook callback is received, the service returns the existing transaction without crediting the wallet again. |

---

## 6. Setup & Running Locally

### Prerequisites
- Python 3.11+
- PostgreSQL 15+

### 1. Clone & Setup Virtual Environment
```bash
git clone <repo-url>
cd "Astrokarma - Django Wallet-Based"

# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Ensure your PostgreSQL credentials in `.env` match your local PostgreSQL instance:
```ini
DB_NAME=astrokarma_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
```

### 3. Run Migrations & Server
```bash
python manage.py migrate
python manage.py runserver
```

---

## 7. Running the Test Suite

The test suite thoroughly verifies all 6 mandatory assessment scenarios plus additional edge cases:

```bash
pytest -v
```

### Verified Test Scenarios:
1. `test_scenario_1_insufficient_balance_to_start`: User with `< 5x` cost cannot start a session.
2. `test_scenario_2_happy_path`: Recharge -> Create -> Provider accepts -> Start -> 2-min billing -> End session -> Wallet debited accurately.
3. `test_scenario_3_balance_runs_out`: Mid-session balance exhaustion -> Auto-terminated by system -> `ended_by="system"` -> `reason="Insufficient Balance"` -> Wallet never negative.
4. `test_scenario_4_invalid_state_transition`: Premature end or invalid transitions return `HTTP 400`.
5. `test_duplicate_recharge_callback_is_idempotent`: Duplicate success webhook credits wallet exactly once.
6. `test_scenario_6_duplicate_billing`: Duplicate ticks for the same minute do not charge twice.
7. `test_single_active_session_restriction`: User cannot run 2 concurrent sessions in progress.
8. `test_api_invalid_transition_returns_400` & `test_api_insufficient_balance_returns_400`: REST API validation.

---

## 8. API Reference

### Authentication (`/api/v1/auth/`)
- `POST /register/` - Register user (`role`: `USER` or `PROVIDER`, `per_minute_rate`)
- `POST /login/` - Login and obtain Auth Token
- `GET /me/` - Current authenticated user profile

### Wallet (`/api/v1/wallet/`)
- `GET /` - View current wallet balance and recent transactions
- `POST /recharge/initiate/` - Initiate a mock recharge:
  ```json
  { "amount": "500.00", "reference": "rech_123" }
  ```
- `POST /recharge/callback/` - Mock payment webhook (Idempotent):
  ```json
  { "reference": "rech_123", "status": "success" }
  ```

### Sessions (`/api/v1/sessions/`)
- `POST /` - Create session (`{"provider_id": 2}`)
- `GET /` - List user / provider sessions
- `GET /{id}/` - Retrieve session details with billing history
- `POST /{id}/assign/` - Assign session to provider (`{"provider_id": 2}`)
- `POST /{id}/accept/` - Provider accepts session
- `POST /{id}/start/` - User starts session (checks min 5x rate balance)
- `POST /{id}/end/` - User or provider ends session (`{"reason": "Finished"}`)
- `POST /{id}/bill/` - Trigger billing tick (`{"target_minutes": 2}`)
