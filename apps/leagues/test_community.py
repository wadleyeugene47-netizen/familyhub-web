from datetime import timedelta
from itertools import combinations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import EventCategory

from .models import League, LeagueChatMessage, Match, Team, TeamMembership
from .scheduling import round_robin_rounds

User = get_user_model()
PASSWORD = "A-strong-test-password-2048"
JSON = {"HTTP_ACCEPT": "application/json"}


class RoundRobinTests(TestCase):
    def test_every_pair_meets_once_per_cycle(self):
        for size in (2, 3, 4, 5, 6):
            teams = list(range(size))
            rounds = round_robin_rounds(teams)
            pairs = [frozenset(pair) for round_ in rounds for pair in round_]
            self.assertEqual(sorted(map(sorted, pairs)), sorted(map(sorted, map(frozenset, combinations(teams, 2)))))
            for round_ in rounds:
                playing = [team for pair in round_ for team in pair]
                self.assertEqual(len(playing), len(set(playing)), "a team plays twice in one round")


class HostLeagueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_superuser("host", "host@example.com", PASSWORD)
        cls.ana = User.objects.create_user("ana", "ana@example.com", PASSWORD, first_name="Ana")
        cls.bruno = User.objects.create_user("bruno", "bruno@example.com", PASSWORD, first_name="Bruno")
        cls.fan = User.objects.create_user("fan", "fan@example.com", PASSWORD, first_name="Fanny")
        cls.waiting = User.objects.create_user("waiting", password=PASSWORD, approval_status=User.Approval.PENDING)
        EventCategory.objects.get_or_create(slug="soccer-sports", defaults={"name": "Soccer & Sports"})

    def setUp(self):
        self.client.force_login(self.host)

    def create_league(self):
        self.client.post(reverse("hub:leagues"), {"name": "Brothers and Goals", "description": "Sunday games"})
        return League.objects.get(name="Brothers and Goals")

    def add_team(self, league, name, fee="0"):
        self.client.post(
            reverse("hub:league_team_add", args=[league.pk]),
            {"new-name": name, "new-short_name": "", "new-captain_fee": fee, "new-fee_currency": "usd"},
        )
        return league.teams.get(name=name)

    def round_data(self, **overrides):
        kickoff = timezone.localtime(timezone.now() + timedelta(days=3))
        data = {
            "first_kickoff": kickoff.strftime("%Y-%m-%dT%H:%M"),
            "minutes_per_game": 50,
            "timezone": "UTC",
            "venue_name": "Community Field",
            "address_line": "1 Goal Street",
            "city": "Boston",
            "country_code": "us",
        }
        data.update(overrides)
        return data

    def test_host_creates_brothers_and_goals_with_team_fees(self):
        self.assertContains(self.client.get(reverse("hub:leagues")), "Brothers and Goals")
        league = self.create_league()
        lions = self.add_team(league, "Lions", fee="120.00")

        self.assertEqual(league.organizer.user, self.host)
        self.assertEqual(str(lions.captain_fee), "120.00")
        self.assertEqual(lions.fee_currency, "USD")

        self.client.post(
            reverse("hub:league_team_edit", args=[league.pk, lions.pk]),
            {
                f"team-{lions.pk}-name": "Lions",
                f"team-{lions.pk}-short_name": "LIO",
                f"team-{lions.pk}-captain_fee": "150",
                f"team-{lions.pk}-fee_currency": "USD",
                f"team-{lions.pk}-fee_paid": "on",
            },
        )
        lions.refresh_from_db()
        self.assertEqual(str(lions.captain_fee), "150.00")
        self.assertTrue(lions.fee_paid)

    def test_only_approved_members_can_be_put_on_teams_and_captain_is_unique(self):
        league = self.create_league()
        lions = self.add_team(league, "Lions")
        form_page = self.client.get(reverse("hub:league", args=[league.pk]))
        self.assertContains(form_page, "Ana (@ana)")
        self.assertNotContains(form_page, "@waiting")

        self.client.post(reverse("hub:league_player_add", args=[league.pk]), {"user": self.ana.pk, "team": lions.pk, "role": "captain"})
        self.client.post(reverse("hub:league_player_add", args=[league.pk]), {"user": self.bruno.pk, "team": lions.pk, "role": "player"})
        self.assertEqual(lions.captain, self.ana)

        bruno = TeamMembership.objects.get(user=self.bruno)
        self.client.post(reverse("hub:league_player_captain", args=[league.pk, bruno.pk]))
        self.assertEqual(lions.captain, self.bruno)
        self.assertEqual(TeamMembership.objects.get(user=self.ana).role, TeamMembership.Role.PLAYER)

        response = self.client.post(reverse("hub:league_player_add", args=[league.pk]), {"user": self.waiting.pk, "team": lions.pk, "role": "player"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamMembership.objects.filter(user=self.waiting).exists())

    def test_rounds_generate_and_scores_build_the_table(self):
        league = self.create_league()
        for name in ("Lions", "Tigers", "Bears", "Wolves"):
            self.add_team(league, name)

        self.client.post(reverse("hub:league_round", args=[league.pk]), self.round_data())
        self.client.post(reverse("hub:league_round", args=[league.pk]), self.round_data())

        self.assertEqual(league.matches.filter(round_number=1).count(), 2)
        self.assertEqual(league.matches.filter(round_number=2).count(), 2)
        first_round = {frozenset((m.home_team_id, m.away_team_id)) for m in league.matches.filter(round_number=1)}
        second_round = {frozenset((m.home_team_id, m.away_team_id)) for m in league.matches.filter(round_number=2)}
        self.assertFalse(first_round & second_round, "round 2 repeats a round 1 game")
        league.refresh_from_db()
        self.assertEqual(league.home_venue.name, "Community Field")

        match = league.matches.filter(round_number=1).first()
        self.client.post(reverse("hub:league_score", args=[league.pk, match.pk]), {"home_score": 3, "away_score": 1})
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.COMPLETED)

        # Starting the league makes it public; the table shows the winner on top.
        self.client.post(reverse("hub:league_start", args=[league.pk]))
        response = self.client.get(reverse("leagues:detail", args=[league.pk]))
        standings = response.context["standings"]
        self.assertEqual(standings[0]["team"], match.home_team)
        self.assertEqual(standings[0]["points"], 3)

        # A typo can be corrected.
        self.client.post(reverse("hub:league_score", args=[league.pk, match.pk]), {"home_score": 1, "away_score": 1})
        standings = self.client.get(reverse("leagues:detail", args=[league.pk])).context["standings"]
        self.assertEqual(standings[0]["points"], 1)

    def test_member_sees_their_team_fee_and_supporters(self):
        league = self.create_league()
        lions = self.add_team(league, "Lions", fee="80")
        self.add_team(league, "Tigers")
        self.client.post(reverse("hub:league_player_add", args=[league.pk]), {"user": self.ana.pk, "team": lions.pk, "role": "captain", "jersey_number": 10})
        self.client.post(reverse("hub:league_start", args=[league.pk]))

        self.client.force_login(self.ana)
        response = self.client.get(reverse("leagues:detail", args=[league.pk]))
        self.assertContains(response, "You're playing")
        self.assertContains(response, "You pay 80.00 USD")
        self.assertEqual(response.context["my_membership"].team, lions)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "Captain · Lions")

        self.client.force_login(self.fan)
        response = self.client.get(reverse("leagues:detail", args=[league.pk]))
        self.assertContains(response, "supporter")
        self.assertIn(self.fan, list(response.context["supporters"]))
        self.assertNotIn(self.ana, list(response.context["supporters"]))

    def test_teams_and_chat_are_hidden_from_visitors(self):
        league = self.create_league()
        self.client.post(reverse("hub:league_start", args=[league.pk]))  # fails: no teams yet
        self.assertEqual(League.objects.get().status, League.Status.DRAFT)
        self.add_team(league, "Lions")
        self.add_team(league, "Tigers")
        self.client.post(reverse("hub:league_start", args=[league.pk]))
        self.client.logout()

        response = self.client.get(reverse("leagues:detail", args=[league.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("supporters", response.context)
        self.assertRedirects(
            self.client.get(reverse("leagues:chat", args=[league.pk])),
            f"{reverse('accounts:login')}?next={reverse('leagues:chat', args=[league.pk])}",
        )

    def test_group_chat_between_players_and_supporters(self):
        league = self.create_league()
        self.add_team(league, "Lions")
        self.add_team(league, "Tigers")
        self.client.post(reverse("hub:league_start", args=[league.pk]))

        self.client.force_login(self.fan)
        self.client.post(reverse("leagues:chat", args=[league.pk]), {"body": "Go Lions!"}, **JSON)

        self.client.force_login(self.ana)
        data = self.client.get(reverse("leagues:chat", args=[league.pk]), {"after": 0}).json()
        self.assertEqual(data["messages"][0]["body"], "Go Lions!")
        self.assertEqual(data["messages"][0]["sender"], "Fanny")
        self.assertFalse(data["messages"][0]["mine"])

        self.client.force_login(self.host)
        message = LeagueChatMessage.objects.get()
        self.client.post(reverse("hub:league_chat_delete", args=[league.pk, message.pk]))
        self.assertFalse(LeagueChatMessage.objects.exists())

    def test_draft_league_is_hidden_from_members(self):
        league = self.create_league()
        self.client.force_login(self.ana)

        self.assertEqual(self.client.get(reverse("leagues:detail", args=[league.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("leagues:chat", args=[league.pk])).status_code, 404)

    def test_members_cannot_use_host_league_pages(self):
        league = self.create_league()
        lions = self.add_team(league, "Lions")
        self.client.force_login(self.ana)

        self.assertEqual(self.client.get(reverse("hub:leagues")).status_code, 404)
        self.client.post(
            reverse("hub:league_team_edit", args=[league.pk, lions.pk]),
            {f"team-{lions.pk}-name": "Hacked", f"team-{lions.pk}-captain_fee": "0", f"team-{lions.pk}-fee_currency": "USD"},
        )
        self.assertEqual(Team.objects.get(pk=lions.pk).name, "Lions")

    def test_member_asks_to_play_and_host_adds_them_to_a_team(self):
        league = self.create_league()
        lions = self.add_team(league, "Lions")
        self.add_team(league, "Tigers")
        self.client.post(reverse("hub:league_start", args=[league.pk]))

        self.client.force_login(self.fan)
        self.assertContains(self.client.get(reverse("leagues:detail", args=[league.pk])), "I want to play")
        self.client.post(reverse("leagues:join", args=[league.pk]), {"note": "Goalkeeper"})
        self.client.post(reverse("leagues:join", args=[league.pk]), {"note": "again"})
        self.assertEqual(league.join_requests.count(), 1)
        self.assertContains(self.client.get(reverse("leagues:detail", args=[league.pk])), "You asked to play")

        self.client.force_login(self.host)
        self.assertContains(self.client.get(reverse("hub:league", args=[league.pk])), "Goalkeeper")
        join_request = league.join_requests.get()
        self.client.post(reverse("hub:league_join_decide", args=[league.pk, join_request.pk]), {"team": lions.pk, "role": "captain"})

        self.assertEqual(lions.captain, self.fan)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "accepted")
        self.client.force_login(self.fan)
        self.assertContains(self.client.get(reverse("leagues:detail", args=[league.pk])), "playing")

    def test_host_can_decline_a_request(self):
        league = self.create_league()
        self.add_team(league, "Lions")
        self.add_team(league, "Tigers")
        self.client.post(reverse("hub:league_start", args=[league.pk]))
        self.client.force_login(self.fan)
        self.client.post(reverse("leagues:join", args=[league.pk]))

        self.client.force_login(self.host)
        join_request = league.join_requests.get()
        self.client.post(reverse("hub:league_join_decide", args=[league.pk, join_request.pk]), {"decision": "decline"})

        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "declined")
        self.assertFalse(TeamMembership.objects.filter(user=self.fan).exists())

    def test_live_league_is_spotlighted_on_homepage(self):
        league = self.create_league()
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("home")), "Brothers and Goals")

        League.objects.filter(pk=league.pk).update(status=League.Status.ACTIVE)
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Brothers and Goals")
        self.assertContains(response, reverse("leagues:detail", args=[league.pk]))

    def test_every_member_finds_the_community_from_menu_and_dashboard(self):
        league = self.create_league()
        League.objects.filter(pk=league.pk).update(status=League.Status.ACTIVE)
        self.client.force_login(self.fan)

        dashboard = self.client.get(reverse("accounts:dashboard"))
        self.assertContains(dashboard, "Your community")
        self.assertContains(dashboard, "Supporter")
        self.assertContains(dashboard, reverse("leagues:detail", args=[league.pk]))
        self.assertContains(self.client.get(reverse("events:list")), ">Community</a>")
