from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class SessionStatus(models.TextChoices):
    UNASSIGNED = "unassigned", "Unassigned"
    ASSIGNED = "assigned", "Assigned"
    ACCEPTED_BY_PROVIDER = "accepted_by_provider", "Accepted by Provider"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EndedByRole(models.TextChoices):
    USER = "user", "User"
    PROVIDER = "provider", "Provider"
    SYSTEM = "system", "System"


class ConsultationSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_sessions",
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_sessions",
    )
    status = models.CharField(
        max_length=30,
        choices=SessionStatus.choices,
        default=SessionStatus.UNASSIGNED,
        db_index=True,
    )
    per_minute_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Fixed per-minute rate for the session once started",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    total_duration_seconds = models.PositiveIntegerField(default=0)
    total_billed_minutes = models.PositiveIntegerField(default=0)
    total_billed_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    ended_by = models.CharField(
        max_length=20,
        choices=EndedByRole.choices,
        null=True,
        blank=True,
    )
    end_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Session #{self.id} ({self.status}) - User: {self.user.username}"


class SessionBillingRecord(models.Model):
    session = models.ForeignKey(
        ConsultationSession,
        on_delete=models.CASCADE,
        related_name="billing_records",
    )
    minute_number = models.PositiveIntegerField(
        help_text="1-based index of the billed minute (1, 2, 3...)",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    wallet_transaction = models.OneToOneField(
        "wallets.WalletTransaction",
        on_delete=models.PROTECT,
        related_name="session_billing_record",
    )
    billed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["minute_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "minute_number"],
                name="unique_session_minute_billing",
            )
        ]

    def __str__(self):
        return f"Billing(Session #{self.session_id} - Min {self.minute_number}: ₹{self.amount})"
