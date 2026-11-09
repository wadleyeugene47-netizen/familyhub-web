from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.events.models import EventLocation
from apps.organizers.models import OrganizerProfile

from .models import Championship, Match, ResultAudit, Team
from .scheduling import bracket_order, configuration_errors, round_robin
from .services import (
    ProgressionConflict,
    clear_result,
    generate_fixtures,
    publish,
    record_result,
    update_schedule,
)
from .standings import StandingRow, championship_tables, tie_breaker_steps


class ChampionshipTestMixin:
    @classmethod
    def make_users(cls):
        User = get_user_model()
        cls.owner_user = User.objects.create_user(username="cup-owner", password="strong-password-123")
        cls.other_user = User.objects.create_user(username="cup-visitor", password="strong-password-123")
        cls.organizer = OrganizerProfile.objects.create(user=cls.owner_user, display_name="Cup Organizer")
        cls.location = EventLocation.objects.create(
            name="Riverside Park",
            address_line="1 Field Road",
            city="Sampletown",
            country_code="GB",
            latitude="51.500000",
            longitude="-0.120000",
        )

    def make_championship(self, team_names, **options):
        settings = {
            "organizer": self.organizer,
            "name": options.pop("name", "Autumn Cup"),
            "format": Championship.Format.GROUPS_KNOCKOUT,
            "team_count": len(team_names),
            "group_count": 2,
            "advance_per_group": 2,
            "first_kickoff_at": datetime(2026, 11, 7, 10, 0, tzinfo=ZoneInfo("Europe/London")),
            "timezone": "Europe/London",
            "field_names": "Pitch 1, Pitch 2",
            "location": self.location,
        }
        settings.update(options)
        championship = Championship.objects.create(**settings)
        for index, name in enumerate(team_names, start=1):
            Team.objects.create(championship=championship, name=name, seed=index)
        return championship

    def start(self, championship):
        generate_fixtures(championship, user=self.owner_user)
        publish(championship, user=self.owner_user)
        championship.refresh_from_db()
        return championship

    def fixture(self, championship, home, away):
        return Match.objects.get(championship=championship, home_team__name=home, away_team__name=away)

    def play(self, match, home_score, away_score, **extra):
        match, _reset = record_result(
            match, user=self.owner_user, home_score=home_score, away_score=away_score, **extra
        )
        return match

    def table(self, championship, group_index=0):
        return championship_tables(championship)[group_index]

    def row(self, table, name):
        return next(row for row in table.rows if row.team.name == name)


class StandingsTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def setUp(self):
        self.cup = self.start(
            self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1)
        )

    def test_win_draw_and_loss_are_counted(self):
        self.result("Ash", "Birch", 2, 0)
        self.result("Ash", "Cedar", 1, 1)
        table = self.table(self.cup)

        ash = self.row(table, "Ash")
        birch = self.row(table, "Birch")
        cedar = self.row(table, "Cedar")
        self.assertEqual((ash.played, ash.won, ash.drawn, ash.lost), (2, 1, 1, 0))
        self.assertEqual((birch.played, birch.won, birch.drawn, birch.lost), (1, 0, 0, 1))
        self.assertEqual((cedar.played, cedar.drawn), (1, 1))

    def test_points_and_goal_difference(self):
        self.result("Ash", "Birch", 4, 1)
        self.result("Ash", "Cedar", 1, 1)
        table = self.table(self.cup)

        ash = self.row(table, "Ash")
        self.assertEqual(ash.points, 4)
        self.assertEqual((ash.goals_for, ash.goals_against, ash.goal_difference), (5, 2, 3))
        self.assertEqual(self.row(table, "Birch").goal_difference, -3)
        self.assertEqual(self.row(table, "Birch").points, 0)
        self.assertEqual(self.row(table, "Cedar").points, 1)
        self.assertEqual(table.rows[0].team.name, "Ash")

    def test_custom_points_system_is_applied(self):
        self.cup.points_for_win = 2
        self.cup.save()
        self.result("Ash", "Birch", 1, 0)
        self.assertEqual(self.row(self.table(self.cup), "Ash").points, 2)

    def test_tied_teams_are_separated_by_goal_difference_then_goals(self):
        self.result("Ash", "Birch", 3, 0)
        self.result("Birch", "Cedar", 2, 0)
        self.result("Ash", "Cedar", 0, 1)
        table = self.table(self.cup)

        self.assertEqual([row.points for row in table.rows], [3, 3, 3])
        # Goal differences: Ash +2, Birch -1, Cedar -1; Birch scored 2 and Cedar 1.
        self.assertEqual([row.team.name for row in table.rows], ["Ash", "Birch", "Cedar"])
        self.assertFalse(any(row.tied for row in table.rows))

    def test_head_to_head_first_rule_changes_the_order(self):
        cup = self.start(
            self.make_championship(
                ["Dart", "Elm", "Fir", "Gale"],
                name="H2H Cup",
                format=Championship.Format.GROUPS,
                group_count=1,
                tie_breaker=Championship.TieBreaker.HEAD_TO_HEAD_FIRST,
            )
        )
        # Dart and Elm finish on 6 points; Elm won their meeting, Dart has the better goal difference.
        self.result("Dart", "Elm", 0, 1, championship=cup)
        self.result("Dart", "Fir", 5, 0, championship=cup)
        self.result("Dart", "Gale", 5, 0, championship=cup)
        self.result("Elm", "Fir", 0, 1, championship=cup)
        self.result("Elm", "Gale", 1, 0, championship=cup)
        self.result("Fir", "Gale", 0, 1, championship=cup)
        names = [row.team.name for row in self.table(cup).rows][:2]
        self.assertEqual(names, ["Elm", "Dart"])

        cup.tie_breaker = Championship.TieBreaker.OVERALL_FIRST
        cup.save()
        names = [row.team.name for row in self.table(cup).rows][:2]
        self.assertEqual(names, ["Dart", "Elm"])

    def test_completely_level_teams_are_flagged_until_lots_are_drawn(self):
        self.result("Ash", "Birch", 1, 1)
        self.result("Ash", "Cedar", 0, 0)
        self.result("Birch", "Cedar", 0, 0)
        table = self.table(self.cup)
        self.assertTrue(all(row.tied for row in table.rows if row.team.name in ("Ash", "Birch")))
        self.assertIsNone(table.team_at(1))

        Team.objects.filter(championship=self.cup, name="Birch").update(lots_rank=1)
        Team.objects.filter(championship=self.cup, name="Ash").update(lots_rank=2)
        table = self.table(self.cup)
        self.assertEqual(table.team_at(1).name, "Birch")
        self.assertFalse(any(row.tied for row in table.rows))

    def test_tie_breaker_explanation_lists_every_step(self):
        steps = tie_breaker_steps(self.cup)
        self.assertEqual(steps[0], "Points")
        self.assertIn("Drawing of lots", steps[-1])

    def test_correcting_a_score_recalculates_standings(self):
        match = self.result("Ash", "Birch", 3, 0)
        self.result("Ash", "Birch", 0, 2, note="Scores were entered the wrong way round")
        table = self.table(self.cup)

        ash = self.row(table, "Ash")
        birch = self.row(table, "Birch")
        self.assertEqual((ash.points, ash.goals_for, ash.goals_against, ash.lost), (0, 0, 2, 1))
        self.assertEqual((birch.points, birch.won, birch.goal_difference), (3, 1, 2))
        self.assertEqual(ash.played, 1)
        entry = ResultAudit.objects.get(match=match, action=ResultAudit.Action.RESULT_CORRECTED)
        self.assertEqual(entry.user, self.owner_user)
        ash_side = "home" if match.home_team.name == "Ash" else "away"
        self.assertEqual((entry.before[f"{ash_side}_score"], entry.after[f"{ash_side}_score"]), (3, 0))
        self.assertEqual(entry.note, "Scores were entered the wrong way round")

    def test_clearing_a_result_removes_it_from_standings(self):
        match = self.result("Ash", "Birch", 3, 0)
        clear_result(match, user=self.owner_user)
        ash = self.row(self.table(self.cup), "Ash")
        self.assertEqual((ash.played, ash.points, ash.goals_for), (0, 0, 0))
        self.assertTrue(ResultAudit.objects.filter(match=match, action=ResultAudit.Action.RESULT_CLEARED).exists())

    def test_negative_or_non_integer_scores_are_rejected(self):
        match = self.fixture_between("Ash", "Birch")
        for home, away in ((-1, 0), (1.5, 0), ("2", 0), (True, 0)):
            with self.subTest(home=home), self.assertRaises(ValidationError):
                record_result(match, user=self.owner_user, home_score=home, away_score=away)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.SCHEDULED)

    def test_goal_details_are_optional_but_cannot_exceed_score(self):
        match = self.fixture_between("Ash", "Birch")
        home_id = match.home_team_id
        self.play(match, 1, 0, incidents=[{"team_id": home_id, "kind": "goal", "player_name": "Sam", "minute": 12}])
        self.assertEqual(match.incidents.count(), 1)
        with self.assertRaises(ValidationError):
            self.play(match, 1, 0, incidents=[{"team_id": home_id, "kind": "goal"}] * 2)
        self.assertEqual(match.incidents.get().player_name, "Sam")

    def result(self, first, second, first_score, second_score, championship=None, **extra):
        match = self.fixture_between(first, second, championship)
        if match.home_team.name == first:
            return self.play(match, first_score, second_score, **extra)
        return self.play(match, second_score, first_score, **extra)

    def fixture_between(self, first, second, championship=None):
        championship = championship or self.cup
        return Match.objects.get(
            championship=championship,
            home_team__name__in=[first, second],
            away_team__name__in=[first, second],
        )


class PostponedAndCancelledTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_postponed_match_keeps_group_open_and_cancelled_match_is_excluded(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        matches = list(cup.matches.order_by("pk"))
        self.play(matches[0], 1, 0)
        self.play(matches[1], 2, 2)
        update_schedule(
            matches[2], user=self.owner_user, kickoff_at=None, field_name="Pitch 1", status=Match.Status.POSTPONED
        )
        table = self.table(cup)
        self.assertFalse(table.is_complete)
        self.assertEqual(table.pending_count, 1)
        cup.refresh_from_db()
        self.assertEqual(cup.status, Championship.Status.PUBLISHED)

        update_schedule(
            matches[2], user=self.owner_user, kickoff_at=None, field_name="Pitch 1", status=Match.Status.CANCELLED
        )
        table = self.table(cup)
        self.assertTrue(table.is_complete)
        self.assertEqual(sum(row.played for row in table.rows), 4)
        self.assertTrue(
            ResultAudit.objects.filter(match=matches[2], action=ResultAudit.Action.STATUS_CHANGED).count() == 2
        )
        cup.refresh_from_db()
        self.assertEqual(cup.status, Championship.Status.COMPLETED)

    def test_cancelled_match_cannot_take_a_result_and_knockout_cannot_be_cancelled(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        match = cup.matches.first()
        update_schedule(match, user=self.owner_user, kickoff_at=None, field_name="", status=Match.Status.CANCELLED)
        with self.assertRaises(ValidationError):
            record_result(match, user=self.owner_user, home_score=1, away_score=0)

        knockout = self.start(
            self.make_championship(["K1", "K2", "K3", "K4"], name="KO", format=Championship.Format.KNOCKOUT)
        )
        semi = knockout.matches.filter(knockout_round=Match.Round.SEMI_FINAL).first()
        with self.assertRaises(ValidationError):
            update_schedule(semi, user=self.owner_user, kickoff_at=None, field_name="", status=Match.Status.CANCELLED)


class FixtureGenerationTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_round_robin_pairs_every_team_once_and_never_itself(self):
        for size in range(2, 9):
            teams = list(range(size))
            rounds = round_robin(teams)
            pairs = [frozenset(pair) for matches in rounds for pair in matches]
            with self.subTest(size=size):
                self.assertEqual(len(pairs), size * (size - 1) // 2)
                self.assertEqual(len(set(pairs)), len(pairs))
                self.assertTrue(all(len(pair) == 2 for pair in pairs))
                for matches in rounds:
                    playing = [team for pair in matches for team in pair]
                    self.assertEqual(len(playing), len(set(playing)))

    def test_bracket_order_keeps_top_seeds_apart(self):
        self.assertEqual(bracket_order(4), [1, 4, 2, 3])
        self.assertEqual(bracket_order(8), [1, 8, 4, 5, 2, 7, 3, 6])

    def test_groups_are_distributed_and_fixtures_scheduled(self):
        cup = self.make_championship([f"Team {n}" for n in range(1, 9)])
        generate_fixtures(cup, user=self.owner_user)

        groups = list(cup.groups.all())
        self.assertEqual([group.name for group in groups], ["Group A", "Group B"])
        self.assertEqual([group.teams.count() for group in groups], [4, 4])
        group_matches = cup.matches.filter(stage=Match.Stage.GROUP)
        self.assertEqual(group_matches.count(), 12)
        for match in group_matches:
            self.assertNotEqual(match.home_team_id, match.away_team_id)
            self.assertEqual(match.home_team.group_id, match.group_id)
            self.assertEqual(match.away_team.group_id, match.group_id)
            self.assertIsNotNone(match.kickoff_at)
            self.assertIn(match.field_name, ["Pitch 1", "Pitch 2"])
        self.assertEqual(group_matches.filter(round_number=1).first().kickoff_at.astimezone(cup.zone).hour, 10)

    def test_duplicate_group_fixture_is_rejected_by_the_database(self):
        cup = self.make_championship([f"Team {n}" for n in range(1, 5)], group_count=1, advance_per_group=2)
        generate_fixtures(cup, user=self.owner_user)
        existing = cup.matches.filter(stage=Match.Stage.GROUP).first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Match.objects.create(
                championship=cup,
                stage=Match.Stage.GROUP,
                group=existing.group,
                home_team=existing.home_team,
                away_team=existing.away_team,
            )

    def test_team_cannot_play_itself(self):
        cup = self.make_championship([f"Team {n}" for n in range(1, 5)], group_count=1, advance_per_group=2)
        generate_fixtures(cup, user=self.owner_user)
        match = cup.matches.filter(stage=Match.Stage.GROUP).first()
        match.away_team = match.home_team
        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_invalid_configurations_are_reported(self):
        cases = [
            ({"format": Championship.Format.KNOCKOUT, "team_count": 6}, "2, 4, 8"),
            ({"team_count": 6, "group_count": 2, "advance_per_group": 3}, "fewer than 3"),
            ({"team_count": 12, "group_count": 3, "advance_per_group": 2}, "= 6 teams"),
            ({"team_count": 3, "group_count": 2}, "at most 1 group"),
        ]
        for options, message in cases:
            championship = Championship(
                format=options.pop("format", Championship.Format.GROUPS_KNOCKOUT), **options
            )
            with self.subTest(options=options):
                self.assertTrue(any(message in error for error in configuration_errors(championship)))

    def test_generation_requires_all_teams(self):
        cup = self.make_championship(["Ash", "Birch", "Cedar"], team_count=8)
        with self.assertRaises(ValidationError):
            generate_fixtures(cup, user=self.owner_user)
        self.assertFalse(cup.matches.exists())

    def test_knockout_bracket_from_groups_uses_group_positions(self):
        cup = self.make_championship([f"Team {n}" for n in range(1, 9)], third_place_match=True)
        generate_fixtures(cup, user=self.owner_user)
        semis = list(cup.matches.filter(knockout_round=Match.Round.SEMI_FINAL).order_by("bracket_position"))
        labels = [(match.home_label, match.away_label) for match in semis]
        self.assertEqual(labels, [("Group A · 1st", "Group B · 2nd"), ("Group B · 1st", "Group A · 2nd")])
        self.assertTrue(all(match.home_team is None and match.away_team is None for match in semis))
        final = cup.matches.get(knockout_round=Match.Round.FINAL)
        third = cup.matches.get(knockout_round=Match.Round.THIRD_PLACE)
        self.assertEqual(final.home_label, "Winner of Semi-final 1")
        self.assertEqual(third.away_label, "Loser of Semi-final 2")
        self.assertLess(third.kickoff_at, final.kickoff_at)

    def test_regenerating_is_blocked_once_results_exist(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        self.play(cup.matches.first(), 1, 0)
        with self.assertRaises(ValidationError):
            generate_fixtures(cup, user=self.owner_user)


class QualificationAndKnockoutTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def setUp(self):
        # Seeds snake into Group A (A1, A4, A5, A8 by seed) and Group B.
        self.cup = self.start(self.make_championship([f"T{n}" for n in range(1, 9)], third_place_match=True))
        self.group_a, self.group_b = list(self.cup.groups.all())

    def finish_group(self, group, order):
        """Play every match in the group so teams finish in the given order."""
        rank = {name: index for index, name in enumerate(order)}
        for match in self.cup.matches.filter(group=group).select_related("home_team", "away_team"):
            if rank[match.home_team.name] < rank[match.away_team.name]:
                self.play(match, 2, 0)
            else:
                self.play(match, 0, 1)

    def group_names(self, group):
        return sorted(group.teams.values_list("name", flat=True))

    def semis(self):
        return list(self.cup.matches.filter(knockout_round=Match.Round.SEMI_FINAL).order_by("bracket_position"))

    def test_qualification_is_not_finalized_while_matches_remain(self):
        a_teams = self.group_names(self.group_a)
        matches = list(self.cup.matches.filter(group=self.group_a))
        self.play(matches[0], 1, 0)
        table = self.table(self.cup, 0)
        self.assertFalse(table.is_complete)
        self.assertTrue(all(row.status == StandingRow.PENDING for row in table.rows))
        self.assertTrue(all(match.home_team is None for match in self.semis()))
        self.assertEqual(len(a_teams), 4)

    def test_completing_group_selects_qualified_teams_and_fills_bracket(self):
        a_order = self.group_names(self.group_a)
        b_order = self.group_names(self.group_b)
        self.finish_group(self.group_a, a_order)

        table = self.table(self.cup, 0)
        self.assertTrue(table.is_complete)
        self.assertEqual([row.team.name for row in table.rows], a_order)
        self.assertEqual([row.status for row in table.rows], ["qualified", "qualified", "eliminated", "eliminated"])
        semi1, semi2 = self.semis()
        self.assertEqual(semi1.home_team.name, a_order[0])
        self.assertIsNone(semi1.away_team)
        self.assertEqual(semi2.away_team.name, a_order[1])

        self.finish_group(self.group_b, b_order)
        semi1, semi2 = self.semis()
        self.assertEqual((semi1.home_team.name, semi1.away_team.name), (a_order[0], b_order[1]))
        self.assertEqual((semi2.home_team.name, semi2.away_team.name), (b_order[0], a_order[1]))

    def test_clinched_and_eliminated_teams_are_reported_early(self):
        order = self.group_names(self.group_a)
        top = order[0]
        for match in self.cup.matches.filter(group=self.group_a).select_related("home_team", "away_team"):
            if top in (match.home_team.name, match.away_team.name):
                self.play(match, 3, 0) if match.home_team.name == top else self.play(match, 0, 3)
        table = self.table(self.cup, 0)
        self.assertFalse(table.is_complete)
        self.assertEqual(self.row(table, top).status, StandingRow.QUALIFIED)
        self.assertEqual(self.row(table, top).status_label, "Qualified (clinched)")

    def test_winners_advance_and_tied_knockout_needs_penalties(self):
        a_order = self.group_names(self.group_a)
        b_order = self.group_names(self.group_b)
        self.finish_group(self.group_a, a_order)
        self.finish_group(self.group_b, b_order)
        semi1, semi2 = self.semis()

        with self.assertRaises(ValidationError):
            self.play(semi1, 1, 1)  # extra time not ticked
        with self.assertRaises(ValidationError):
            self.play(semi1, 1, 1, extra_time=True)  # no penalties
        with self.assertRaises(ValidationError):
            self.play(semi1, 1, 1, extra_time=True, home_penalties=4, away_penalties=4)
        semi1.refresh_from_db()
        self.assertEqual(semi1.status, Match.Status.SCHEDULED)

        semi1 = self.play(semi1, 1, 1, extra_time=True, home_penalties=3, away_penalties=5)
        self.assertEqual(semi1.winner.name, b_order[1])
        self.assertEqual(semi1.decided_by, Match.Decision.PENALTIES)
        semi2 = self.play(semi2, 2, 1, extra_time=True)
        self.assertEqual(semi2.decided_by, Match.Decision.EXTRA_TIME)

        final = self.cup.matches.get(knockout_round=Match.Round.FINAL)
        third = self.cup.matches.get(knockout_round=Match.Round.THIRD_PLACE)
        self.assertEqual((final.home_team.name, final.away_team.name), (b_order[1], b_order[0]))
        self.assertEqual((third.home_team.name, third.away_team.name), (a_order[0], a_order[1]))

        self.play(third, 0, 2)
        self.play(final, 3, 0)
        self.cup.refresh_from_db()
        self.assertEqual(self.cup.status, Championship.Status.COMPLETED)

    def test_correcting_a_result_with_downstream_results_needs_confirmation(self):
        self.finish_group(self.group_a, self.group_names(self.group_a))
        self.finish_group(self.group_b, self.group_names(self.group_b))
        semi1, semi2 = self.semis()
        self.play(semi1, 2, 0)
        self.play(semi2, 2, 0)
        final = self.cup.matches.get(knockout_round=Match.Round.FINAL)
        self.play(final, 1, 0)
        original_finalist = final.home_team

        with self.assertRaises(ProgressionConflict):
            self.play(semi1, 0, 2)
        semi1.refresh_from_db()
        final.refresh_from_db()
        self.assertEqual((semi1.home_score, semi1.away_score), (2, 0))
        self.assertEqual(final.status, Match.Status.COMPLETED)

        self.play(semi1, 0, 2, allow_reset=True)
        final.refresh_from_db()
        self.assertEqual(final.status, Match.Status.SCHEDULED)
        self.assertIsNone(final.home_score)
        self.assertNotEqual(final.home_team, original_finalist)
        self.assertEqual(final.home_team, semi1.away_team)
        self.assertTrue(
            ResultAudit.objects.filter(match=final, action=ResultAudit.Action.RESULT_INVALIDATED).exists()
        )

    def test_organizer_decision_rule_requires_explicit_winner(self):
        cup = self.start(
            self.make_championship(
                ["K1", "K2"],
                name="Coin Toss Cup",
                format=Championship.Format.KNOCKOUT,
                knockout_tie_rule=Championship.KnockoutTieRule.ORGANIZER,
            )
        )
        final = cup.matches.get()
        with self.assertRaises(ValidationError):
            self.play(final, 0, 0)
        final = self.play(final, 0, 0, winner_side="away")
        self.assertEqual(final.winner.name, "K2")
        self.assertEqual(final.decided_by, Match.Decision.ORGANIZER)

    def test_knockout_only_bracket_seeds_teams_directly(self):
        cup = self.start(
            self.make_championship([f"K{n}" for n in range(1, 9)], name="Straight KO", format=Championship.Format.KNOCKOUT)
        )
        quarters = list(cup.matches.filter(knockout_round=Match.Round.QUARTER_FINAL).order_by("bracket_position"))
        pairs = [(match.home_team.name, match.away_team.name) for match in quarters]
        self.assertEqual(pairs, [("K1", "K8"), ("K4", "K5"), ("K2", "K7"), ("K3", "K6")])
        self.assertFalse(cup.matches.filter(knockout_round=Match.Round.SEMI_FINAL, home_team__isnull=False).exists())
        self.play(quarters[0], 0, 1)
        self.play(quarters[1], 2, 1)
        semi = cup.matches.get(knockout_round=Match.Round.SEMI_FINAL, bracket_position=1)
        self.assertEqual((semi.home_team.name, semi.away_team.name), ("K8", "K4"))


class PermissionTests(ChampionshipTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_services_refuse_users_who_do_not_manage_the_championship(self):
        cup = self.start(self.make_championship(["Ash", "Birch", "Cedar"], format=Championship.Format.GROUPS, group_count=1))
        match = cup.matches.first()
        with self.assertRaises(PermissionDenied):
            record_result(match, user=self.other_user, home_score=5, away_score=0)
        with self.assertRaises(PermissionDenied):
            update_schedule(match, user=self.other_user, kickoff_at=None, field_name="", status=Match.Status.CANCELLED)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.SCHEDULED)

    def test_administrator_with_permission_can_manage(self):
        cup = self.make_championship(["Ash", "Birch"], format=Championship.Format.GROUPS, group_count=1)
        admin = get_user_model().objects.create_superuser(username="root", password="strong-password-123")
        self.assertTrue(cup.can_manage(admin))
        self.assertFalse(cup.can_manage(self.other_user))

    def test_publishing_requires_valid_configuration(self):
        cup = self.make_championship(["Ash", "Birch", "Cedar"], team_count=8)
        with self.assertRaises(ValidationError):
            publish(cup, user=self.owner_user)
        cup.refresh_from_db()
        self.assertEqual(cup.status, Championship.Status.DRAFT)

    def test_results_need_a_published_championship(self):
        cup = self.make_championship(["Ash", "Birch"], format=Championship.Format.GROUPS, group_count=1)
        generate_fixtures(cup, user=self.owner_user)
        with self.assertRaises(ValidationError):
            record_result(cup.matches.first(), user=self.owner_user, home_score=1, away_score=0)
        self.assertIsNone(cup.matches.first().home_score)
        self.assertGreater(cup.matches.first().kickoff_at, datetime(2026, 11, 7, tzinfo=ZoneInfo("UTC")) - timedelta(hours=1))
