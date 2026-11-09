from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class League(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="leagues",
    )
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leagues",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    format = models.CharField(max_length=80, default="round_robin")
    rules = models.TextField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    home_venue = models.ForeignKey(
        "events.EventLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Where generated rounds are played unless the host picks another place.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizer", "name"],
                name="unique_league_name_per_organizer",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True)
                | Q(starts_on__isnull=True)
                | Q(ends_on__gte=models.F("starts_on")),
                name="league_end_on_or_after_start",
            ),
        ]

    def clean(self):
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "League end date must not be before its start date."})

    def __str__(self):
        return self.name


class Team(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.PROTECT,
        related_name="teams",
    )
    name = models.CharField(max_length=120)
    short_name = models.CharField(max_length=12, blank=True)
    captain_fee = models.DecimalField(
        "captain pays",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="What this team's captain owes the host.",
    )
    fee_currency = models.CharField(max_length=3, default="USD")
    fee_paid = models.BooleanField("captain has paid", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["league", "name"],
                name="unique_team_name_per_league",
            ),
        ]

    def __str__(self):
        return self.name

    def active_memberships(self):
        return self.memberships.filter(status=TeamMembership.Status.ACTIVE).select_related("user")

    @property
    def captain(self):
        membership = self.active_memberships().filter(role=TeamMembership.Role.CAPTAIN).first()
        return membership.user if membership else None


class TeamMembership(models.Model):
    class Role(models.TextChoices):
        PLAYER = "player", "Player"
        COACH = "coach", "Coach"
        CAPTAIN = "captain", "Captain"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        LEFT = "left", "Left team"
        REMOVED = "removed", "Removed"

    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="team_memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PLAYER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    jersey_number = models.PositiveSmallIntegerField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["team", "user"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"],
                condition=Q(status__in=["pending", "active"]),
                name="unique_active_team_membership",
            ),
            models.UniqueConstraint(
                fields=["team", "jersey_number"],
                condition=Q(jersey_number__isnull=False, status="active"),
                name="unique_active_team_jersey_number",
            ),
        ]
        indexes = [models.Index(fields=["team", "status"])]

    def __str__(self):
        return f"{self.user} on {self.team}"


class Match(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    league = models.ForeignKey(
        League,
        on_delete=models.PROTECT,
        related_name="matches",
    )
    event = models.OneToOneField(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="soccer_match",
    )
    home_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="home_matches",
    )
    away_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="away_matches",
    )
    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    result_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["event__starts_at"]
        indexes = [models.Index(fields=["league", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(home_team=models.F("away_team")),
                name="match_teams_must_differ",
            ),
            models.CheckConstraint(
                condition=(Q(home_score__isnull=True) & Q(away_score__isnull=True))
                | (Q(home_score__isnull=False) & Q(away_score__isnull=False)),
                name="match_scores_set_together",
            ),
            models.CheckConstraint(
                condition=Q(status="scheduled", home_score__isnull=True)
                | Q(status="cancelled", home_score__isnull=True)
                | Q(status="completed", home_score__isnull=False),
                name="match_result_matches_status",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.home_team_id and self.away_team_id:
            if self.home_team_id == self.away_team_id:
                errors["away_team"] = "A team cannot play itself."
            if self.home_team.league_id != self.league_id:
                errors["home_team"] = "Home team must belong to this league."
            if self.away_team.league_id != self.league_id:
                errors["away_team"] = "Away team must belong to this league."
        if self.event_id and self.league_id:
            if self.event.organizer_id != self.league.organizer_id:
                errors["event"] = "Match event must be owned by the league organizer."
        if self.status == self.Status.COMPLETED and (
            self.home_score is None or self.away_score is None
        ):
            errors["status"] = "Completed matches must have both scores recorded."
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def record_result(self, *, home_score, away_score):
        if home_score < 0 or away_score < 0:
            raise ValidationError("Scores cannot be negative.")
        locked_match = Match.objects.select_for_update().get(pk=self.pk)
        locked_match.home_score = home_score
        locked_match.away_score = away_score
        locked_match.status = Match.Status.COMPLETED
        locked_match.result_recorded_at = timezone.now()
        locked_match.full_clean()
        locked_match.save(
            update_fields=(
                "home_score",
                "away_score",
                "status",
                "result_recorded_at",
            )
        )
        self.refresh_from_db()
        return self

    def __str__(self):
        return f"{self.home_team} vs {self.away_team}"


class LeagueChatMessage(models.Model):
    """The league's group chat. Every approved member can read and write; the host can delete."""

    MAX_LENGTH = 1000

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField(max_length=MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.sender} in {self.league}: {self.body[:40]}"


class LeagueJoinRequest(models.Model):
    """A member asking the host for a place on a team."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="join_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="league_join_requests")
    note = models.CharField(max_length=200, blank=True, help_text="Optional: favourite position, a friend to play with.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["league", "user"],
                condition=Q(status="pending"),
                name="one_pending_join_request_per_league",
            ),
        ]

    def __str__(self):
        return f"{self.user} wants to play in {self.league}"
