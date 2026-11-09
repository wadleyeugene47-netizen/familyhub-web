"""Group standings, calculated only from completed match rows stored in the database."""

from dataclasses import dataclass, field
from itertools import groupby

from .models import Championship, Match, MatchIncident

UNRANKED_LOTS = 10_000


@dataclass
class StandingRow:
    team: object
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0
    remaining: int = 0
    position: int = 0
    tied: bool = False
    provisional: bool = False
    status: str = ""
    sort_key: tuple = field(default=(), repr=False)

    QUALIFIED = "qualified"
    ELIMINATED = "eliminated"
    PENDING = "pending"
    TIE = "tie"
    WINNER = "winner"

    @property
    def goal_difference(self):
        return self.goals_for - self.goals_against

    @property
    def status_label(self):
        return {
            self.QUALIFIED: "Qualified (clinched)" if self.provisional else "Qualified",
            self.ELIMINATED: "Eliminated",
            self.PENDING: "Not yet decided",
            self.TIE: "Level on every tie-breaker",
            self.WINNER: "Group winner",
        }.get(self.status, "")


@dataclass
class GroupTable:
    group: object
    rows: list
    match_count: int
    pending_count: int

    @property
    def is_complete(self):
        return self.match_count > 0 and self.pending_count == 0

    def team_at(self, position):
        """The team finishing at a position, or None while that position is not final."""
        if not self.is_complete or not 1 <= position <= len(self.rows):
            return None
        row = self.rows[position - 1]
        return None if row.tied else row.team

    @property
    def unresolved_ties(self):
        return [row for row in self.rows if row.tied]


def tie_breaker_steps(championship):
    overall = ["Goal difference across all group matches", "Goals scored across all group matches"]
    head_to_head = [
        "Points in matches between the tied teams",
        "Goal difference in matches between the tied teams",
        "Goals scored in matches between the tied teams",
    ]
    if championship.tie_breaker == Championship.TieBreaker.HEAD_TO_HEAD_FIRST:
        middle = head_to_head + overall
    else:
        middle = overall + head_to_head
    return (
        ["Points"]
        + middle
        + ["Number of wins", "Drawing of lots, recorded by the organizer"]
    )


def _apply_result(home, away, home_score, away_score, championship):
    home.played += 1
    away.played += 1
    home.goals_for += home_score
    home.goals_against += away_score
    away.goals_for += away_score
    away.goals_against += home_score
    if home_score > away_score:
        home.won += 1
        away.lost += 1
        home.points += championship.points_for_win
        away.points += championship.points_for_loss
    elif home_score < away_score:
        away.won += 1
        home.lost += 1
        away.points += championship.points_for_win
        home.points += championship.points_for_loss
    else:
        home.drawn += 1
        away.drawn += 1
        home.points += championship.points_for_draw
        away.points += championship.points_for_draw


def _head_to_head(cluster, completed, championship):
    team_ids = {row.team.pk for row in cluster}
    mini = {team_id: StandingRow(team=None) for team_id in team_ids}
    for match in completed:
        if match.home_team_id in team_ids and match.away_team_id in team_ids:
            _apply_result(
                mini[match.home_team_id],
                mini[match.away_team_id],
                match.home_score,
                match.away_score,
                championship,
            )
    return mini


def _rank(rows, completed, championship):
    ordered = []
    rows = sorted(rows, key=lambda row: -row.points)
    for _points, cluster in groupby(rows, key=lambda row: row.points):
        cluster = list(cluster)
        h2h = _head_to_head(cluster, completed, championship) if len(cluster) > 1 else {}
        for row in cluster:
            overall = (-row.goal_difference, -row.goals_for)
            if h2h:
                mini = h2h[row.team.pk]
                head = (-mini.points, -mini.goal_difference, -mini.goals_for)
            else:
                head = (0, 0, 0)
            if championship.tie_breaker == Championship.TieBreaker.HEAD_TO_HEAD_FIRST:
                criteria = head + overall
            else:
                criteria = overall + head
            lots = row.team.lots_rank if row.team.lots_rank is not None else UNRANKED_LOTS
            row.sort_key = (-row.points,) + criteria + (-row.won, lots)
        cluster.sort(key=lambda row: (row.sort_key, row.team.name.casefold()))
        ordered.extend(cluster)

    for position, row in enumerate(ordered, start=1):
        row.position = position
    for _key, run in groupby(ordered, key=lambda row: row.sort_key):
        run = list(run)
        if len(run) > 1:
            for row in run:
                row.tied = True
    return ordered


