"""Configuration checks, group distribution, fixture generation and knockout bracket layout."""

import string
from datetime import timedelta

from .models import Championship, Group, Match, Team

KNOCKOUT_SIZES = (2, 4, 8, 16, 32, 64)
FIRST_ROUND_BY_SIZE = {
    2: Match.Round.FINAL,
    4: Match.Round.SEMI_FINAL,
    8: Match.Round.QUARTER_FINAL,
    16: Match.Round.ROUND_OF_16,
    32: Match.Round.ROUND_OF_32,
    64: Match.Round.ROUND_OF_64,
}
ROUND_ORDER = (
    Match.Round.ROUND_OF_64,
    Match.Round.ROUND_OF_32,
    Match.Round.ROUND_OF_16,
    Match.Round.QUARTER_FINAL,
    Match.Round.SEMI_FINAL,
    Match.Round.THIRD_PLACE,
    Match.Round.FINAL,
)


def knockout_size(championship):
    if championship.format == Championship.Format.KNOCKOUT:
        return championship.team_count
    if championship.format == Championship.Format.GROUPS_KNOCKOUT:
        return championship.group_count * championship.advance_per_group
    return 0


def configuration_errors(championship):
    """Problems with the tournament settings themselves, independent of registered teams."""
    errors = []
    teams = championship.team_count or 0
    groups = championship.group_count or 0
    advance = championship.advance_per_group or 0
    if championship.has_groups:
        if groups < 1:
            errors.append("Choose at least one group.")
        elif teams < 2 * groups:
            errors.append(
                f"Each group needs at least two teams, so {teams} teams can fill at most {teams // 2} group(s)."
            )
    if championship.format == Championship.Format.KNOCKOUT and teams not in KNOCKOUT_SIZES:
        errors.append("A knockout tournament needs 2, 4, 8, 16, 32 or 64 teams so every round pairs up evenly.")
    if championship.format == Championship.Format.GROUPS_KNOCKOUT and groups >= 1 and teams >= 2 * groups:
        smallest = teams // groups
        if advance >= smallest:
            errors.append(
                f"With {teams} teams in {groups} group(s) the smallest group has {smallest} teams, "
                f"so fewer than {smallest} teams per group can advance."
            )
        qualifiers = groups * advance
        if qualifiers not in KNOCKOUT_SIZES:
            errors.append(
                f"{groups} group(s) × {advance} qualifier(s) = {qualifiers} teams; the knockout stage needs "
                "2, 4, 8, 16, 32 or 64."
            )
    if championship.third_place_match and knockout_size(championship) < 4:
        errors.append("A third-place match needs at least four teams in the knockout stage.")
    if (
        championship.points_for_win is not None
        and championship.points_for_draw is not None
        and championship.points_for_win <= championship.points_for_draw
    ):
        errors.append("A win must be worth more than a draw.")
    return errors


def team_errors(championship):
    """Problems with the registered teams and their group assignments."""
    errors = []
    teams = list(championship.teams.select_related("group"))
    if len(teams) != championship.team_count:
        errors.append(
            f"{len(teams)} of {championship.team_count} teams are registered. "
            "Add or remove teams, or change the number of teams."
        )
    if championship.has_groups and len(teams) == championship.team_count:
        groups = list(championship.groups.all())
        if len(groups) != championship.group_count:
            errors.append("The groups do not match the configured number of groups.")
        assigned = [team for team in teams if team.group_id]
        if assigned and len(assigned) == len(teams):
            for group in groups:
                size = sum(1 for team in teams if team.group_id == group.pk)
                if size < 2:
                    errors.append(f"{group.name} needs at least two teams.")
                elif championship.format == Championship.Format.GROUPS_KNOCKOUT and size <= championship.advance_per_group:
                    errors.append(
                        f"{group.name} has {size} teams, but {championship.advance_per_group} advance from each group."
                    )
    return errors


