import random
from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.communities.models import Community
from apps.organizers.models import OrganizerProfile

from . import services
from .balancer import Player, balance
from .models import AllStarGame, AllStarRegistration
from .services import AllStarError

User = get_user_model()


def totals(teams):
    return [sum(p.rating for p in team) for team in teams]


class BalancerTests(SimpleTestCase):
    def test_every_player_is_placed_exactly_once(self):
        players = [Player(i, (i % 5) + 1, "outfield") for i in range(23)]
        placed = [p.key for team in balance(players, 4) for p in team]
        self.assertEqual(sorted(placed), list(range(23)))

    def test_equal_ratings_are_split_evenly(self):
        players = [Player(i, r, "outfield") for i, r in enumerate([5, 5, 4, 4, 3, 3, 2, 2, 1, 1])]
        self.assertEqual(totals(balance(players, 2)), [15, 15])

    def test_goalkeepers_are_spread_before_anything_else(self):
        players = [Player(1, 5, "goalkeeper"), Player(2, 5, "goalkeeper")]
        players += [Player(i, 3, "outfield") for i in range(3, 13)]
        for team in balance(players, 2):
            self.assertEqual(sum(p.position == "goalkeeper" for p in team), 1)

    def test_the_same_players_give_the_same_teams(self):
        players = [Player(i, (i * 7) % 5 + 1, "outfield" if i % 6 else "goalkeeper") for i in range(30)]
        self.assertEqual(balance(players, 3), balance(list(reversed(players)), 3))

    def test_random_squads_stay_fair(self):
        rng = random.Random(2048)
        for _ in range(200):
            team_count = rng.randint(2, 8)
            size = rng.randint(team_count, 60)
            players = [
                Player(
                    i,
                    rng.randint(1, 5),
                    rng.choice(["goalkeeper", "outfield", "outfield", "outfield", "outfield"]),
                    rng.choice(["male", "female"]),
                )
                for i in range(size)
            ]
            teams = balance(players, team_count)
            sizes = [len(team) for team in teams]
            self.assertLessEqual(max(sizes) - min(sizes), 1)
            for position in ("goalkeeper", "outfield"):
                counts = Counter()
                for index, team in enumerate(teams):
                    counts[index] = sum(p.position == position for p in team)
                spread = max(counts.values()) - min(counts.values())
                # Outfield can trail by one more when the goalkeepers already filled a team's extra place.
                self.assertLessEqual(spread, 2 if position == "outfield" else 1)


class AllStarTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer_user = User.objects.create_user("org", "org@example.com", "x-Strong-pass-1")
        cls.other_user = User.objects.create_user("other", "other@example.com", "x-Strong-pass-1")
        profile = OrganizerProfile.objects.create(user=cls.organizer_user, display_name="Org")
        cls.community = Community.objects.create(organizer=profile, name="Brothers")
        cls.game = AllStarGame.objects.create(
            community=cls.community,
            title="Summer Stars",
            starts_at=timezone.now() + timedelta(days=10),
            status=AllStarGame.Status.OPEN,
        )
        cls.players = [
            User.objects.create_user(f"player{i}", f"player{i}@example.com", "x-Strong-pass-1") for i in range(6)
        ]

    def sign_up_everyone(self):
        for index, player in enumerate(self.players):
            position = "goalkeeper" if index < 2 else "outfield"
            services.register(self.game, player, position=position, gender="male")

    def rate_everyone(self):
        self.client.force_login(self.organizer_user)
        data = {
            f"rating_{registration.pk}": str((index % 5) + 1)
            for index, registration in enumerate(self.game.registrations.all())
        }
        self.client.post(reverse("allstars:ratings", args=[self.game.pk]), data)

    def post_status(self, action):
        return self.client.post(reverse("allstars:status", args=[self.game.pk]), {"action": action})


