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
        reference_id = reference.strip() if reference and reference.strip() else f"rech_{uuid.uuid4().hex[:16]}"

        if reference:
            existing_txn = WalletTransaction.objects.filter(reference=reference_id).first()
            if existing_txn:
                if existing_txn.user_id != user.id:
                    raise BusinessRuleViolation("Transaction reference is already in use by another user.")
                if existing_txn.status != TransactionStatus.PENDING:
                    raise BusinessRuleViolation(f"Transaction reference is already finalized with status '{existing_txn.status}'.")
                if existing_txn.amount != amount:
                    raise BusinessRuleViolation("Pending transaction with this reference already exists with a different amount.")
                return existing_txn

        transaction_record = WalletTransaction.objects.create(
            wallet=wallet,
            user=user,
            amount=amount,
            reference=reference_id,
            transaction_type=TransactionType.RECHARGE,
            status=TransactionStatus.PENDING,
            description=f"Wallet recharge of INR {amount}",
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

            normalized_status = callback_status.strip().lower()

            # Idempotency guard: If already success, do not credit again
            if txn.status == TransactionStatus.SUCCESS:
                return txn

            if txn.status == TransactionStatus.FAILED:
                if normalized_status == TransactionStatus.FAILED:
                    return txn
                raise BusinessRuleViolation(f"Transaction '{reference}' is already failed and cannot transition to '{callback_status}'.")

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
        if amount <= Decimal("0.00"):
            raise BusinessRuleViolation("Deduction amount must be greater than zero.")

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