def ensure_groups(championship):
    """Create or remove groups so they match the configured format and group count."""
    if not championship.has_groups:
        championship.groups.all().delete()
        return []
    existing = list(championship.groups.order_by("order"))
    for group in existing[championship.group_count:]:
        group.delete()
    for index in range(len(existing), championship.group_count):
        Group.objects.create(championship=championship, name=f"Group {_group_letter(index)}", order=index)
    return list(championship.groups.order_by("order"))


def _group_letter(index):
    letters = string.ascii_uppercase
    return letters[index] if index < len(letters) else f"{letters[index // 26 - 1]}{letters[index % 26]}"


def _seed_order(teams):
    return sorted(teams, key=lambda team: (team.seed is None, team.seed or 0, team.name.casefold()))


def distribute_teams(championship, *, reset=False):
    """Snake-draft seeded teams into groups; with reset=False only unassigned teams move."""
    groups = ensure_groups(championship)
    if not groups:
        return
    teams = list(championship.teams.all())
    if reset:
        ordered = _seed_order(teams)
        for index, team in enumerate(ordered):
            row, column = divmod(index, len(groups))
            team.group = groups[column if row % 2 == 0 else len(groups) - 1 - column]
        Team.objects.bulk_update(ordered, ["group"])
        return
    sizes = {group.pk: sum(1 for team in teams if team.group_id == group.pk) for group in groups}
    unassigned = _seed_order([team for team in teams if team.group_id is None])
    for team in unassigned:
        group = min(groups, key=lambda candidate: (sizes[candidate.pk], candidate.order))
        team.group = group
        sizes[group.pk] += 1
    Team.objects.bulk_update(unassigned, ["group"])


