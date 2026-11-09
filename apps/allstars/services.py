from django.db import transaction

from .balancer import Player, balance
from .models import MAX_RATING, MIN_RATING, AllStarGame, AllStarRegistration, AllStarTeam

Status = AllStarGame.Status

TRANSITIONS = {
    "open": ({Status.DRAFT, Status.CLOSED}, Status.OPEN),
    "close": ({Status.OPEN}, Status.CLOSED),
    "publish": ({Status.CLOSED}, Status.PUBLISHED),
    "unpublish": ({Status.PUBLISHED}, Status.CLOSED),
    "cancel": ({Status.DRAFT, Status.OPEN, Status.CLOSED, Status.PUBLISHED}, Status.CANCELLED),
}


class AllStarError(Exception):
    """A request that cannot go ahead. The message is safe to show to the person."""


def _locked(game):
    return AllStarGame.objects.select_for_update().get(pk=game.pk)


def _active(game):
    return game.registrations.filter(status=AllStarRegistration.Status.ACTIVE)


@transaction.atomic
def register(game, user, *, position, gender, note=""):
    game = _locked(game)
    if not game.accepting_registrations:
        raise AllStarError("Registration for this game is closed.")
    registration, created = AllStarRegistration.objects.get_or_create(
        game=game, user=user, defaults={"position": position, "gender": gender, "note": note}
    )
    if created:
        return registration
    if registration.status == AllStarRegistration.Status.REMOVED:
        raise AllStarError("The organizer removed you from this game. Ask them if you think that's a mistake.")
    registration.status = AllStarRegistration.Status.ACTIVE
    registration.position, registration.gender, registration.note = position, gender, note
    registration.save(update_fields=("status", "position", "gender", "note"))
    return registration


@transaction.atomic
def withdraw(game, user):
    game = _locked(game)
    if not game.accepting_registrations:
        raise AllStarError("Registration has closed, so please ask the organizer to take you off the list.")
    updated = _active(game).filter(user=user).update(status=AllStarRegistration.Status.WITHDRAWN, team=None)
    if not updated:
        raise AllStarError("You are not registered for this game.")


@transaction.atomic
def remove_player(game, registration_id):
    game = _locked(game)
    if game.status in (Status.PUBLISHED, Status.CANCELLED):
        raise AllStarError("Take back the announced teams before changing the players.")
    if not _active(game).filter(pk=registration_id).update(status=AllStarRegistration.Status.REMOVED, team=None):
        raise AllStarError("That player is not on the list.")


@transaction.atomic
def save_ratings(game, ratings):
    """ratings maps registration id to a rating, or None to clear it."""
    game = _locked(game)
    if game.status in (Status.PUBLISHED, Status.CANCELLED):
        raise AllStarError("Ratings can't change once the teams are announced.")
    if any(value is not None and not MIN_RATING <= value <= MAX_RATING for value in ratings.values()):
        raise AllStarError(f"Ratings go from {MIN_RATING} to {MAX_RATING}.")
    registrations = list(_active(game).filter(pk__in=ratings))
    for registration in registrations:
        registration.skill_rating = ratings[registration.pk]
    AllStarRegistration.objects.bulk_update(registrations, ["skill_rating"])


def _sync_teams(game):
    """Make the game have exactly team_count teams, keeping any names the organizer already chose."""
    game.teams.filter(order__gte=game.team_count).delete()
    existing = {team.order: team for team in game.teams.all()}
    teams = []
    for order in range(game.team_count):
        team = existing.get(order) or AllStarTeam.objects.create(
            game=game, order=order, name=f"Team {chr(ord('A') + order)}"
        )
        teams.append(team)
    return teams


@transaction.atomic
def balance_teams(game):
    game = _locked(game)
    if game.status != Status.CLOSED:
        raise AllStarError("Close registration before balancing the teams.")
    registrations = list(_active(game))
    unrated = sum(registration.skill_rating is None for registration in registrations)
    if unrated:
        raise AllStarError(f"Give every player a rating first. {unrated} still need one.")
    if len(registrations) < game.team_count:
        raise AllStarError(f"You need at least {game.team_count} players for {game.team_count} teams.")
    by_id = {registration.pk: registration for registration in registrations}
    players = [Player(r.pk, r.skill_rating, r.position, r.gender) for r in registrations]
    for team, members in zip(_sync_teams(game), balance(players, game.team_count)):
        for member in members:
            by_id[member.key].team = team
    AllStarRegistration.objects.bulk_update(registrations, ["team"])


@transaction.atomic
def move_player(game, registration_id, team_id):
    game = _locked(game)
    if game.status != Status.CLOSED:
        raise AllStarError("Players can only be moved while registration is closed and teams aren't announced.")
    team = game.teams.filter(pk=team_id).first()
    if team is None or not _active(game).filter(pk=registration_id).update(team=team):
        raise AllStarError("That player or team is not part of this game.")


@transaction.atomic
def rename_team(game, team_id, name):
    game = _locked(game)
    name = name.strip()[:80]
    team = game.teams.filter(pk=team_id).first()
    if team is None or not name:
        raise AllStarError("Enter a name for the team.")
    if game.teams.filter(name__iexact=name).exclude(pk=team.pk).exists():
        raise AllStarError("Another team in this game already has that name.")
    team.name = name
    team.save(update_fields=("name",))


@transaction.atomic
def change_status(game, action):
    game = _locked(game)
    if action not in TRANSITIONS or game.status not in TRANSITIONS[action][0]:
        raise AllStarError("That step isn't available for this game right now.")
    if action == "publish":
        active = _active(game)
        if not active.exists() or active.filter(team__isnull=True).exists() or game.teams.count() != game.team_count:
            raise AllStarError("Balance the teams first, so that every player has a team.")
    game.status = TRANSITIONS[action][1]
    game.save(update_fields=("status", "updated_at"))
    return game
