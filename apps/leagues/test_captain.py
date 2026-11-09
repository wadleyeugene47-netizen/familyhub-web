from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.organizers.models import OrganizerProfile

from .models import League, LeagueJoinRequest, Team, TeamMembership
from .services import add_player

User = get_user_model()


class CaptainDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        host = User.objects.create_user("host", "host@example.com", "x-Strong-pass-1")
        profile = OrganizerProfile.objects.create(user=host, display_name="Host")
        cls.league = League.objects.create(organizer=profile, name="Sunday", status=League.Status.ACTIVE)
        cls.lions = Team.objects.create(league=cls.league, name="Lions", captain_fee=120, fee_paid=False)
        cls.tigers = Team.objects.create(league=cls.league, name="Tigers")
        cls.captain = User.objects.create_user("cap", "cap@example.com", "x-Strong-pass-1")
        cls.player = User.objects.create_user("pla", "pla@example.com", "x-Strong-pass-1")
        cls.rival = User.objects.create_user("riv", "riv@example.com", "x-Strong-pass-1")
        cls.captain_row = add_player(team=cls.lions, user=cls.captain, role=TeamMembership.Role.CAPTAIN, jersey_number=9)
        cls.player_row = add_player(team=cls.lions, user=cls.player, role=TeamMembership.Role.PLAYER, jersey_number=7)
        cls.rival_row = add_player(team=cls.tigers, user=cls.rival, role=TeamMembership.Role.CAPTAIN)

    def post_numbers(self, team, numbers):
        return self.client.post(
            reverse("leagues:captain_roster", args=[team.pk]),
            {f"jersey_{pk}": value for pk, value in numbers.items()},
        )

    def test_captain_sees_team_squad_and_fee(self):
        self.client.force_login(self.captain)
        page = self.client.get(reverse("leagues:captain"))
        self.assertContains(page, "Lions")
        self.assertContains(page, "pla")
        self.assertContains(page, "120")
        self.assertContains(page, "team fee not paid yet")
        self.assertNotContains(page, "Tigers")

    def test_plain_player_gets_the_empty_page_and_no_menu_link(self):
        self.client.force_login(self.player)
        page = self.client.get(reverse("leagues:captain"))
        self.assertContains(page, "You aren't a team captain yet")
        self.assertNotContains(page, "Captain dashboard</a>")

    def test_captain_gets_the_menu_link(self):
        self.client.force_login(self.captain)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), reverse("leagues:captain"))

    def test_captain_can_swap_shirt_numbers(self):
        self.client.force_login(self.captain)
        self.post_numbers(self.lions, {self.captain_row.pk: "7", self.player_row.pk: "9"})
        self.captain_row.refresh_from_db()
        self.player_row.refresh_from_db()
        self.assertEqual((self.captain_row.jersey_number, self.player_row.jersey_number), (7, 9))

    def test_duplicate_numbers_are_refused_and_nothing_changes(self):
        self.client.force_login(self.captain)
        self.post_numbers(self.lions, {self.captain_row.pk: "5", self.player_row.pk: "5"})
        self.captain_row.refresh_from_db()
        self.assertEqual(self.captain_row.jersey_number, 9)

    def test_out_of_range_and_junk_numbers_are_refused(self):
        self.client.force_login(self.captain)
        for bad in ("100", "-1", "ten"):
            self.post_numbers(self.lions, {self.captain_row.pk: bad})
            self.captain_row.refresh_from_db()
            self.assertEqual(self.captain_row.jersey_number, 9)

    def test_captain_cannot_touch_another_team(self):
        self.client.force_login(self.captain)
        response = self.post_numbers(self.tigers, {self.rival_row.pk: "3"})
        self.assertEqual(response.status_code, 404)

    def test_captain_cannot_smuggle_in_another_teams_player(self):
        self.client.force_login(self.captain)
        self.post_numbers(self.lions, {self.rival_row.pk: "3"})
        self.rival_row.refresh_from_db()
        self.assertIsNone(self.rival_row.jersey_number)

    def test_plain_player_cannot_change_numbers(self):
        self.client.force_login(self.player)
        response = self.post_numbers(self.lions, {self.player_row.pk: "10"})
        self.assertEqual(response.status_code, 404)

    def add(self, team, user, jersey=""):
        return self.client.post(
            reverse("leagues:captain_player_add", args=[team.pk]),
            {f"add-{team.pk}-user": user.pk, f"add-{team.pk}-jersey_number": jersey},
        )

    def remove(self, team, membership):
        return self.client.post(reverse("leagues:captain_player_remove", args=[team.pk, membership.pk]))

    def edit(self, team, **data):
        return self.client.post(
            reverse("leagues:captain_team_edit", args=[team.pk]),
            {f"team-{team.pk}-{key}": value for key, value in data.items()},
        )

    def free_agent(self, name="free"):
        return User.objects.create_user(name, f"{name}@example.com", "x-Strong-pass-1")

    def active_on(self, team, user):
        return team.memberships.filter(user=user, status=TeamMembership.Status.ACTIVE).exists()

    def test_captain_adds_a_member_as_a_plain_player(self):
        new = self.free_agent()
        self.client.force_login(self.captain)
        self.add(self.lions, new, "11")
        membership = self.lions.memberships.get(user=new)
        self.assertEqual(membership.status, TeamMembership.Status.ACTIVE)
        self.assertEqual(membership.role, TeamMembership.Role.PLAYER)
        self.assertEqual(membership.jersey_number, 11)

    def test_cannot_add_someone_already_on_a_team_in_this_league(self):
        self.client.force_login(self.captain)
        self.add(self.lions, self.rival)
        self.assertFalse(self.active_on(self.lions, self.rival))

    def test_cannot_add_a_member_waiting_for_approval(self):
        waiting = User.objects.create_user("wait", password="x-Strong-pass-1", approval_status=User.Approval.PENDING)
        self.client.force_login(self.captain)
        self.add(self.lions, waiting)
        self.assertFalse(self.active_on(self.lions, waiting))

    def test_taken_shirt_number_refuses_the_add(self):
        new = self.free_agent()
        self.client.force_login(self.captain)
        self.add(self.lions, new, "7")
        self.assertFalse(self.active_on(self.lions, new))

    def test_adding_answers_their_request_to_play(self):
        new = self.free_agent()
        join_request = self.league.join_requests.create(user=new)
        self.client.force_login(self.captain)
        self.assertContains(self.client.get(reverse("leagues:captain")), "Add to my team")
        self.add(self.lions, new)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, LeagueJoinRequest.Status.ACCEPTED)
        self.assertIsNotNone(join_request.decided_at)

    def test_captain_removes_a_player_who_can_then_be_added_back(self):
        self.client.force_login(self.captain)
        self.remove(self.lions, self.player_row)
        self.assertFalse(self.active_on(self.lions, self.player))
        self.add(self.lions, self.player)
        self.assertTrue(self.active_on(self.lions, self.player))

    def test_captain_cannot_remove_themselves(self):
        self.client.force_login(self.captain)
        self.remove(self.lions, self.captain_row)
        self.assertTrue(self.active_on(self.lions, self.captain))

    def test_captain_cannot_remove_another_teams_player(self):
        self.client.force_login(self.captain)
        self.remove(self.lions, self.rival_row)
        self.assertEqual(self.remove(self.tigers, self.rival_row).status_code, 404)
        self.assertTrue(self.active_on(self.tigers, self.rival))

    def test_plain_player_cannot_add_remove_or_edit(self):
        new = self.free_agent()
        self.client.force_login(self.player)
        self.assertEqual(self.add(self.lions, new).status_code, 404)
        self.assertEqual(self.remove(self.lions, self.captain_row).status_code, 404)
        self.assertEqual(self.edit(self.lions, name="Hacked").status_code, 404)
        self.lions.refresh_from_db()
        self.assertEqual(self.lions.name, "Lions")

    def test_captain_edits_the_team(self):
        self.client.force_login(self.captain)
        self.edit(self.lions, name="Golden Lions", short_name="GL")
        self.lions.refresh_from_db()
        self.assertEqual((self.lions.name, self.lions.short_name), ("Golden Lions", "GL"))

    def test_captain_cannot_change_the_fee_or_paid_mark(self):
        self.client.force_login(self.captain)
        self.edit(self.lions, name="Lions", captain_fee="0", fee_paid="on")
        self.lions.refresh_from_db()
        self.assertEqual(self.lions.captain_fee, 120)
        self.assertFalse(self.lions.fee_paid)

    def test_team_name_must_be_unique_in_the_league(self):
        self.client.force_login(self.captain)
        self.edit(self.lions, name="tigers")
        self.lions.refresh_from_db()
        self.assertEqual(self.lions.name, "Lions")

    def test_finished_league_is_read_only(self):
        League.objects.filter(pk=self.league.pk).update(status=League.Status.COMPLETED)
        new = self.free_agent()
        self.client.force_login(self.captain)
        self.assertEqual(self.add(self.lions, new).status_code, 404)
        self.assertEqual(self.edit(self.lions, name="Late").status_code, 404)
        self.assertNotContains(self.client.get(reverse("leagues:captain")), "Add player")

    def test_draft_league_stays_hidden_from_its_captain(self):
        League.objects.filter(pk=self.league.pk).update(status=League.Status.DRAFT)
        self.client.force_login(self.captain)
        self.assertContains(self.client.get(reverse("leagues:captain")), "You aren't a team captain yet")
