from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Championship(models.Model):
    class Format(models.TextChoices):
        GROUPS = "groups", "Group stage only"
        KNOCKOUT = "knockout", "Knockout only"
        GROUPS_KNOCKOUT = "groups_knockout", "Group stage, then knockout"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class TieBreaker(models.TextChoices):
        OVERALL_FIRST = "overall_first", "Goal difference, goals scored, then head-to-head"
        HEAD_TO_HEAD_FIRST = "head_to_head_first", "Head-to-head, then goal difference and goals scored"

    class KnockoutTieRule(models.TextChoices):
        EXTRA_TIME_PENALTIES = "extra_time_penalties", "Extra time, then penalties"
        PENALTIES = "penalties", "Straight to penalties"
        ORGANIZER = "organizer", "Organizer decides (replay, coin toss or other approved method)"

    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="championships",
    )
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="championships",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to="event_images/championships/%Y/%m/", blank=True)
    format = models.CharField(max_length=20, choices=Format.choices, default=Format.GROUPS_KNOCKOUT)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    team_count = models.PositiveSmallIntegerField(
        default=8,
        validators=[MinValueValidator(2), MaxValueValidator(64)],
    )
    group_count = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(16)],
    )
    advance_per_group = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(16)],
        help_text="How many teams from each group reach the knockout stage.",
    )
    group_legs = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(2)],
        help_text="1 = each pair meets once, 2 = home and away.",
    )
    third_place_match = models.BooleanField(default=False)

    points_for_win = models.PositiveSmallIntegerField(default=3)
    points_for_draw = models.PositiveSmallIntegerField(default=1)
    points_for_loss = models.PositiveSmallIntegerField(default=0)
    tie_breaker = models.CharField(
        max_length=24,
        choices=TieBreaker.choices,
        default=TieBreaker.OVERALL_FIRST,
    )
    knockout_tie_rule = models.CharField(
        max_length=24,
        choices=KnockoutTieRule.choices,
        default=KnockoutTieRule.EXTRA_TIME_PENALTIES,
    )

    match_duration_minutes = models.PositiveSmallIntegerField(
        default=90,
        validators=[MinValueValidator(10), MaxValueValidator(180)],
    )
    break_between_matches_minutes = models.PositiveSmallIntegerField(
        default=15,
        validators=[MaxValueValidator(240)],
    )
    first_kickoff_at = models.DateTimeField(null=True, blank=True)
    days_between_rounds = models.PositiveSmallIntegerField(
        default=7,
        validators=[MaxValueValidator(60)],
        help_text="0 plays every round on the same day.",
    )
    timezone = models.CharField(max_length=64, default="UTC")
    field_names = models.CharField(
        max_length=255,
        default="Field 1",
        help_text="Comma-separated pitch names. Matches in the same round are spread across them.",
    )
    location = models.ForeignKey(
        "events.EventLocation",
        on_delete=models.PROTECT,
        related_name="championships",
        null=True,
        blank=True,
    )

    registration_deadline = models.DateTimeField(null=True, blank=True)
    entry_requirements = models.TextField(blank=True)
    entry_event = models.ForeignKey(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="championship_entries",
        null=True,
        blank=True,
        help_text="Optional paid event teams register through; its price is the entry fee.",
    )
    rules = models.TextField(blank=True)
    advancement_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("manage_any_championship", "Can manage any championship")]
        constraints = [
            models.UniqueConstraint(
                fields=["organizer", "name"],
                name="unique_championship_name_per_organizer",
            ),
            models.CheckConstraint(
                condition=Q(points_for_win__gt=models.F("points_for_draw"))
                & Q(points_for_draw__gte=models.F("points_for_loss")),
                name="championship_points_ordered",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.points_for_win is not None and self.points_for_draw is not None:
            if self.points_for_win <= self.points_for_draw:
                errors["points_for_win"] = "A win must be worth more than a draw."
        if self.points_for_draw is not None and self.points_for_loss is not None:
            if self.points_for_draw < self.points_for_loss:
                errors["points_for_draw"] = "A draw must be worth at least as much as a loss."
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            errors["timezone"] = "Enter a valid IANA timezone, such as Europe/Lisbon."
        if self.entry_event_id and self.organizer_id and self.entry_event.organizer_id != self.organizer_id:
            errors["entry_event"] = "The entry event must belong to the championship organizer."
        if errors:
            raise ValidationError(errors)

    @property
    def has_groups(self):
        return self.format in (self.Format.GROUPS, self.Format.GROUPS_KNOCKOUT)

    @property
    def has_knockout(self):
        return self.format in (self.Format.KNOCKOUT, self.Format.GROUPS_KNOCKOUT)

    @property
    def is_public(self):
        return self.status in (self.Status.PUBLISHED, self.Status.COMPLETED)

    @property
    def fields(self):
        names = [name.strip() for name in self.field_names.split(",") if name.strip()]
        return names or ["Field 1"]

    @property
    def zone(self):
        return ZoneInfo(self.timezone)

    @property
    def entry_fee(self):
        return self.entry_event.price if self.entry_event_id else None

    def can_manage(self, user):
        if not user.is_authenticated:
            return False
        if self.organizer.user_id == user.pk:
            return True
        return user.is_superuser or user.has_perm("championships.manage_any_championship")


class Group(models.Model):
    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=40)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["championship", "name"], name="unique_group_name_per_championship"),
            models.UniqueConstraint(fields=["championship", "order"], name="unique_group_order_per_championship"),
        ]

    def __str__(self):
        return self.name


