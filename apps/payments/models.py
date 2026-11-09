import uuid

from django.db import models
from django.db.models import Q


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"
        DISPUTED = "disputed", "Disputed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.OneToOneField(
        "registrations.Registration",
        on_delete=models.PROTECT,
        related_name="payment",
    )
    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    organizer_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    stripe_payment_intent_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(condition=Q(platform_fee__gte=0), name="payment_fee_nonnegative"),
            models.CheckConstraint(condition=Q(organizer_amount__gte=0), name="payment_organizer_amount_nonnegative"),
            models.CheckConstraint(
                condition=Q(amount=models.F("platform_fee") + models.F("organizer_amount")),
                name="payment_amount_split_balances",
            ),
        ]
        indexes = [models.Index(fields=["organizer", "status"])]

    def __str__(self):
        return f"Payment {self.pk} ({self.status})"


class PaymentAttempt(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Checkout open"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="attempts")
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    checkout_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    expires_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["payment", "status"])]

    def __str__(self):
        return f"Checkout attempt {self.pk} ({self.status})"


class Refund(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_refund_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive")]


class PaymentDispute(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="disputes")
    stripe_dispute_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class TransferRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        REVERSED = "reversed", "Reversed"
        FAILED = "failed", "Failed"

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="transfer_record")
    stripe_transfer_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrganizerPayout(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="payouts",
    )
    stripe_payout_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    arrival_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organizer", "status"])]


class WebhookEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=120)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} ({self.status})"
