from django.conf import settings
from django.db import models


class EmailNotification(models.Model):
    class Kind(models.TextChoices):
        REGISTRATION_CONFIRMED = "registration_confirmed", "Registration confirmed"
        PAYMENT_PENDING = "payment_pending", "Payment pending"
        PAYMENT_CONFIRMED = "payment_confirmed", "Payment confirmed"
        PAYMENT_FAILED = "payment_failed", "Payment failed"
        REFUND_UPDATED = "refund_updated", "Refund updated"
        EVENT_UPDATED = "event_updated", "Event updated"
        EVENT_CANCELLED = "event_cancelled", "Event cancelled"
        EVENT_REMINDER = "event_reminder", "Event reminder"
        MEMBER_SIGNUP = "member_signup", "New member waiting for approval"
        MEMBER_APPROVED = "member_approved", "Membership approved"
        MEMBER_REJECTED = "member_rejected", "Membership declined"
        EVENT_SUBMITTED = "event_submitted", "Event waiting for approval"
        EVENT_APPROVED = "event_approved", "Event approved"
        EVENT_REJECTED = "event_rejected", "Event declined"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="email_notifications",
    )
    registration = models.ForeignKey(
        "registrations.Registration",
        on_delete=models.PROTECT,
        related_name="email_notifications",
        null=True,
        blank=True,
    )
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.PROTECT,
        related_name="email_notifications",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    dedupe_key = models.CharField(max_length=180, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.kind} to {self.recipient} ({self.status})"
