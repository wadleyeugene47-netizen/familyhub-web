from django.conf import settings
from django.db import models
from django.db.models import Q


class Registration(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="event_registrations",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "participant"],
                condition=Q(status__in=["pending_payment", "confirmed"]),
                name="unique_active_event_registration",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "status"]),
            models.Index(fields=["participant", "status"]),
        ]

    def __str__(self):
        return f"{self.participant} - {self.event} ({self.status})"
