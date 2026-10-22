import uuid
from decimal import Decimal
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.common.exceptions import BusinessRuleViolation, InsufficientBalanceError
from apps.wallets.models import TransactionStatus, TransactionType, Wallet, WalletTransaction


class WalletService:
    @staticmethod
    def get_or_create_wallet(user) -> Wallet:
        wallet, _ = Wallet.objects.get_or_create(user=user)
        return wallet

    @classmethod
    def get_balance(cls, user) -> Decimal:
        wallet = cls.get_or_create_wallet(user)
        return wallet.balance

    @classmethod
    def initiate_recharge(cls, user, amount: Decimal, reference: str = None) -> WalletTransaction:
        if amount <= Decimal("0.00"):
            raise BusinessRuleViolation("Recharge amount must be greater than zero.")

        wallet = cls.get_or_create_wallet(user)
        reference_id = reference or f"rech_{uuid.uuid4().hex[:16]}"

        transaction_record, created = WalletTransaction.objects.get_or_create(
            reference=reference_id,
            defaults={
                "wallet": wallet,
                "user": user,
                "amount": amount,
                "transaction_type": TransactionType.RECHARGE,
                "status": TransactionStatus.PENDING,
                "description": f"Wallet recharge of INR {amount}",
            },
        )
        return transaction_record

    @classmethod
    def process_recharge_callback(cls, reference: str, callback_status: str) -> WalletTransaction:
        with transaction.atomic():
            txn = (
                WalletTransaction.objects.select_for_update()
                .filter(reference=reference)
                .first()
            )
            if not txn:
                raise BusinessRuleViolation(f"Transaction with reference '{reference}' does not exist.")

            # Idempotency guard: If already success, do not credit again
            if txn.status == TransactionStatus.SUCCESS:
                return txn

            normalized_status = callback_status.strip().lower()

            if normalized_status == TransactionStatus.SUCCESS:
                wallet = Wallet.objects.select_for_update().get(id=txn.wallet_id)
                wallet.balance += txn.amount
                wallet.save(update_fields=["balance", "updated_at"])

                txn.status = TransactionStatus.SUCCESS
                txn.save(update_fields=["status", "updated_at"])

            elif normalized_status == TransactionStatus.FAILED:
                txn.status = TransactionStatus.FAILED
                txn.save(update_fields=["status", "updated_at"])
            else:
                raise BusinessRuleViolation(f"Invalid callback status '{callback_status}'.")

            return txn

    @classmethod
    def deduct_for_billing(
        cls,
        wallet_id: int,
        amount: Decimal,
        reference: str,
        description: str,
    ) -> WalletTransaction:
        """
        Deducts amount from wallet atomically.
        Assumes caller is within transaction.atomic() or creates one.
        """
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(id=wallet_id)
            if wallet.balance < amount:
                raise InsufficientBalanceError(
                    f"Insufficient balance. Required: {amount}, Available: {wallet.balance}"
                )

            wallet.balance -= amount
            wallet.save(update_fields=["balance", "updated_at"])

            txn = WalletTransaction.objects.create(
                wallet=wallet,
                user=wallet.user,
                amount=amount,
                transaction_type=TransactionType.SESSION_DEBIT,
                reference=reference,
                status=TransactionStatus.SUCCESS,
                description=description,
            )
            return txn
