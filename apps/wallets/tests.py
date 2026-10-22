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