def round_robin(teams, legs=1):
    """Circle-method rounds of (home, away) pairs. Every team meets every other team once per leg."""
    teams = list(teams)
    if len(teams) % 2:
        teams.append(None)
    count = len(teams)
    rounds = []
    for round_index in range(count - 1):
        pairs = []
        for index in range(count // 2):
            first, second = teams[index], teams[count - 1 - index]
            if first is None or second is None:
                continue
            pairs.append((first, second) if (round_index + index) % 2 == 0 else (second, first))
        rounds.append(pairs)
        teams = [teams[0], teams[-1]] + teams[1:-1]
    if legs == 2:
        rounds += [[(away, home) for home, away in pairs] for pairs in rounds]
    return rounds


def bracket_order(size):
    """Seed numbers in bracket order, so seeds 1 and 2 can only meet in the final."""
    order = [1, 2]
    while len(order) < size:
        mirror = len(order) * 2 + 1
        order = [seed for current in order for seed in (current, mirror - current)]
    return order


class Scheduler:
    """Hands out kickoff times and fields, one round at a time, in the championship timezone."""

    def __init__(self, championship):
        self.championship = championship
        self.fields = championship.fields
        self.slot = timedelta(
            minutes=championship.match_duration_minutes + championship.break_between_matches_minutes
        )
        start = championship.first_kickoff_at
        self.day_start = start.astimezone(championship.zone).replace(tzinfo=None) if start else None
        self.round_index = 0
        self.next_slot = 0

    def round_times(self, count, sequential=False):
        """Kickoff times and fields for one round; sequential matches share the first field in turn."""
        fields = self.fields[:1] if sequential else self.fields
        if self.day_start is None:
            return [(None, fields[index % len(fields)]) for index in range(count)]
        days = self.championship.days_between_rounds
        if days or self.round_index == 0:
            day = self.day_start + timedelta(days=days * self.round_index)
            first_slot = 0
        else:
            day = self.day_start
            first_slot = self.next_slot
        times = []
        for index in range(count):
            slot_number = first_slot + index // len(fields)
            local = day + self.slot * slot_number
            times.append((local.replace(tzinfo=self.championship.zone), fields[index % len(fields)]))
        self.next_slot = first_slot + -(-count // len(fields))
        self.round_index += 1
        return times


def create_group_fixtures(championship, scheduler):
    groups = list(championship.groups.order_by("order"))
    group_rounds = {
        group.pk: round_robin(_seed_order(group.teams.all()), legs=championship.group_legs)
        for group in groups
    }
    total_rounds = max((len(rounds) for rounds in group_rounds.values()), default=0)
    created = []
    for round_index in range(total_rounds):
        pairings = []
        for group in groups:
            rounds = group_rounds[group.pk]
            if round_index < len(rounds):
                pairings.extend((group, home, away) for home, away in rounds[round_index])
        for (group, home, away), (kickoff, field) in zip(pairings, scheduler.round_times(len(pairings))):
            match = Match(
                championship=championship,
                stage=Match.Stage.GROUP,
                group=group,
                round_number=round_index + 1,
                home_team=home,
                away_team=away,
                kickoff_at=kickoff,
                field_name=field,
            )
            match.full_clean()
            match.save()
            created.append(match)
    return created


def _entrants(championship):
    """Knockout entrants in seed order: teams directly, or group finishing positions."""
    if championship.format == Championship.Format.KNOCKOUT:
        return [{"team": team} for team in _seed_order(championship.teams.all())]
    groups = list(championship.groups.order_by("order"))
    return [
        {"group": group, "rank": rank}
        for rank in range(1, championship.advance_per_group + 1)
        for group in groups
    ]


def _set_source(match, side, entrant):
    if "team" in entrant:
        setattr(match, f"{side}_team", entrant["team"])
    else:
        setattr(match, f"{side}_source_group", entrant["group"])
        setattr(match, f"{side}_source_rank", entrant["rank"])


def create_knockout_bracket(championship, scheduler):
    entrants = _entrants(championship)
    size = len(entrants)
    if size not in KNOCKOUT_SIZES:
        return []
    order = bracket_order(size)
    round_code = FIRST_ROUND_BY_SIZE[size]
    previous = []
    current = []
    round_number = 1
    times = scheduler.round_times(size // 2)
    for position in range(size // 2):
        match = Match(
            championship=championship,
            stage=Match.Stage.KNOCKOUT,
            knockout_round=round_code,
            round_number=round_number,
            bracket_position=position + 1,
            kickoff_at=times[position][0],
            field_name=times[position][1],
        )
        _set_source(match, "home", entrants[order[position * 2] - 1])
        _set_source(match, "away", entrants[order[position * 2 + 1] - 1])
        match.full_clean()
        match.save()
        current.append(match)
    created = list(current)

    while len(current) > 1:
        previous, current = current, []
        round_number += 1
        round_code = FIRST_ROUND_BY_SIZE[len(previous)]
        is_final = round_code == Match.Round.FINAL
        add_third_place = is_final and championship.third_place_match
        pairings = len(previous) // 2
        times = scheduler.round_times(pairings + (1 if add_third_place else 0), sequential=is_final)
        if add_third_place:
            third = Match(
                championship=championship,
                stage=Match.Stage.KNOCKOUT,
                knockout_round=Match.Round.THIRD_PLACE,
                round_number=round_number,
                bracket_position=1,
                home_source_match=previous[0],
                home_source_outcome=Match.Outcome.LOSER,
                away_source_match=previous[1],
                away_source_outcome=Match.Outcome.LOSER,
                kickoff_at=times[0][0],
                field_name=times[0][1],
            )
            third.full_clean()
            third.save()
            created.append(third)
            times = times[1:]
        for position in range(pairings):
            match = Match(
                championship=championship,
                stage=Match.Stage.KNOCKOUT,
                knockout_round=round_code,
                round_number=round_number,
                bracket_position=position + 1,
                home_source_match=previous[position * 2],
                home_source_outcome=Match.Outcome.WINNER,
                away_source_match=previous[position * 2 + 1],
                away_source_outcome=Match.Outcome.WINNER,
                kickoff_at=times[position][0],
                field_name=times[position][1],
            )
            match.full_clean()
            match.save()
            current.append(match)
        created.extend(current)
    return created
