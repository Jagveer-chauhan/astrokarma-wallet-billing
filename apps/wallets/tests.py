from decimal import Decimal
import pytest
from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.exceptions import BusinessRuleViolation, InsufficientBalanceError
from apps.users.models import User, UserRole
from apps.wallets.models import TransactionStatus, TransactionType, Wallet, WalletTransaction
from apps.wallets.services import WalletService


@pytest.mark.django_db
class TestWalletService:
    @pytest.fixture(autouse=True)
    def setup_users(self):
        self.user = User.objects.create_user(
            username="testclient",
            email="client@example.com",
            password="password123",
            role=UserRole.USER,
        )
        self.wallet = WalletService.get_or_create_wallet(self.user)

    def test_wallet_initial_balance_is_zero(self):
        assert self.wallet.balance == Decimal("0.00")

    def test_initiate_recharge(self):
        txn = WalletService.initiate_recharge(self.user, Decimal("250.00"), reference="ref_001")
        assert txn.status == TransactionStatus.PENDING
        assert txn.amount == Decimal("250.00")
        assert txn.reference == "ref_001"
        assert self.wallet.balance == Decimal("0.00")

    def test_initiate_recharge_invalid_amount(self):
        with pytest.raises(BusinessRuleViolation):
            WalletService.initiate_recharge(self.user, Decimal("0.00"))

    def test_process_recharge_callback_success(self):
        txn = WalletService.initiate_recharge(self.user, Decimal("500.00"), reference="ref_002")
        updated_txn = WalletService.process_recharge_callback("ref_002", "success")

        assert updated_txn.status == TransactionStatus.SUCCESS
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("500.00")

    def test_duplicate_recharge_callback_is_idempotent(self):
        """Scenario 5: Process the same successful recharge callback twice. Verify wallet credited once."""
        WalletService.initiate_recharge(self.user, Decimal("300.00"), reference="ref_dup_001")

        # First callback
        first_call = WalletService.process_recharge_callback("ref_dup_001", "success")
        assert first_call.status == TransactionStatus.SUCCESS
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("300.00")

        # Second callback (same ref)
        second_call = WalletService.process_recharge_callback("ref_dup_001", "success")
        assert second_call.status == TransactionStatus.SUCCESS
        self.wallet.refresh_from_db()
        # Balance must remain 300.00, NOT 600.00
        assert self.wallet.balance == Decimal("300.00")

    def test_process_recharge_callback_failed(self):
        WalletService.initiate_recharge(self.user, Decimal("100.00"), reference="ref_fail_001")
        updated_txn = WalletService.process_recharge_callback("ref_fail_001", "failed")

        assert updated_txn.status == TransactionStatus.FAILED
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("0.00")

    def test_deduct_balance_success(self):
        WalletService.initiate_recharge(self.user, Decimal("200.00"), reference="ref_deduct_01")
        WalletService.process_recharge_callback("ref_deduct_01", "success")

        txn = WalletService.deduct_for_billing(
            wallet_id=self.wallet.id,
            amount=Decimal("50.00"),
            reference="bill_ref_01",
            description="Minute 1 billing",
        )
        assert txn.status == TransactionStatus.SUCCESS
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("150.00")

    def test_deduct_balance_insufficient(self):
        with pytest.raises(InsufficientBalanceError):
            WalletService.deduct_for_billing(
                wallet_id=self.wallet.id,
                amount=Decimal("50.00"),
                reference="bill_ref_fail",
                description="Failed billing",
            )
        self.wallet.refresh_from_db()
        assert self.wallet.balance == Decimal("0.00")


