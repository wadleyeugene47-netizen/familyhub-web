"""Round-robin pairings, one round at a time.

The circle method: fix the first team and rotate the rest, so over len(teams) - 1 rounds every team
meets every other team once. After a full cycle the next cycle starts again with home and away swapped.
"""
from django.db.models import Max


def round_robin_rounds(teams):
    """All rounds of one cycle, each a list of (home, away) pairs. Odd counts give one team a bye."""
    slots = list(teams)
    if len(slots) < 2:
        return []
    if len(slots) % 2:
        slots.append(None)
    rounds = []
    half = len(slots) // 2
    for number in range(len(slots) - 1):
        pairs = []
        for i in range(half):
            home, away = slots[i], slots[-1 - i]
            if home is None or away is None:
                continue
            # Alternate the fixed team's side so it is not always at home.
            if i == 0 and number % 2:
                home, away = away, home
            pairs.append((home, away))
        rounds.append(pairs)
        slots = [slots[0], slots[-1], *slots[1:-1]]
    return rounds


def next_round_number(league):
    return (league.matches.aggregate(last=Max("round_number"))["last"] or 0) + 1


def pairings_for_round(league, round_number):
    rounds = round_robin_rounds(league.teams.order_by("pk"))
    if not rounds:
        return []
    cycle, index = divmod(round_number - 1, len(rounds))
    pairs = rounds[index]
    if cycle % 2:
        pairs = [(away, home) for home, away in pairs]
    return pairs
