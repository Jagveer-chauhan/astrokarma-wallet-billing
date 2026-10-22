from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models


class UserRole(models.TextChoices):
    USER = "USER", "User"
    PROVIDER = "PROVIDER", "Provider"


class User(AbstractUser):
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
        db_index=True,
    )
    per_minute_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Consultation rate per minute in INR (applicable for providers)",
    )

    @property
    def is_provider(self) -> bool:
        return self.role == UserRole.PROVIDER

    @property
    def is_client(self) -> bool:
        return self.role == UserRole.USER

    def __str__(self):
        return f"{self.username} ({self.role})"
