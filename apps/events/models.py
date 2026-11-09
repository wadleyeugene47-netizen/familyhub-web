import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class EventCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True)
    description = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "event categories"

    def __str__(self):
        return self.name


class EventLocation(models.Model):
    name = models.CharField(max_length=160, blank=True)
    address_line = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country_code = models.CharField(max_length=2)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(latitude__isnull=True) | Q(latitude__gte=-90, latitude__lte=90),
                name="location_latitude_in_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__isnull=True) | Q(longitude__gte=-180, longitude__lte=180),
                name="location_longitude_in_range",
            ),
        ]

    def __str__(self):
        return self.name or f"{self.city}, {self.country_code}"


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Waiting for approval"
        REJECTED = "rejected", "Declined"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="events",
    )
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    category = models.ForeignKey(
        EventCategory,
        on_delete=models.PROTECT,
        related_name="events",
    )
    location = models.ForeignKey(
        EventLocation,
        on_delete=models.PROTECT,
        related_name="events",
    )
    title = models.CharField(max_length=180)
    description = models.TextField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default="UTC")
    capacity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    review_note = models.TextField(
        blank=True,
        help_text="The owner's message to the organizer when declining or cancelling the event.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["status", "starts_at"]),
            models.Index(fields=["organizer", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="event_ends_after_start",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gt=0),
                name="event_capacity_positive",
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0),
                name="event_price_nonnegative",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_free(self):
        return self.price == 0


class EventCancellationPolicy(models.Model):
    event = models.OneToOneField(
        Event,
        on_delete=models.PROTECT,
        related_name="cancellation_policy",
    )
    cancellations_allowed = models.BooleanField(default=True)
    refund_deadline_hours = models.PositiveSmallIntegerField(default=24)
    policy_text = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(refund_deadline_hours__gte=0),
                name="refund_deadline_nonnegative",
            ),
        ]

    def __str__(self):
        return f"Cancellation policy for {self.event.title}"


class EventImage(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="images",
    )
    image = models.ImageField(upload_to="event_images/%Y/%m/")
    alt_text = models.CharField(max_length=180)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["event", "sort_order"])]

    def __str__(self):
        return f"Image for {self.event.title}"