@pytest.mark.django_db
class TestWalletAPI:
    def setup_method(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="api_client",
            email="api_client@example.com",
            password="password123",
            role=UserRole.USER,
        )
        self.client.force_authenticate(user=self.user)

    def test_get_wallet_balance(self):
        response = self.client.get("/api/v1/wallet/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["balance"] == "0.00"

    def test_initiate_recharge_endpoint(self):
        payload = {"amount": "150.00", "reference": "api_ref_001"}
        response = self.client.post("/api/v1/wallet/recharge/initiate/", payload)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["transaction"]["reference"] == "api_ref_001"
        assert response.data["transaction"]["status"] == "pending"

    def test_callback_endpoint_idempotency(self):
        # Initiate
        self.client.post(
            "/api/v1/wallet/recharge/initiate/",
            {"amount": "200.00", "reference": "webhook_test_01"},
        )

        unauthenticated_client = APIClient()
        callback_payload = {"reference": "webhook_test_01", "status": "success"}

        # First webhook call
        resp1 = unauthenticated_client.post("/api/v1/wallet/recharge/callback/", callback_payload)
        assert resp1.status_code == status.HTTP_200_OK
        assert resp1.data["current_wallet_balance"] == "200.00"

        # Second webhook call (duplicate)
        resp2 = unauthenticated_client.post("/api/v1/wallet/recharge/callback/", callback_payload)
        assert resp2.status_code == status.HTTP_200_OK
        assert resp2.data["current_wallet_balance"] == "200.00"

    def test_initiate_recharge_reference_collision_cross_user_fails(self):
        # User 1 initiates with ref_shared
        WalletService.initiate_recharge(self.user, Decimal("100.00"), reference="ref_shared")

        # User 2 tries to initiate with ref_shared -> must be rejected
        other_user = User.objects.create_user(username="other", email="other@example.com", password="password123")
        with pytest.raises(BusinessRuleViolation, match="already in use by another user"):
            WalletService.initiate_recharge(other_user, Decimal("100.00"), reference="ref_shared")

    def test_cannot_succeed_already_failed_callback(self):
        WalletService.initiate_recharge(self.user, Decimal("100.00"), reference="ref_fail_trans")
        WalletService.process_recharge_callback("ref_fail_trans", "failed")

        with pytest.raises(BusinessRuleViolation, match="already failed"):
            WalletService.process_recharge_callback("ref_fail_trans", "success")

    def test_deduct_negative_or_zero_fails(self):
        wallet = WalletService.get_or_create_wallet(self.user)
        with pytest.raises(BusinessRuleViolation, match="greater than zero"):
            WalletService.deduct_for_billing(wallet.id, Decimal("-10.00"), "ref_neg", "test")

        with pytest.raises(BusinessRuleViolation, match="greater than zero"):
            WalletService.deduct_for_billing(wallet.id, Decimal("0.00"), "ref_zero", "test")

    def test_webhook_secret_enforcement(self, settings):
        settings.WEBHOOK_SECRET = "supersecretkey"

        self.client.post("/api/v1/wallet/recharge/initiate/", {"amount": "100.00", "reference": "ref_sec_01"})
        unauthenticated_client = APIClient()

        # Without header -> 403 Forbidden
        bad_resp = unauthenticated_client.post(
            "/api/v1/wallet/recharge/callback/",
            {"reference": "ref_sec_01", "status": "success"},
        )
        assert bad_resp.status_code == status.HTTP_403_FORBIDDEN

        # With valid header -> 200 OK
        good_resp = unauthenticated_client.post(
            "/api/v1/wallet/recharge/callback/",
            {"reference": "ref_sec_01", "status": "success"},
            headers={"X-Webhook-Secret": "supersecretkey"},
        )
        assert good_resp.status_code == status.HTTP_200_OK

    def test_wallet_transactions_pagination(self):
        wallet = WalletService.get_or_create_wallet(self.user)
        for i in range(25):
            WalletTransaction.objects.create(
                wallet=wallet,
                user=self.user,
                amount=Decimal("10.00"),
                reference=f"pag_ref_{i}",
                status=TransactionStatus.SUCCESS,
            )

        resp = self.client.get("/api/v1/wallet/transactions/")
        assert resp.status_code == status.HTTP_200_OK
        assert "results" in resp.data
        assert resp.data["count"] == 25
        assert len(resp.data["results"]) == 20  # page size is 20
