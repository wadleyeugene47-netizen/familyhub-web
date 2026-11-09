from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile

from .models import League, Match, Team
from .standings import calculate_standings


class LeagueModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="league-organizer",
            password="strong-password-123",
        )
        cls.organizer = OrganizerProfile.objects.create(
            user=user,
            display_name="League Organizer",
        )
        cls.league = League.objects.create(
            organizer=cls.organizer,
            name="Community League",
            format="round_robin",
        )
        cls.home_team = Team.objects.create(league=cls.league, name="Harbor FC")
        cls.away_team = Team.objects.create(league=cls.league, name="Hill United")
        cls.category = EventCategory.objects.create(name="Soccer Match", slug="soccer-match")
        cls.location = EventLocation.objects.create(
            address_line="1 Stadium Road",
            city="Sampletown",
            country_code="US",
        )

    def make_match_event(self, title):
        starts_at = timezone.now() + timedelta(days=14)
        return Event.objects.create(
            organizer=self.organizer,
            category=self.category,
            location=self.location,
            title=title,
            description="League fixture.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=100,
            price=Decimal("0.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )

    def make_match(self, *, home_team=None, away_team=None, title="Fixture"):
        return Match.objects.create(
            league=self.league,
            event=self.make_match_event(title),
            home_team=home_team or self.home_team,
            away_team=away_team or self.away_team,
        )

    def test_match_uses_shared_event_for_schedule_and_location(self):
        match = self.make_match()

        self.assertEqual(match.event.location, self.location)
        self.assertEqual(match.event.organizer, self.organizer)

    def test_completed_result_updates_standings(self):
        match = self.make_match()

        match.record_result(home_score=2, away_score=1)

        table = calculate_standings(self.league)
        self.assertEqual(table[0]["team"], self.home_team)
        self.assertEqual(table[0]["points"], 3)
        self.assertEqual(table[0]["goal_difference"], 1)
        self.assertEqual(table[1]["points"], 0)
        self.assertEqual(match.status, Match.Status.COMPLETED)
        self.assertIsNotNone(match.result_recorded_at)

    def test_draw_awards_one_point_to_each_team(self):
        match = self.make_match()
        match.record_result(home_score=1, away_score=1)

        standings = calculate_standings(self.league)

        self.assertEqual([row["points"] for row in standings], [1, 1])
        self.assertEqual([row["drawn"] for row in standings], [1, 1])

    def test_negative_score_is_rejected(self):
        match = self.make_match()

        with self.assertRaises(ValidationError):
            match.record_result(home_score=-1, away_score=0)

    def test_match_rejects_team_from_another_league(self):
        other_league = League.objects.create(
            organizer=self.organizer,
            name="Second League",
        )
        other_team = Team.objects.create(league=other_league, name="Other FC")
        match = Match(
            league=self.league,
            event=self.make_match_event("Invalid League Fixture"),
            home_team=other_team,
            away_team=self.away_team,
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_team_names_are_unique_within_league(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Team.objects.create(league=self.league, name=self.home_team.name)
