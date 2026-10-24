import math
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import (
    ActiveSessionExistsError,
    BusinessRuleViolation,
    InsufficientBalanceError,
    InvalidStateTransitionError,
    UnauthorizedSessionActionError,
)
from apps.consultations.models import (
    ConsultationSession,
    EndedByRole,
    SessionBillingRecord,
    SessionStatus,
)
from apps.wallets.models import Wallet
from apps.wallets.services import WalletService


class SessionService:
    @staticmethod
    def create_session(user, provider=None) -> ConsultationSession:
        if not user.is_authenticated:
            raise BusinessRuleViolation("Authentication required to create a session.")

        if provider:
            if not provider.is_provider:
                raise BusinessRuleViolation("Assigned user must have the PROVIDER role.")
            if user.id == provider.id:
                raise BusinessRuleViolation("Cannot book a consultation session with yourself.")

        status = SessionStatus.ASSIGNED if provider else SessionStatus.UNASSIGNED
        rate = provider.per_minute_rate if provider else Decimal("0.00")

        return ConsultationSession.objects.create(
            user=user,
            provider=provider,
            status=status,
            per_minute_rate=rate,
        )

    @classmethod
    def assign_provider(cls, session_id: int, user, provider) -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.UNASSIGNED:
                raise InvalidStateTransitionError(
                    f"Cannot assign provider. Current session status is '{session.status}'."
                )

            if session.user_id != user.id and not user.is_staff:
                raise UnauthorizedSessionActionError("Only the session owner can assign a provider.")

            if not provider.is_provider:
                raise BusinessRuleViolation("Assigned user must have the PROVIDER role.")

            if user.id == provider.id:
                raise BusinessRuleViolation("Cannot assign yourself as provider for your own consultation.")

            session.provider = provider
            session.per_minute_rate = provider.per_minute_rate
            session.status = SessionStatus.ASSIGNED
            session.save(update_fields=["provider", "per_minute_rate", "status", "updated_at"])
            return session

    @classmethod
    def accept_session(cls, session_id: int, provider) -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.ASSIGNED:
                raise InvalidStateTransitionError(
                    f"Cannot accept session. Current status is '{session.status}', expected 'assigned'."
                )

            if session.provider_id != provider.id:
                raise UnauthorizedSessionActionError("Only the assigned provider can accept this session.")

            session.status = SessionStatus.ACCEPTED_BY_PROVIDER
            session.save(update_fields=["status", "updated_at"])
            return session

    @classmethod
    def reject_session(cls, session_id: int, provider, reason: str = "Rejected by provider") -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.ASSIGNED:
                raise InvalidStateTransitionError(
                    f"Cannot reject session. Current status is '{session.status}', expected 'assigned'."
                )

            if session.provider_id != provider.id and not provider.is_staff:
                raise UnauthorizedSessionActionError("Only the assigned provider can reject this session.")

            session.status = SessionStatus.CANCELLED
            session.ended_by = EndedByRole.PROVIDER
            session.end_reason = reason
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_by", "end_reason", "ended_at", "updated_at"])
            return session

    @classmethod
    def cancel_session(cls, session_id: int, user, reason: str = "Cancelled by user") -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status in [SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED, SessionStatus.CANCELLED]:
                raise InvalidStateTransitionError(
                    f"Cannot cancel session with status '{session.status}'."
                )

            if session.user_id != user.id and not user.is_staff:
                raise UnauthorizedSessionActionError("Only the session owner can cancel the session.")

            session.status = SessionStatus.CANCELLED
            session.ended_by = EndedByRole.USER
            session.end_reason = reason
            session.ended_at = timezone.now()
            session.save(update_fields=["status", "ended_by", "end_reason", "ended_at", "updated_at"])
            return session

    @classmethod
    def start_session(cls, session_id: int, user) -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.ACCEPTED_BY_PROVIDER:
                raise InvalidStateTransitionError(
                    f"Cannot start session. Current status is '{session.status}', expected 'accepted_by_provider'."
                )

            if session.user_id != user.id:
                raise UnauthorizedSessionActionError("Only the session user can start the session.")

            # Rule: User cannot have more than one active (in_progress) session
            active_session_exists = (
                ConsultationSession.objects.filter(
                    user=user,
                    status=SessionStatus.IN_PROGRESS,
                )
                .exclude(id=session.id)
                .exists()
            )
            if active_session_exists:
                raise ActiveSessionExistsError(
                    "User already has an active session in progress. Complete or end it first."
                )

            # Rule: Provider cannot have more than one active (in_progress) session
            if session.provider:
                provider_active_session = (
                    ConsultationSession.objects.filter(
                        provider=session.provider,
                        status=SessionStatus.IN_PROGRESS,
                    )
                    .exclude(id=session.id)
                    .exists()
                )
                if provider_active_session:
                    raise ActiveSessionExistsError(
                        "The assigned provider already has an active session in progress. Please wait until they finish."
                    )

            # Rule: Minimum balance required is 5x the per-minute session cost
            per_minute_cost = session.per_minute_rate if session.per_minute_rate > Decimal("0.00") else session.provider.per_minute_rate
            required_min_balance = per_minute_cost * Decimal("5")

            WalletService.get_or_create_wallet(user)
            wallet = Wallet.objects.select_for_update().get(user=user)
            if wallet.balance < required_min_balance:
                raise InsufficientBalanceError(
                    f"Insufficient balance to start session. Required minimum: ₹{required_min_balance} "
                    f"(5 x ₹{per_minute_cost}/min). Current balance: ₹{wallet.balance}."
                )

            # Fix per-minute cost and mark in progress
            session.per_minute_rate = per_minute_cost
            session.started_at = timezone.now()
            session.status = SessionStatus.IN_PROGRESS
            session.save(
                update_fields=[
                    "per_minute_rate",
                    "started_at",
                    "status",
                    "updated_at",
                ]
            )
            return session

    @classmethod
    def end_session(
        cls,
        session_id: int,
        requested_by_user,
        reason: str = "Completed successfully",
    ) -> ConsultationSession:
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.IN_PROGRESS:
                raise InvalidStateTransitionError(
                    f"Cannot end session. Session is not in progress (current status: '{session.status}')."
                )

            is_user = session.user_id == requested_by_user.id
            is_provider = session.provider_id == requested_by_user.id

            if not (is_user or is_provider or requested_by_user.is_staff):
                raise UnauthorizedSessionActionError("Only the session user or provider can end the session.")

            ended_by = EndedByRole.USER if is_user else EndedByRole.PROVIDER

            now = timezone.now()
            session.ended_at = now
            if session.started_at:
                session.total_duration_seconds = max(
                    0, int((now - session.started_at).total_seconds())
                )

            # Auto-bill any remaining unbilled elapsed time before completing
            BillingService.process_session_billing(session_id=session.id, as_of_time=now)
            session.refresh_from_db()

            # If BillingService already auto-completed the session (e.g. insufficient funds), return it
            if session.status == SessionStatus.COMPLETED:
                return session

            session.status = SessionStatus.COMPLETED
            session.ended_by = ended_by
            session.end_reason = reason
            session.save(
                update_fields=[
                    "status",
                    "ended_at",
                    "total_duration_seconds",
                    "ended_by",
                    "end_reason",
                    "updated_at",
                ]
            )
            return session


