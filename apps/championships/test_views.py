from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Championship, Match, ResultAudit
from .services import generate_fixtures, publish, record_result
from .tests import ChampionshipTestMixin


class ChampionshipWorkflowTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_organizer_runs_a_championship_end_to_end(self):
        self.client.force_login(self.owner_user)
        response = self.client.post(
            reverse("championships:create"),
            {
                "name": "Spring Cup",
                "description": "Four teams, one trophy.",
                "format": Championship.Format.GROUPS_KNOCKOUT,
                "team_count": 4,
                "group_count": 1,
                "advance_per_group": 2,
                "group_legs": 1,
                "points_for_win": 3,
                "points_for_draw": 1,
                "points_for_loss": 0,
                "tie_breaker": Championship.TieBreaker.OVERALL_FIRST,
                "knockout_tie_rule": Championship.KnockoutTieRule.PENALTIES,
                "first_kickoff_at": "2026-11-14T09:30",
                "timezone": "Europe/Lisbon",
                "match_duration_minutes": 60,
                "break_between_matches_minutes": 10,
                "days_between_rounds": 0,
                "field_names": "North, South",
                "venue_name": "Parque Verde",
                "address_line": "Rua do Campo 1",
                "city": "Lisbon",
                "country_code": "pt",
                "latitude": "38.722252",
                "longitude": "-9.139337",
            },
        )
        cup = Championship.objects.get(name="Spring Cup")
        self.assertRedirects(response, reverse("championships:manage", args=[cup.pk]))
        self.assertEqual(cup.location.country_code, "PT")
        self.assertEqual(cup.first_kickoff_at.astimezone(cup.zone).hour, 9)
        self.assertEqual(cup.groups.count(), 1)

        response = self.client.post(
            reverse("championships:team_add", args=[cup.pk]),
            {"mode": "bulk", "names": "Lions\nTigers\nBears\nWolves"},
        )
        self.assertEqual(cup.teams.count(), 4)

        response = self.client.post(reverse("championships:fixtures_generate", args=[cup.pk]), follow=True)
        self.assertContains(response, "fixtures generated")
        self.assertEqual(cup.matches.filter(stage=Match.Stage.GROUP).count(), 6)
        self.assertEqual(cup.matches.filter(stage=Match.Stage.KNOCKOUT).count(), 1)

        response = self.client.post(reverse("championships:publish", args=[cup.pk]), follow=True)
        cup.refresh_from_db()
        self.assertEqual(cup.status, Championship.Status.PUBLISHED)

        for match in cup.matches.filter(stage=Match.Stage.GROUP).select_related("home_team", "away_team"):
            home_wins = match.home_team.name < match.away_team.name
            response = self.client.post(
                reverse("championships:match_result", args=[cup.pk, match.pk]),
                {
                    "home_score": "2" if home_wins else "0",
                    "away_score": "0" if home_wins else "1",
                    "incidents-TOTAL_FORMS": "3",
                    "incidents-INITIAL_FORMS": "0",
                    "incidents-MIN_NUM_FORMS": "0",
                    "incidents-MAX_NUM_FORMS": "40",
                    "incidents-0-side": "home",
                    "incidents-0-kind": "goal",
                    "incidents-0-player_name": "Ana" if home_wins else "",
                    "incidents-0-minute": "10" if home_wins else "",
                    "incidents-1-side": "home",
                    "incidents-1-kind": "goal",
                    "incidents-2-side": "home",
                    "incidents-2-kind": "goal",
                },
                follow=True,
            )
            self.assertContains(response, "Result saved")
        final = cup.matches.get(knockout_round=Match.Round.FINAL)
        self.assertEqual((final.home_team.name, final.away_team.name), ("Bears", "Lions"))

        public = self.client.get(reverse("championships:detail", args=[cup.pk]))
        self.assertContains(public, "Standings")
        self.assertContains(public, "Qualified")
        self.assertContains(public, "Eliminated")
        self.assertContains(public, "Ana")
        self.assertContains(public, "openstreetmap.org/export/embed.html")

        response = self.client.post(
            reverse("championships:match_result", args=[cup.pk, final.pk]),
            {"home_score": "1", "away_score": "1", "incidents-TOTAL_FORMS": "0", "incidents-INITIAL_FORMS": "0"},
        )
        self.assertContains(response, "Enter the penalty shoot-out result")
        response = self.client.post(
            reverse("championships:match_result", args=[cup.pk, final.pk]),
            {
                "home_score": "1",
                "away_score": "1",
                "home_penalties": "4",
                "away_penalties": "2",
                "incidents-TOTAL_FORMS": "0",
                "incidents-INITIAL_FORMS": "0",
            },
            follow=True,
        )
        self.assertContains(response, "Result saved")
        cup.refresh_from_db()
        self.assertEqual(cup.status, Championship.Status.COMPLETED)
        public = self.client.get(reverse("championships:detail", args=[cup.pk]))
        self.assertContains(public, "Champion")
        self.assertContains(public, "Bears win on penalties")

    def test_invalid_score_input_is_rejected_on_the_server(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        match = cup.matches.first()
        self.client.force_login(self.owner_user)
        for home, away in (("-1", "0"), ("abc", "1"), ("1.5", "0"), ("", "2")):
            with self.subTest(home=home):
                response = self.client.post(
                    reverse("championships:match_result", args=[cup.pk, match.pk]),
                    {"home_score": home, "away_score": away, "incidents-TOTAL_FORMS": "0", "incidents-INITIAL_FORMS": "0"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "has-error")
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.SCHEDULED)
        self.assertFalse(ResultAudit.objects.filter(match=match).exists())

    def test_downstream_change_asks_for_confirmation_before_clearing(self):
        cup = self.start(self.make_championship(["K1", "K2", "K3", "K4"], format=Championship.Format.KNOCKOUT))
        semi1, semi2 = cup.matches.filter(knockout_round=Match.Round.SEMI_FINAL).order_by("bracket_position")
        self.play(semi1, 1, 0)
        self.play(semi2, 1, 0)
        final = cup.matches.get(knockout_round=Match.Round.FINAL)
        self.play(final, 2, 0)
        self.client.force_login(self.owner_user)
        url = reverse("championships:match_result", args=[cup.pk, semi1.pk])
        data = {"home_score": "0", "away_score": "1", "incidents-TOTAL_FORMS": "0", "incidents-INITIAL_FORMS": "0"}

        response = self.client.post(url, data)
        self.assertContains(response, "already have results")
        self.assertContains(response, 'name="confirm_reset"')
        final.refresh_from_db()
        self.assertEqual(final.status, Match.Status.COMPLETED)

        response = self.client.post(url, {**data, "confirm_reset": "on"}, follow=True)
        self.assertContains(response, "were cleared")
        final.refresh_from_db()
        self.assertEqual(final.status, Match.Status.SCHEDULED)
        self.assertEqual(final.home_team.name, "K4")

    def test_postponing_and_cancelling_through_the_schedule_form(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        match = cup.matches.first()
        self.client.force_login(self.owner_user)
        url = reverse("championships:match_schedule", args=[cup.pk, match.pk])
        response = self.client.post(
            url, {"kickoff_at": "2026-11-21T12:00", "field_name": "Pitch 2", "status": "postponed", "note": "Waterlogged pitch"}
        )
        self.assertRedirects(response, reverse("championships:manage", args=[cup.pk]) + f"#match-{match.pk}")
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.POSTPONED)
        self.assertEqual(match.kickoff_at.astimezone(cup.zone).hour, 12)
        self.assertEqual(ResultAudit.objects.get(match=match).note, "Waterlogged pitch")

        public = self.client.get(reverse("championships:detail", args=[cup.pk]))
        self.assertContains(public, "Postponed")

        self.client.post(url, {"kickoff_at": "", "field_name": "", "status": "cancelled"})
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.CANCELLED)
        self.assertNotContains(
            self.client.get(reverse("championships:manage", args=[cup.pk])),
            reverse("championships:match_result", args=[cup.pk, match.pk]),
        )

    def test_drawing_of_lots_fills_the_bracket_slot(self):
        cup = self.start(
            self.make_championship(["Ash", "Birch", "Cedar", "Dale"], group_count=1, advance_per_group=2)
        )
        for match in cup.matches.filter(stage=Match.Stage.GROUP):
            self.play(match, 1, 1)
        final = cup.matches.get(knockout_round=Match.Round.FINAL)
        self.assertIsNone(final.home_team)
        self.client.force_login(self.owner_user)
        for rank, team in enumerate(cup.teams.order_by("name"), start=1):
            response = self.client.post(
                reverse("championships:team_update", args=[cup.pk, team.pk]),
                {"name": team.name, "short_name": "", "seed": team.seed or "", "lots_rank": rank},
            )
            self.assertEqual(response.status_code, 302)
        final.refresh_from_db()
        self.assertEqual((final.home_team.name, final.away_team.name), ("Ash", "Birch"))
        self.assertTrue(ResultAudit.objects.filter(championship=cup, action=ResultAudit.Action.TEAM_UPDATED).exists())

    def test_live_endpoint_version_changes_when_a_result_is_saved(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        url = reverse("championships:live", args=[cup.pk])
        before = self.client.get(url).json()["version"]
        self.play(cup.matches.first(), 1, 0)
        after = self.client.get(url).json()["version"]
        self.assertNotEqual(before, after)


class ChampionshipPermissionViewTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def setUp(self):
        self.cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        self.match = self.cup.matches.first()

    def result_post(self):
        return self.client.post(
            reverse("championships:match_result", args=[self.cup.pk, self.match.pk]),
            {"home_score": "9", "away_score": "0", "incidents-TOTAL_FORMS": "0", "incidents-INITIAL_FORMS": "0"},
        )

    def assert_unchanged(self):
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.SCHEDULED)
        self.assertIsNone(self.match.home_score)

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.result_post()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assert_unchanged()

    def test_other_user_cannot_change_results_schedule_or_settings(self):
        self.client.force_login(self.other_user)
        self.assertEqual(self.result_post().status_code, 404)
        self.assertEqual(self.client.get(reverse("championships:manage", args=[self.cup.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("championships:match_schedule", args=[self.cup.pk, self.match.pk]),
                {"status": "cancelled", "field_name": "", "kickoff_at": ""},
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post(reverse("championships:fixtures_clear", args=[self.cup.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("championships:update", args=[self.cup.pk])).status_code, 404)
        self.assert_unchanged()
        self.assertEqual(self.cup.matches.count(), 3)

    def test_participants_can_still_view_the_public_page(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("championships:detail", args=[self.cup.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Manage championship")
        self.assertNotContains(response, reverse("championships:match_result", args=[self.cup.pk, self.match.pk]))

    def test_administrator_can_correct_results(self):
        admin = get_user_model().objects.create_superuser(username="cup-admin", password="strong-password-123")
        self.client.force_login(admin)
        self.assertEqual(self.result_post().status_code, 302)
        self.match.refresh_from_db()
        self.assertEqual(self.match.home_score, 9)
        self.assertEqual(ResultAudit.objects.get(match=self.match).user, admin)

    def test_draft_championship_is_hidden_from_the_public(self):
        draft = self.make_championship(["Ash", "Birch"], name="Hidden Cup", format=Championship.Format.GROUPS, group_count=1)
        self.assertEqual(self.client.get(reverse("championships:detail", args=[draft.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("championships:list")), "Hidden Cup")
        self.client.force_login(self.owner_user)
        self.assertContains(self.client.get(reverse("championships:detail", args=[draft.pk])), "Draft preview")

    def test_get_requests_never_change_state(self):
        self.client.force_login(self.owner_user)
        for name in ("fixtures_generate", "fixtures_clear", "publish", "groups_distribute"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"championships:{name}", args=[self.cup.pk])).status_code, 405)
        self.assertEqual(self.cup.matches.count(), 3)


class ChampionshipMobileDisplayTests(ChampionshipTestMixin, TestCase):
    """Markup and stylesheet checks for small screens. Visual rendering itself is not exercised by Django tests."""

    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_standings_and_bracket_use_scrollable_mobile_layouts(self):
        cup = self.make_championship([f"T{n}" for n in range(1, 9)])
        generate_fixtures(cup, user=self.owner_user)
        publish(cup, user=self.owner_user)
        response = self.client.get(reverse("championships:detail", args=[cup.pk]))

        self.assertContains(response, 'name="viewport" content="width=device-width, initial-scale=1"')
        self.assertContains(response, 'class="table-wrap table-scroll" role="region" aria-label="Group A standings"')
        self.assertContains(response, 'class="col-team"')
        self.assertContains(response, 'class="col-optional"')
        self.assertContains(response, 'class="bracket-scroll" role="region" aria-label="Knockout bracket"')
        self.assertContains(response, 'style="--rounds: 2"')
        self.assertContains(response, 'role="tablist"')

        css = (settings.BASE_DIR / "static/gatherplay/css/gatherplay.css").read_text(encoding="utf-8")
        mobile = css[css.index("/* 16. Championships"):]
        mobile = mobile[mobile.index("@media (max-width: 640px)"):]
        self.assertNotIn("display: none", mobile[: mobile.index(".bracket-scroll")])  # every stat column stays visible
        self.assertIn("minmax(78vw, 1fr)", mobile)
        self.assertIn("position: sticky;", css[css.index(".table-cup .col-team {"):])

    def test_manage_page_renders_for_every_format(self):
        self.client.force_login(self.owner_user)
        for index, (fmt, names) in enumerate(
            (
                (Championship.Format.GROUPS, ["A", "B", "C"]),
                (Championship.Format.KNOCKOUT, ["A", "B", "C", "D"]),
                (Championship.Format.GROUPS_KNOCKOUT, [f"T{n}" for n in range(8)]),
            )
        ):
            cup = self.make_championship(names, name=f"Format {index}", format=fmt, group_count=1 if fmt == "groups" else 2)
            with self.subTest(format=fmt):
                self.assertEqual(self.client.get(reverse("championships:manage", args=[cup.pk])).status_code, 200)
                generate_fixtures(cup, user=self.owner_user)
                publish(cup, user=self.owner_user)
                match = cup.matches.exclude(home_team=None).first()
                record_result(match, user=self.owner_user, home_score=2, away_score=1)
                self.assertEqual(self.client.get(reverse("championships:manage", args=[cup.pk])).status_code, 200)
                self.assertEqual(self.client.get(reverse("championships:detail", args=[cup.pk])).status_code, 200)
                self.assertEqual(
                    self.client.get(reverse("championships:match_result", args=[cup.pk, match.pk])).status_code, 200
                )
                self.assertEqual(
                    self.client.get(reverse("championships:match_schedule", args=[cup.pk, match.pk])).status_code, 200
                )
        self.assertEqual(self.client.get(reverse("championships:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("championships:organizer_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("championships:create")).status_code, 200)
