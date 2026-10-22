from decimal import Decimal
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.exceptions import (
    ActiveSessionExistsError,
    InsufficientBalanceError,
    InvalidStateTransitionError,
)
from apps.consultations.models import (
    ConsultationSession,
    EndedByRole,
    SessionBillingRecord,
    SessionStatus,
)
from apps.consultations.services import BillingService, SessionService
from apps.users.models import User, UserRole
from apps.wallets.models import Wallet
from apps.wallets.services import WalletService


@pytest.mark.django_db
class TestConsultationBillingLifecycle:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="astrologer_bob",
            email="bob@example.com",
            password="password123",
            role=UserRole.PROVIDER,
            per_minute_rate=Decimal("50.00"),
        )
        self.wallet = WalletService.get_or_create_wallet(self.user)
        self.client = APIClient()

    def test_scenario_1_insufficient_balance_to_start(self):
        """
        Scenario 1: Insufficient balance
        A user with less than 5 minutes' worth of balance cannot start a session.
        Rate = ₹50/min, 5 minutes = ₹250.
        Wallet balance = ₹200 (less than ₹250).
        """
        # Recharge only 200 (needs 250)
        WalletService.initiate_recharge(self.user, Decimal("200.00"), reference="rec_s1")
        WalletService.process_recharge_callback("rec_s1", "success")

        session = SessionService.create_session(self.user, self.provider)
        assert session.status == SessionStatus.ASSIGNED

        SessionService.accept_session(session.id, self.provider)
        session.refresh_from_db()
        assert session.status == SessionStatus.ACCEPTED_BY_PROVIDER

        # Attempt to start must fail with InsufficientBalanceError
        with pytest.raises(InsufficientBalanceError):
            SessionService.start_session(session.id, self.user)

        session.refresh_from_db()
        assert session.status == SessionStatus.ACCEPTED_BY_PROVIDER

    def test_scenario_2_happy_path(self):
        """
        Scenario 2: Happy path
        Recharge wallet -> Create session -> Provider accepts -> Start session ->
        Billing occurs for 2 minutes -> End session.
        Verify that the wallet is debited correctly.
        """
        # 1. Recharge ₹300
        WalletService.initiate_recharge(self.user, Decimal("300.00"), reference="rec_s2")
        WalletService.process_recharge_callback("rec_s2", "success")
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("300.00")

        # 2. Create session
        session = SessionService.create_session(self.user, self.provider)
        assert session.status == SessionStatus.ASSIGNED

        # 3. Provider accepts
        session = SessionService.accept_session(session.id, self.provider)
        assert session.status == SessionStatus.ACCEPTED_BY_PROVIDER

        # 4. Start session (locks per-minute rate = 50.00)
        session = SessionService.start_session(session.id, self.user)
        assert session.status == SessionStatus.IN_PROGRESS
        assert session.per_minute_rate == Decimal("50.00")
        assert session.started_at is not None

        # 5. Billing occurs for 2 minutes (₹100 total)
        billing_result = BillingService.process_session_billing(session.id, target_minutes=2)
        assert billing_result["newly_billed_minutes"] == 2
        assert billing_result["total_billed_minutes"] == 2
        assert billing_result["total_billed_amount"] == "100.00"

        # Verify wallet debited
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("200.00")

        # 6. End session
        session = SessionService.end_session(session.id, self.user, reason="Finished consultation")
        assert session.status == SessionStatus.COMPLETED
        assert session.ended_by == EndedByRole.USER
        assert session.total_billed_amount == Decimal("100.00")
        assert session.total_billed_minutes == 2

    def test_scenario_3_balance_runs_out(self):
        """
        Scenario 3: Balance runs out
        During an active session, the wallet cannot cover the next minute.
        Verify that:
        - The session automatically ends.
        - Wallet does not become negative.
        - The correct ending reason is recorded.
        """
        # User starts with ₹250 (exact 5 minutes at ₹50/min)
        WalletService.initiate_recharge(self.user, Decimal("250.00"), reference="rec_s3")
        WalletService.process_recharge_callback("rec_s3", "success")

        session = SessionService.create_session(self.user, self.provider)
        SessionService.accept_session(session.id, self.provider)
        SessionService.start_session(session.id, self.user)

        # Bill 5 minutes -> ₹250 debited -> balance becomes ₹0
        res = BillingService.process_session_billing(session.id, target_minutes=5)
        assert res["total_billed_minutes"] == 5
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("0.00")

        # Attempt to bill minute 6 (wallet cannot cover ₹50)
        result = BillingService.process_session_billing(session.id, target_minutes=6)

        assert result["action"] == "session_terminated_insufficient_balance"
        assert result["ended_by"] == EndedByRole.SYSTEM
        assert result["end_reason"] == "Insufficient Balance"

        # Check session attributes
        session.refresh_from_db()
        assert session.status == SessionStatus.COMPLETED
        assert session.ended_by == EndedByRole.SYSTEM
        assert session.end_reason == "Insufficient Balance"
        assert session.total_billed_minutes == 5
        assert session.total_billed_amount == Decimal("250.00")
        assert session.ended_at is not None

        # Check wallet never negative
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("0.00")

    def test_scenario_4_invalid_state_transition(self):
        """
        Scenario 4: Invalid state transition
        Attempt an invalid action, such as ending a session before it has started.
        Verify that the request fails with InvalidStateTransitionError / returns 400.
        """
        session = SessionService.create_session(self.user, self.provider)
        assert session.status == SessionStatus.ASSIGNED

        # Attempt to end before starting
        with pytest.raises(InvalidStateTransitionError):
            SessionService.end_session(session.id, self.user)

        # Attempt to start directly before provider accepts
        with pytest.raises(InvalidStateTransitionError):
            SessionService.start_session(session.id, self.user)

    def test_scenario_6_duplicate_billing(self):
        """
        Scenario 6: Duplicate billing
        Trigger billing more than once for the same billable minute.
        Verify that the user is not charged twice.
        """
        WalletService.initiate_recharge(self.user, Decimal("300.00"), reference="rec_s6")
        WalletService.process_recharge_callback("rec_s6", "success")

        session = SessionService.create_session(self.user, self.provider)
        SessionService.accept_session(session.id, self.provider)
        SessionService.start_session(session.id, self.user)

        # Trigger billing for minute 1
        res1 = BillingService.process_session_billing(session.id, target_minutes=1)
        assert res1["newly_billed_minutes"] == 1
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("250.00")

        # Trigger billing for minute 1 again
        res2 = BillingService.process_session_billing(session.id, target_minutes=1)
        assert res2["newly_billed_minutes"] == 0
        self.wallet.refresh_from_db()
        # Balance must remain ₹250.00, NOT debited again
        assert self.wallet.balance == Decimal("250.00")

        # Verify only 1 billing record exists
        assert SessionBillingRecord.objects.filter(session=session).count() == 1

    def test_single_active_session_restriction(self):
        """User cannot have more than one active (in_progress) session at the same time."""
        WalletService.initiate_recharge(self.user, Decimal("1000.00"), reference="rec_multi")
        WalletService.process_recharge_callback("rec_multi", "success")

        # First session
        session1 = SessionService.create_session(self.user, self.provider)
        SessionService.accept_session(session1.id, self.provider)
        SessionService.start_session(session1.id, self.user)
        session1.refresh_from_db()
        assert session1.status == SessionStatus.IN_PROGRESS

        # Second session
        session2 = SessionService.create_session(self.user, self.provider)
        SessionService.accept_session(session2.id, self.provider)

        # Attempt to start second session must fail
        with pytest.raises(ActiveSessionExistsError):
            SessionService.start_session(session2.id, self.user)