class Team(models.Model):
    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)
    short_name = models.CharField(max_length=12, blank=True)
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        related_name="teams",
        null=True,
        blank=True,
    )
    seed = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional. Lower seeds are spread across groups and kept apart in the bracket.",
    )
    lots_rank = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Drawing of lots, used only when every tie-breaker is level. Lower ranks finish higher.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["championship", "name"], name="unique_team_name_per_championship"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.group_id and self.championship_id and self.group.championship_id != self.championship_id:
            raise ValidationError({"group": "The group must belong to this championship."})


class Match(models.Model):
    class Stage(models.TextChoices):
        GROUP = "group", "Group stage"
        KNOCKOUT = "knockout", "Knockout"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        POSTPONED = "postponed", "Postponed"
        CANCELLED = "cancelled", "Cancelled"

    class Round(models.TextChoices):
        ROUND_OF_64 = "r64", "Round of 64"
        ROUND_OF_32 = "r32", "Round of 32"
        ROUND_OF_16 = "r16", "Round of 16"
        QUARTER_FINAL = "qf", "Quarter-final"
        SEMI_FINAL = "sf", "Semi-final"
        FINAL = "final", "Final"
        THIRD_PLACE = "third", "Third-place match"

    class Decision(models.TextChoices):
        REGULAR_TIME = "regular", "Regular time"
        EXTRA_TIME = "extra_time", "After extra time"
        PENALTIES = "penalties", "Penalties"
        ORGANIZER = "organizer", "Organizer decision"

    class Outcome(models.TextChoices):
        WINNER = "winner", "Winner"
        LOSER = "loser", "Loser"

    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name="matches")
    stage = models.CharField(max_length=10, choices=Stage.choices)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="matches", null=True, blank=True)
    round_number = models.PositiveSmallIntegerField(default=1)
    knockout_round = models.CharField(max_length=6, choices=Round.choices, blank=True)
    bracket_position = models.PositiveSmallIntegerField(null=True, blank=True)

    home_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="home_matches", null=True, blank=True)
    away_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="away_matches", null=True, blank=True)

    # Where knockout participants come from: a group finishing position or an earlier match.
    home_source_group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    home_source_rank = models.PositiveSmallIntegerField(null=True, blank=True)
    home_source_match = models.ForeignKey("self", on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    home_source_outcome = models.CharField(max_length=6, choices=Outcome.choices, blank=True)
    away_source_group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    away_source_rank = models.PositiveSmallIntegerField(null=True, blank=True)
    away_source_match = models.ForeignKey("self", on_delete=models.CASCADE, related_name="+", null=True, blank=True)
    away_source_outcome = models.CharField(max_length=6, choices=Outcome.choices, blank=True)

    kickoff_at = models.DateTimeField(null=True, blank=True)
    field_name = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)

    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    home_penalties = models.PositiveSmallIntegerField(null=True, blank=True)
    away_penalties = models.PositiveSmallIntegerField(null=True, blank=True)
    decided_by = models.CharField(max_length=12, choices=Decision.choices, blank=True)
    winner = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="won_matches", null=True, blank=True)
    result_recorded_at = models.DateTimeField(null=True, blank=True)
    result_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kickoff_at", "round_number", "bracket_position", "pk"]
        verbose_name_plural = "matches"
        indexes = [
            models.Index(fields=["championship", "stage", "status"]),
            models.Index(fields=["championship", "kickoff_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(home_team__isnull=True)
                | Q(away_team__isnull=True)
                | ~Q(home_team=models.F("away_team")),
                name="championship_match_teams_differ",
            ),
            models.CheckConstraint(
                condition=(Q(home_score__isnull=True) & Q(away_score__isnull=True))
                | (Q(home_score__isnull=False) & Q(away_score__isnull=False)),
                name="championship_match_scores_set_together",
            ),
            models.CheckConstraint(
                condition=(Q(status="completed") & Q(home_score__isnull=False))
                | (~Q(status="completed") & Q(home_score__isnull=True)),
                name="championship_match_result_matches_status",
            ),
            models.CheckConstraint(
                condition=(Q(home_penalties__isnull=True) & Q(away_penalties__isnull=True))
                | (Q(home_penalties__isnull=False) & Q(away_penalties__isnull=False)),
                name="championship_match_penalties_set_together",
            ),
            models.CheckConstraint(
                condition=Q(stage="group", group__isnull=False, knockout_round="")
                | Q(stage="knockout", group__isnull=True, bracket_position__isnull=False)
                & ~Q(knockout_round=""),
                name="championship_match_stage_fields",
            ),
            models.CheckConstraint(
                condition=~Q(stage="knockout", status="cancelled"),
                name="championship_knockout_not_cancelled",
            ),
            models.UniqueConstraint(
                fields=["group", "home_team", "away_team"],
                condition=Q(stage="group"),
                name="unique_group_fixture",
            ),
            models.UniqueConstraint(
                fields=["championship", "knockout_round", "bracket_position"],
                condition=Q(stage="knockout"),
                name="unique_bracket_slot",
            ),
        ]

    def __str__(self):
        return f"{self.home_label} vs {self.away_label}"

    @property
    def is_knockout(self):
        return self.stage == self.Stage.KNOCKOUT

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def teams_known(self):
        return self.home_team_id is not None and self.away_team_id is not None

    @property
    def loser(self):
        if self.winner_id is None:
            return None
        return self.away_team if self.winner_id == self.home_team_id else self.home_team

    @property
    def stage_label(self):
        if self.is_knockout:
            label = self.get_knockout_round_display()
            if self.knockout_round in (self.Round.FINAL, self.Round.THIRD_PLACE):
                return label
            return f"{label} {self.bracket_position}"
        return f"{self.group.name} · Matchday {self.round_number}"

    def _source_label(self, side):
        group = getattr(self, f"{side}_source_group")
        if group is not None:
            rank = getattr(self, f"{side}_source_rank")
            return f"{group.name} · {ordinal(rank)}"
        source = getattr(self, f"{side}_source_match")
        if source is not None:
            outcome = getattr(self, f"{side}_source_outcome")
            return f"{'Loser' if outcome == self.Outcome.LOSER else 'Winner'} of {source.stage_label}"
        return "To be decided"

    @property
    def home_label(self):
        return self.home_team.name if self.home_team_id else self._source_label("home")

    @property
    def away_label(self):
        return self.away_team.name if self.away_team_id else self._source_label("away")

    def clean(self):
        super().clean()
        errors = {}
        if self.home_team_id and self.home_team_id == self.away_team_id:
            errors["away_team"] = "A team cannot play itself."
        for side in ("home", "away"):
            team = getattr(self, f"{side}_team")
            if team is None:
                continue
            if team.championship_id != self.championship_id:
                errors[f"{side}_team"] = "Teams must belong to this championship."
            elif self.stage == self.Stage.GROUP and team.group_id != self.group_id:
                errors[f"{side}_team"] = "Group fixtures can only include teams from that group."
        if self.stage == self.Stage.KNOCKOUT and self.status == self.Status.CANCELLED:
            errors["status"] = "Knockout matches cannot be cancelled; record an organizer decision instead."
        if self.status == self.Status.COMPLETED and (self.home_score is None or self.away_score is None):
            errors["status"] = "Completed matches need both scores."
        if errors:
            raise ValidationError(errors)


class MatchIncident(models.Model):
    """Optional goal and card details. The final score never depends on these rows."""

    class Kind(models.TextChoices):
        GOAL = "goal", "Goal"
        PENALTY_GOAL = "penalty_goal", "Penalty goal"
        OWN_GOAL = "own_goal", "Own goal"
        YELLOW_CARD = "yellow", "Yellow card"
        RED_CARD = "red", "Red card"

    GOAL_KINDS = (Kind.GOAL, Kind.PENALTY_GOAL, Kind.OWN_GOAL)

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="incidents")
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="For goals, the team credited with the goal (including own goals). For cards, the player's team.",
    )
    kind = models.CharField(max_length=14, choices=Kind.choices, default=Kind.GOAL)
    player_name = models.CharField(max_length=120, blank=True)
    minute = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(150)])
    assist_name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["minute", "pk"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.player_name}".strip()

    @property
    def is_goal(self):
        return self.kind in self.GOAL_KINDS


class ResultAudit(models.Model):
    class Action(models.TextChoices):
        RESULT_RECORDED = "result_recorded", "Result recorded"
        RESULT_CORRECTED = "result_corrected", "Result corrected"
        RESULT_CLEARED = "result_cleared", "Result cleared"
        RESULT_INVALIDATED = "result_invalidated", "Result invalidated by an earlier correction"
        STATUS_CHANGED = "status_changed", "Status changed"
        SCHEDULE_CHANGED = "schedule_changed", "Schedule changed"
        FIXTURES_GENERATED = "fixtures_generated", "Fixtures generated"
        FIXTURES_CLEARED = "fixtures_cleared", "Fixtures cleared"
        PUBLISHED = "published", "Championship published"
        TEAM_UPDATED = "team_updated", "Team details changed"

    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name="audit_entries")
    match = models.ForeignKey(Match, on_delete=models.SET_NULL, related_name="audit_entries", null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", null=True, blank=True)
    action = models.CharField(max_length=24, choices=Action.choices)
    summary = models.CharField(max_length=255)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["championship", "-created_at"])]

    def __str__(self):
        return self.summary


def ordinal(number):
    if number is None:
        return ""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
