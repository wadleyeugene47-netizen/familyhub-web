"""Split players into fair teams. Pure logic with no database, so the same input always gives the same teams.

1. Team sizes differ by at most one.
2. Each position (and each gender within it) is spread as evenly as possible.
3. Team rating totals are brought as close together as those two rules allow.
"""
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

MAX_SWAPS = 500


@dataclass(frozen=True)
class Player:
    key: int
    rating: int
    position: str
    gender: str = ""


def _has_room(team, teams, base, extra):
    if len(team) < base:
        return True
    return len(team) == base and sum(len(other) > base for other in teams) < extra


def _place(players, team_count):
    teams = [[] for _ in range(team_count)]
    base, extra = divmod(len(players), team_count)
    # The scarcest position goes first, while every team still has room for it.
    position_sizes = Counter(player.position for player in players)
    ordered = sorted(players, key=lambda p: (position_sizes[p.position], p.position, -p.rating, p.gender, p.key))

    def pressure(index, player):
        team = teams[index]
        same_position = [p for p in team if p.position == player.position]
        return (
            len(same_position),
            sum(p.gender == player.gender for p in same_position),
            sum(p.rating for p in team),
            len(team),
            index,
        )

    for player in ordered:
        open_teams = [i for i, team in enumerate(teams) if _has_room(team, teams, base, extra)]
        teams[min(open_teams, key=lambda i: pressure(i, player))].append(player)
    return teams


def _best_swap(teams):
    """The like-for-like swap that most evens out the totals, or None when no swap helps."""
    best, best_gain = None, 0
    totals = [sum(p.rating for p in team) for team in teams]
    for a, b in combinations(range(len(teams)), 2):
        for i, first in enumerate(teams[a]):
            for j, second in enumerate(teams[b]):
                if (first.position, first.gender) != (second.position, second.gender):
                    continue
                change = first.rating - second.rating
                gain = totals[a] ** 2 + totals[b] ** 2 - (totals[a] - change) ** 2 - (totals[b] + change) ** 2
                if gain > best_gain:
                    best, best_gain = (a, i, b, j), gain
    return best


def balance(players, team_count):
    """Return one list of players per team."""
    if team_count < 2:
        raise ValueError("At least two teams are needed.")
    teams = _place(list(players), team_count)
    for _ in range(MAX_SWAPS):
        swap = _best_swap(teams)
        if swap is None:
            break
        a, i, b, j = swap
        teams[a][i], teams[b][j] = teams[b][j], teams[a][i]
    return teams