def _assign_status(table, championship):
    rows = table.rows
    if championship.format == Championship.Format.GROUPS:
        if table.is_complete and rows and not rows[0].tied:
            rows[0].status = StandingRow.WINNER
        return
    if championship.format != Championship.Format.GROUPS_KNOCKOUT:
        return
    advance = championship.advance_per_group
    if table.is_complete:
        for _key, run in groupby(rows, key=lambda row: row.sort_key):
            run = list(run)
            first, last = run[0].position, run[-1].position
            spans_cutoff = len(run) > 1 and first <= advance < last
            for row in run:
                if spans_cutoff:
                    row.status = StandingRow.TIE
                elif row.position <= advance:
                    row.status = StandingRow.QUALIFIED
                else:
                    row.status = StandingRow.ELIMINATED
        return
    # Group still in play: only report outcomes no remaining result can change.
    for row in rows:
        row.provisional = True
        best_case = row.points + row.remaining * championship.points_for_win
        others = [other for other in rows if other is not row]
        could_finish_level_or_above = sum(
            1
            for other in others
            if other.points + other.remaining * championship.points_for_win >= row.points
        )
        certainly_above = sum(1 for other in others if other.points > best_case)
        if could_finish_level_or_above < advance:
            row.status = StandingRow.QUALIFIED
        elif certainly_above >= advance:
            row.status = StandingRow.ELIMINATED
        else:
            row.status = StandingRow.PENDING


def group_table(group, matches=None, teams=None):
    championship = group.championship
    if matches is None:
        matches = list(group.matches.all())
    if teams is None:
        teams = list(group.teams.all())
    rows = {team.pk: StandingRow(team=team) for team in teams}
    completed = [
        match
        for match in matches
        if match.status == Match.Status.COMPLETED
        and match.home_team_id in rows
        and match.away_team_id in rows
    ]
    pending = [
        match
        for match in matches
        if match.status not in (Match.Status.COMPLETED, Match.Status.CANCELLED)
    ]
    for match in completed:
        _apply_result(
            rows[match.home_team_id],
            rows[match.away_team_id],
            match.home_score,
            match.away_score,
            championship,
        )
    for match in pending:
        for team_id in (match.home_team_id, match.away_team_id):
            if team_id in rows:
                rows[team_id].remaining += 1
    table = GroupTable(
        group=group,
        rows=_rank(list(rows.values()), completed, championship),
        match_count=len(matches),
        pending_count=len(pending),
    )
    _assign_status(table, championship)
    return table


def championship_tables(championship):
    groups = list(championship.groups.all())
    matches = list(
        championship.matches.filter(stage=Match.Stage.GROUP).select_related("home_team", "away_team")
    )
    teams = list(championship.teams.all())
    tables = []
    for group in groups:
        group.championship = championship
        tables.append(
            group_table(
                group,
                matches=[match for match in matches if match.group_id == group.pk],
                teams=[team for team in teams if team.group_id == group.pk],
            )
        )
    return tables


def team_records(championship, matches):
    """Tournament-wide record per team across group and knockout matches (penalties excluded)."""
    records = {team.pk: StandingRow(team=team) for team in championship.teams.select_related("group")}
    for match in matches:
        if match.status == Match.Status.COMPLETED and match.home_team_id in records and match.away_team_id in records:
            _apply_result(
                records[match.home_team_id],
                records[match.away_team_id],
                match.home_score,
                match.away_score,
                championship,
            )
    eliminated = set()
    for match in matches:
        if match.is_knockout and match.is_completed and match.loser is not None:
            if match.knockout_round != Match.Round.SEMI_FINAL or not championship.third_place_match:
                eliminated.add(match.loser.pk)
    if championship.format == Championship.Format.GROUPS_KNOCKOUT:
        for table in championship_tables(championship):
            eliminated.update(row.team.pk for row in table.rows if row.status == StandingRow.ELIMINATED)
    for row in records.values():
        row.status = StandingRow.ELIMINATED if row.team.pk in eliminated else ""
    return sorted(records.values(), key=lambda row: row.team.name.casefold())


def top_scorers(championship):
    tally = {}
    incidents = MatchIncident.objects.filter(
        match__championship=championship,
        match__status=Match.Status.COMPLETED,
        kind__in=[MatchIncident.Kind.GOAL, MatchIncident.Kind.PENALTY_GOAL],
    ).exclude(player_name="").select_related("team")
    for incident in incidents:
        key = (incident.player_name.casefold(), incident.team_id)
        entry = tally.setdefault(key, {"player": incident.player_name, "team": incident.team, "goals": 0})
        entry["goals"] += 1
    return sorted(tally.values(), key=lambda entry: (-entry["goals"], entry["player"].casefold()))
