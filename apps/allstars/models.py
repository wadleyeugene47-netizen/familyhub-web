from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

MIN_RATING = 1
MAX_RATING = 5


class AllStarGame(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Registration open"
        CLOSED = "closed", "Registration closed"
        PUBLISHED = "published", "Teams announced"
        CANCELLED = "cancelled", "Cancelled"

    PUBLIC_STATUSES = [Status.OPEN, Status.CLOSED, Status.PUBLISHED]

    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.PROTECT,
        related_name="all_star_games",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField("kickoff")
    timezone = models.CharField(max_length=64, default="UTC")
    venue = models.CharField(max_length=200, blank=True)
    registration_closes_at = models.DateTimeField("registration closes", null=True, blank=True)
    team_count = models.PositiveSmallIntegerField(
        "number of teams",
        default=2,
        validators=[MinValueValidator(2), MaxValueValidator(8)],
    )
    eligibility = models.CharField("who can play", max_length=60, default="mixed")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at"]
        constraints = [
            models.UniqueConstraint(fields=["community", "title"], name="unique_all_star_title_per_community"),
        ]

    def __str__(self):
        return self.title

    @property
    def accepting_registrations(self):
        if self.status != self.Status.OPEN:
            return False
        return self.registration_closes_at is None or timezone.now() < self.registration_closes_at

    @property
    def badge_status(self):
        return {"open": "active", "closed": "pending"}.get(self.status, self.status)

    def can_manage(self, user):
        return self.community.can_manage(user)


class AllStarTeam(models.Model):
    game = models.ForeignKey(AllStarGame, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=80)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["game", "name"], name="unique_all_star_team_name_per_game"),
        ]

    def __str__(self):
        return self.name


class AllStarRegistration(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Registered"
        WITHDRAWN = "withdrawn", "Withdrew"
        REMOVED = "removed", "Removed by the organizer"

    game = models.ForeignKey(AllStarGame, on_delete=models.CASCADE, related_name="registrations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="all_star_registrations")
    position = models.CharField(max_length=60)
    gender = models.CharField(max_length=60)
    note = models.CharField(max_length=200, blank=True)
    # Only the organizer ever sees this. Never show it on a player-facing page.
    skill_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_RATING), MaxValueValidator(MAX_RATING)],
    )
    team = models.ForeignKey(
        AllStarTeam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registrations",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["game", "user"], name="one_all_star_registration_per_player"),
            models.CheckConstraint(
                condition=Q(skill_rating__isnull=True) | Q(skill_rating__gte=MIN_RATING, skill_rating__lte=MAX_RATING),
                name="all_star_rating_in_range",
            ),
        ]

    def __str__(self):
        return f"{self.user} in {self.game}"