class RegistrationTests(AllStarTestBase):
    def test_player_can_register_and_withdraw(self):
        self.client.force_login(self.players[0])
        url = reverse("allstars:register", args=[self.game.pk])
        self.client.post(url, {"position": "outfield", "gender": "male"})
        registration = AllStarRegistration.objects.get(user=self.players[0])
        self.assertEqual(registration.status, AllStarRegistration.Status.ACTIVE)
        self.client.post(reverse("allstars:withdraw", args=[self.game.pk]))
        registration.refresh_from_db()
        self.assertEqual(registration.status, AllStarRegistration.Status.WITHDRAWN)

    def test_signed_out_visitors_are_sent_to_login(self):
        response = self.client.post(reverse("allstars:register", args=[self.game.pk]), {})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_past_deadline_refuses_registration(self):
        AllStarGame.objects.filter(pk=self.game.pk).update(registration_closes_at=timezone.now() - timedelta(hours=1))
        self.game.refresh_from_db()
        with self.assertRaises(AllStarError):
            services.register(self.game, self.players[0], position="outfield", gender="male")

    def test_game_limited_to_one_gender_refuses_others(self):
        AllStarGame.objects.filter(pk=self.game.pk).update(eligibility="female")
        self.client.force_login(self.players[0])
        response = self.client.post(
            reverse("allstars:register", args=[self.game.pk]), {"position": "outfield", "gender": "male"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AllStarRegistration.objects.exists())

    def test_removed_player_cannot_sign_back_up(self):
        registration = services.register(self.game, self.players[0], position="outfield", gender="male")
        services.remove_player(self.game, registration.pk)
        with self.assertRaises(AllStarError):
            services.register(self.game, self.players[0], position="outfield", gender="male")

    def test_draft_game_is_hidden_from_players(self):
        AllStarGame.objects.filter(pk=self.game.pk).update(status=AllStarGame.Status.DRAFT)
        self.client.force_login(self.players[0])
        self.assertEqual(self.client.get(reverse("allstars:detail", args=[self.game.pk])).status_code, 404)


class OrganizerFlowTests(AllStarTestBase):
    def test_full_flow_from_sign_up_to_announced_teams(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.assertFalse(self.game.registrations.filter(team__isnull=True).exists())
        for team in self.game.teams.all():
            self.assertEqual(team.registrations.count(), 3)
            self.assertEqual(team.registrations.filter(position="goalkeeper").count(), 1)
        self.post_status("publish")
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, AllStarGame.Status.PUBLISHED)

        self.client.force_login(self.players[0])
        page = self.client.get(reverse("allstars:detail", args=[self.game.pk]))
        self.assertContains(page, "Team A")
        self.assertContains(page, "player1")

    def test_ratings_never_reach_players(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.post_status("publish")
        # A separate browser, so the organizer's "Ratings saved" toast can't appear here.
        player_browser = Client()
        player_browser.force_login(self.players[0])
        for url in (reverse("allstars:detail", args=[self.game.pk]), reverse("accounts:dashboard")):
            page = player_browser.get(url)
            self.assertNotContains(page, "rating")
            self.assertNotContains(page, "Rating")

    def test_signed_out_visitors_do_not_see_rosters(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.post_status("publish")
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("allstars:detail", args=[self.game.pk])), "player1")

    def test_balance_refuses_until_everyone_is_rated(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.assertTrue(self.game.registrations.filter(team__isnull=True).exists())

    def test_balance_needs_registration_closed(self):
        self.sign_up_everyone()
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.assertFalse(self.game.teams.exists())

    def test_publish_refused_when_a_late_player_has_no_team(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.post_status("open")
        late = User.objects.create_user("late", "late@example.com", "x-Strong-pass-1")
        self.game.refresh_from_db()
        services.register(self.game, late, position="outfield", gender="male")
        self.post_status("close")
        self.post_status("publish")
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, AllStarGame.Status.CLOSED)

    def test_cannot_move_a_player_to_another_games_team(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        other_game = AllStarGame.objects.create(
            community=self.community, title="Winter", starts_at=timezone.now(), status=AllStarGame.Status.CLOSED
        )
        foreign_team = other_game.teams.create(name="Foreign", order=0)
        registration = self.game.registrations.first()
        original_team = registration.team_id
        self.client.post(
            reverse("allstars:player_move", args=[self.game.pk, registration.pk]), {"team": foreign_team.pk}
        )
        registration.refresh_from_db()
        self.assertEqual(registration.team_id, original_team)

    def test_other_members_cannot_use_organizer_pages(self):
        self.sign_up_everyone()
        self.client.force_login(self.other_user)
        for name in ("manage", "update"):
            self.assertEqual(self.client.get(reverse(f"allstars:{name}", args=[self.game.pk])).status_code, 404)
        for name in ("balance", "status", "ratings"):
            self.assertEqual(self.client.post(reverse(f"allstars:{name}", args=[self.game.pk])).status_code, 404)

    def test_create_form_offers_only_own_communities(self):
        other_profile = OrganizerProfile.objects.create(user=self.other_user, display_name="Other")
        foreign = Community.objects.create(organizer=other_profile, name="Foreign")
        self.client.force_login(self.organizer_user)
        data = {
            "community": foreign.pk,
            "title": "Sneaky",
            "starts_at": "2030-01-01T18:00",
            "timezone": "UTC",
            "team_count": 2,
            "eligibility": "mixed",
        }
        self.client.post(reverse("allstars:create"), data)
        self.assertFalse(AllStarGame.objects.filter(title="Sneaky").exists())
        data["community"] = self.community.pk
        self.client.post(reverse("allstars:create"), data)
        self.assertTrue(AllStarGame.objects.filter(title="Sneaky").exists())

    def test_times_are_saved_in_the_games_timezone(self):
        self.client.force_login(self.organizer_user)
        self.client.post(
            reverse("allstars:create"),
            {
                "community": self.community.pk,
                "title": "Local",
                "starts_at": "2030-01-01T18:00",
                "timezone": "America/New_York",
                "team_count": 2,
                "eligibility": "mixed",
            },
        )
        game = AllStarGame.objects.get(title="Local")
        self.assertEqual(game.starts_at.astimezone(timezone.get_fixed_timezone(0)).hour, 23)

    def test_player_dashboard_hides_team_until_announced(self):
        self.sign_up_everyone()
        self.client.force_login(self.organizer_user)
        self.post_status("close")
        self.rate_everyone()
        self.client.post(reverse("allstars:balance", args=[self.game.pk]))
        self.client.force_login(self.players[0])
        dashboard = reverse("accounts:dashboard")
        self.assertNotContains(self.client.get(dashboard), "Team A")
        self.assertNotContains(self.client.get(dashboard), "Team B")
        self.client.force_login(self.organizer_user)
        self.post_status("publish")
        self.client.force_login(self.players[0])
        page = self.client.get(dashboard).content.decode()
        self.assertTrue("Team A" in page or "Team B" in page)
