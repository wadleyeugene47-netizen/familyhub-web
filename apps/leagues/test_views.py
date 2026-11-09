from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile

from .models import League, Match, Team


class LeagueWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner_user = User.objects.create_user(username="league-owner", password="strong-password-123")
        cls.other_user = User.objects.create_user(username="other-owner", password="strong-password-123")
        cls.owner = OrganizerProfile.objects.create(user=cls.owner_user, display_name="League Owner")
        cls.other_owner = OrganizerProfile.objects.create(user=cls.other_user, display_name="Other Owner")
        cls.league = League.objects.create(
            organizer=cls.owner,
            name="Town League",
            status=League.Status.DRAFT,
        )
        cls.home_team = Team.objects.create(league=cls.league, name="Harbor FC")
        cls.away_team = Team.objects.create(league=cls.league, name="Hill United")
        cls.category = EventCategory.objects.create(name="Match", slug="match")
        cls.location = EventLocation.objects.create(
            name="Community Ground",
            address_line="1 Field Road",
            city="Sampletown",
            country_code="GB",
        )

    def make_match_event(self, title):
        starts_at = timezone.now() + timedelta(days=12)
        return Event.objects.create(
            organizer=self.owner,
            category=self.category,
            location=self.location,
            title=title,
            description="Fixture event.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=80,
            price=Decimal("0.00"),
            currency="GBP",
            status=Event.Status.DRAFT,
        )

    def test_owner_can_create_league_and_add_team(self):
        self.client.force_login(self.owner_user)
        create_response = self.client.post(
            reverse("leagues:create"),
            {
                "name": "New League",
                "description": "Community play.",
                "format": "round_robin",
                "rules": "Fair play.",
                "starts_on": "2026-11-01",
                "ends_on": "2027-01-01",
            },
        )
        league = League.objects.get(name="New League")
        self.assertRedirects(create_response, reverse("leagues:manage", args=[league.pk]))

        team_response = self.client.post(
            reverse("leagues:team_create", args=[league.pk]),
            {"name": "Riverside", "short_name": "RIV"},
        )
        self.assertRedirects(team_response, reverse("leagues:manage", args=[league.pk]))
        self.assertTrue(league.teams.filter(name="Riverside").exists())

    def test_other_organizer_cannot_view_or_activate_league(self):
        self.client.force_login(self.other_user)

        manage_response = self.client.get(reverse("leagues:manage", args=[self.league.pk]))
        activate_response = self.client.post(reverse("leagues:activate", args=[self.league.pk]))

        self.assertEqual(manage_response.status_code, 404)
        self.assertEqual(activate_response.status_code, 404)
        self.league.refresh_from_db()
        self.assertEqual(self.league.status, League.Status.DRAFT)

    def test_activation_requires_two_teams_then_publishes_fixtures(self):
        self.client.force_login(self.owner_user)
        self.league.teams.filter(pk=self.away_team.pk).delete()

        response = self.client.post(reverse("leagues:activate", args=[self.league.pk]))
        self.assertRedirects(response, reverse("leagues:manage", args=[self.league.pk]))
        self.league.refresh_from_db()
        self.assertEqual(self.league.status, League.Status.DRAFT)

        away_team = Team.objects.create(league=self.league, name="Hill United")
        event = self.make_match_event("Harbor FC vs Hill United")
        match = Match.objects.create(
            league=self.league,
            event=event,
            home_team=self.home_team,
            away_team=away_team,
        )
        response = self.client.post(reverse("leagues:activate", args=[self.league.pk]))
        self.assertRedirects(response, reverse("leagues:manage", args=[self.league.pk]))
        self.league.refresh_from_db()
        event.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(self.league.status, League.Status.ACTIVE)
        self.assertEqual(event.status, Event.Status.PUBLISHED)
        self.assertEqual(match.status, Match.Status.SCHEDULED)

    def test_public_page_hides_draft_and_active_page_shows_standings_and_fixtures(self):
        event = self.make_match_event("Harbor FC vs Hill United")
        match = Match.objects.create(
            league=self.league,
            event=event,
            home_team=self.home_team,
            away_team=self.away_team,
        )
        draft_response = self.client.get(reverse("leagues:detail", args=[self.league.pk]))
        self.assertEqual(draft_response.status_code, 404)

        self.league.status = League.Status.ACTIVE
        self.league.save(update_fields=["status", "updated_at"])
        event.status = Event.Status.PUBLISHED
        event.save(update_fields=["status", "updated_at"])
        match.record_result(home_score=3, away_score=0)

        response = self.client.get(reverse("leagues:detail", args=[self.league.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Harbor FC vs Hill United")
        self.assertContains(response, "Standings")
        self.assertContains(response, "3")

    def test_result_submission_is_owner_scoped(self):
        self.league.status = League.Status.ACTIVE
        self.league.save(update_fields=["status", "updated_at"])
        event = self.make_match_event("Result Test Match")
        event.status = Event.Status.PUBLISHED
        event.save(update_fields=["status", "updated_at"])
        match = Match.objects.create(
            league=self.league,
            event=event,
            home_team=self.home_team,
            away_team=self.away_team,
        )
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse("leagues:match_result", args=[self.league.pk, match.pk]),
            {"home_score": "2", "away_score": "1"},
        )

        self.assertEqual(response.status_code, 404)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.SCHEDULED)
