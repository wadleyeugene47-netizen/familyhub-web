import re

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from apps.hub.models import phone_validator


def normalize_phone(phone):
    """Digits only, so "+1 (617) 555-0100" and "16175550100" match."""
    return re.sub(r"\D", "", phone or "")


class User(AbstractUser):
    """One account per person across every community. Community roles live on memberships, not here."""

    class Approval(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Declined"

    phone = models.CharField(max_length=32, blank=True, validators=[phone_validator])
    # Accounts made outside sign-up (createsuperuser, admin, scripts) are approved; sign-up sets pending.
    approval_status = models.CharField(max_length=10, choices=Approval.choices, default=Approval.APPROVED)
    approval_decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="accounts_user_unique_email",
                violation_error_message="An account with this email already exists.",
            ),
        ]

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def is_approved_member(self):
        return self.is_superuser or self.approval_status == self.Approval.APPROVED


class PreApprovedPhone(models.Model):
    """A phone number the owner added. Whoever signs up with it first joins without waiting for approval."""

    phone = models.CharField(max_length=32, validators=[phone_validator])
    phone_digits = models.CharField(max_length=32, unique=True, editable=False)
    name = models.CharField(max_length=120, blank=True, help_text="Who this number belongs to, for your reference.")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    claimed_by = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pre_approval",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "pre-approved phone"

    def __str__(self):
        return f"{self.phone} ({self.name})" if self.name else self.phone

    def save(self, *args, **kwargs):
        self.phone_digits = normalize_phone(self.phone)
        super().save(*args, **kwargs)
