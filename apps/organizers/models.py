from django.conf import settings
from django.db import models


class OrganizerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organizer_profile",
    )
    display_name = models.CharField(max_length=120)
    bio = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name


class OrganizerPaymentAccount(models.Model):
    class OnboardingStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        PENDING = "pending", "Pending"
        RESTRICTED = "restricted", "Restricted"
        ENABLED = "enabled", "Enabled"

    organizer = models.OneToOneField(
        OrganizerProfile,
        on_delete=models.PROTECT,
        related_name="payment_account",
    )
    provider = models.CharField(max_length=20, default="stripe")
    connected_account_id = models.CharField(max_length=255, unique=True)
    onboarding_status = models.CharField(
        max_length=20,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.NOT_STARTED,
    )
    charges_enabled = models.BooleanField(default=False)
    payouts_enabled = models.BooleanField(default=False)
    details_submitted = models.BooleanField(default=False)
    requirements_due = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organizer} payment account ({self.onboarding_status})"