@pytest.mark.django_db
class TestConsultationRESTApiEndpoints:
    def setup_method(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="rest_user",
            email="rest_user@example.com",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="rest_provider",
            email="rest_provider@example.com",
            password="password123",
            role=UserRole.PROVIDER,
            per_minute_rate=Decimal("40.00"),
        )
        self.wallet = WalletService.get_or_create_wallet(self.user)

    def test_api_invalid_transition_returns_400(self):
        """Verify invalid state transitions via API return HTTP 400 with descriptive error."""
        self.client.force_authenticate(user=self.user)

        # Create session
        create_resp = self.client.post("/api/v1/sessions/", {"provider_id": self.provider.id})
        assert create_resp.status_code == status.HTTP_201_CREATED
        session_id = create_resp.data["id"]

        # Attempt to end immediately -> HTTP 400
        end_resp = self.client.post(f"/api/v1/sessions/{session_id}/end/")
        assert end_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in end_resp.data
        assert end_resp.data["code"] == "invalid_state_transition"

    def test_api_insufficient_balance_returns_400(self):
        """Verify starting session without 5x rate balance returns HTTP 400."""
        self.client.force_authenticate(user=self.user)

        create_resp = self.client.post("/api/v1/sessions/", {"provider_id": self.provider.id})
        session_id = create_resp.data["id"]

        # Provider accepts
        self.client.force_authenticate(user=self.provider)
        accept_resp = self.client.post(f"/api/v1/sessions/{session_id}/accept/")
        assert accept_resp.status_code == status.HTTP_200_OK

        # User tries to start with 0 balance
        self.client.force_authenticate(user=self.user)
        start_resp = self.client.post(f"/api/v1/sessions/{session_id}/start/")
        assert start_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert start_resp.data["code"] == "insufficient_balance"