class BillingService:
    @classmethod
    def process_session_billing(
        cls,
        session_id: int,
        target_minutes: int = None,
        as_of_time: datetime = None,
    ) -> dict:
        """
        Calculates and debits billable minutes for an in-progress session.
        Guarantees minute-level idempotency and auto-terminates if balance is insufficient.
        """
        with transaction.atomic():
            session = ConsultationSession.objects.select_for_update().get(id=session_id)

            if session.status != SessionStatus.IN_PROGRESS:
                return {
                    "session_id": session.id,
                    "status": session.status,
                    "message": f"Session is not in progress (status: {session.status}). No billing performed.",
                    "total_billed_minutes": session.total_billed_minutes,
                    "total_billed_amount": str(session.total_billed_amount),
                }

            if not session.started_at:
                return {
                    "session_id": session.id,
                    "status": session.status,
                    "message": "Session has not started yet.",
                    "total_billed_minutes": 0,
                    "total_billed_amount": "0.00",
                }

            # Determine billable minutes
            if target_minutes is not None:
                billable_minutes = target_minutes
            else:
                current_time = as_of_time or timezone.now()
                elapsed_seconds = max(0, int((current_time - session.started_at).total_seconds()))
                billable_minutes = math.ceil(elapsed_seconds / 60) if elapsed_seconds > 0 else 0

            # Lock the user's wallet
            WalletService.get_or_create_wallet(session.user)
            wallet = Wallet.objects.select_for_update().get(user=session.user)
            rate = session.per_minute_rate
            newly_billed_minutes = 0

            for minute_num in range(session.total_billed_minutes + 1, billable_minutes + 1):
                # Minute idempotency guard
                already_billed = SessionBillingRecord.objects.filter(
                    session=session,
                    minute_number=minute_num,
                ).exists()

                if already_billed:
                    session.total_billed_minutes = max(session.total_billed_minutes, minute_num)
                    continue

                # Insufficient balance check for next billable minute
                if wallet.balance < rate:
                    now = timezone.now()
                    session.ended_at = now
                    session.total_duration_seconds = max(
                        0, int((now - session.started_at).total_seconds())
                    )
                    session.status = SessionStatus.COMPLETED
                    session.ended_by = EndedByRole.SYSTEM
                    session.end_reason = "Insufficient Balance"
                    session.save(
                        update_fields=[
                            "status",
                            "ended_at",
                            "total_duration_seconds",
                            "ended_by",
                            "end_reason",
                            "total_billed_minutes",
                            "total_billed_amount",
                            "updated_at",
                        ]
                    )

                    return {
                        "session_id": session.id,
                        "status": session.status,
                        "action": "session_terminated_insufficient_balance",
                        "ended_by": session.ended_by,
                        "end_reason": session.end_reason,
                        "wallet_balance": str(wallet.balance),
                        "total_billed_minutes": session.total_billed_minutes,
                        "total_billed_amount": str(session.total_billed_amount),
                    }

                # Deduct rate from wallet
                ref = f"bill_sess_{session.id}_min_{minute_num}"
                txn = WalletService.deduct_for_billing(
                    wallet_id=wallet.id,
                    amount=rate,
                    reference=ref,
                    description=f"Minute {minute_num} billing for Consultation #{session.id}",
                )

                SessionBillingRecord.objects.create(
                    session=session,
                    minute_number=minute_num,
                    amount=rate,
                    wallet_transaction=txn,
                )

                session.total_billed_minutes = minute_num
                session.total_billed_amount += rate
                newly_billed_minutes += 1

                # Refresh in-memory wallet balance
                wallet.refresh_from_db(fields=["balance"])

            session.save(
                update_fields=[
                    "total_billed_minutes",
                    "total_billed_amount",
                    "updated_at",
                ]
            )

            return {
                "session_id": session.id,
                "status": session.status,
                "newly_billed_minutes": newly_billed_minutes,
                "total_billed_minutes": session.total_billed_minutes,
                "total_billed_amount": str(session.total_billed_amount),
                "wallet_balance": str(wallet.balance),
            }
