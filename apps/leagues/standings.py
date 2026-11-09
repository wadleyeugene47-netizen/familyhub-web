from collections import defaultdict

from .models import Match, Team


POINTS_FOR_WIN = 3
POINTS_FOR_DRAW = 1


def calculate_standings(league):
    rows = {
        team.pk: {
            "team": team,
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_difference": 0,
            "points": 0,
        }
        for team in Team.objects.filter(league=league)
    }

    completed_matches = Match.objects.filter(
        league=league,
        status=Match.Status.COMPLETED,
        home_score__isnull=False,
        away_score__isnull=False,
    ).select_related("home_team", "away_team")

    for match in completed_matches:
        home = rows[match.home_team_id]
        away = rows[match.away_team_id]
        home["played"] += 1
        away["played"] += 1
        home["goals_for"] += match.home_score
        home["goals_against"] += match.away_score
        away["goals_for"] += match.away_score
        away["goals_against"] += match.home_score

        if match.home_score > match.away_score:
            home["won"] += 1
            away["lost"] += 1
            home["points"] += POINTS_FOR_WIN
        elif match.home_score < match.away_score:
            away["won"] += 1
            home["lost"] += 1
            away["points"] += POINTS_FOR_WIN
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["points"] += POINTS_FOR_DRAW
            away["points"] += POINTS_FOR_DRAW

    for row in rows.values():
        row["goal_difference"] = row["goals_for"] - row["goals_against"]

    return sorted(
        rows.values(),
        key=lambda row: (
            -row["points"],
            -row["goal_difference"],
            -row["goals_for"],
            row["team"].name.casefold(),
        ),
    )
