from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation

from .models import League, LeagueJoinRequest, Match, TeamMembership
from .scheduling import next_round_number, pairings_for_round


class RoundError(Exception):
    pass


def _match_category():
    categories = EventCategory.objects.filter(is_active=True)
    return categories.filter(slug="soccer-sports").first() or categories.first()


@transaction.atomic
def generate_next_round(league, data):
    """Create the next round-robin round: one match event per pairing, played back to back."""
    league = League.objects.select_for_update().get(pk=league.pk)
    round_number = next_round_number(league)
    pairs = pairings_for_round(league, round_number)
    if not pairs:
        raise RoundError("Add at least two teams before creating a round.")
    category = _match_category()
    if category is None:
        raise RoundError("Create an event category in Site settings first.")
    venue = EventLocation.objects.create(
        name=data["venue_name"],
        address_line=data["address_line"],
        city=data["city"],
        country_code=data["country_code"],
    )
    league.home_venue = venue
    league.save(update_fields=("home_venue", "updated_at"))
    length = timedelta(minutes=data["minutes_per_game"])
    status = Event.Status.PUBLISHED if league.status == League.Status.ACTIVE else Event.Status.DRAFT
    matches = []
    for index, (home, away) in enumerate(pairs):
        starts_at = data["first_kickoff"] + index * length
        event = Event.objects.create(
            organizer=league.organizer,
            category=category,
            location=venue,
            title=f"{league.name} · Round {round_number}: {home.name} vs {away.name}",
            description=f"Round {round_number} of {league.name}.",
            starts_at=starts_at,
            ends_at=starts_at + length,
            timezone=data["timezone"],
            capacity=100,
            price=0,
            status=status,
        )
        match = Match(league=league, event=event, home_team=home, away_team=away, round_number=round_number)
        match.full_clean()
        match.save()
        matches.append(match)
    return round_number, matches


@transaction.atomic
def activate_league(league):
    league = League.objects.select_for_update().get(pk=league.pk)
    if league.teams.count() < 2:
        raise RoundError("Add at least two teams before starting the league.")
    league.status = League.Status.ACTIVE
    league.save(update_fields=("status", "updated_at"))
    Event.objects.filter(soccer_match__league=league, status=Event.Status.DRAFT).update(
        status=Event.Status.PUBLISHED, updated_at=timezone.now()
    )
    return league


@transaction.atomic
def add_player(*, team, user, role, jersey_number=None):
    if role == TeamMembership.Role.CAPTAIN:
        team.memberships.filter(status=TeamMembership.Status.ACTIVE, role=TeamMembership.Role.CAPTAIN).update(
            role=TeamMembership.Role.PLAYER
        )
    return TeamMembership.objects.create(
        team=team,
        user=user,
        role=role,
        jersey_number=jersey_number,
        status=TeamMembership.Status.ACTIVE,
    )


@transaction.atomic
def captain_add_player(team, user, jersey_number=None):
    # Locking the league stops two captains signing the same player at the same moment.
    league = League.objects.select_for_update().get(pk=team.league_id)
    if league.status != League.Status.ACTIVE:
        raise ValidationError("This league isn't running, so its teams can't change.")
    already_playing = TeamMembership.objects.filter(
        team__league=league,
        user=user,
        status__in=[TeamMembership.Status.ACTIVE, TeamMembership.Status.PENDING],
    )
    if already_playing.exists():
        raise ValidationError(f"{user.display_name} is already on a team in this league.")
    if jersey_number is not None and team.active_memberships().filter(jersey_number=jersey_number).exists():
        raise ValidationError("Someone on your team already wears this number.")
    membership = add_player(
        team=team, user=user, role=TeamMembership.Role.PLAYER, jersey_number=jersey_number
    )
    # Their request to play is answered now, so it shouldn't stay in the host's queue.
    league.join_requests.filter(user=user, status=LeagueJoinRequest.Status.PENDING).update(
        status=LeagueJoinRequest.Status.ACCEPTED, decided_at=timezone.now()
    )
    return membership


@transaction.atomic
def captain_remove_player(team, membership_id):
    membership = (
        team.memberships.select_for_update()
        .filter(pk=membership_id, status=TeamMembership.Status.ACTIVE)
        .select_related("user")
        .first()
    )
    if membership is None:
        raise ValidationError("That player is no longer on your team.")
    if membership.role == TeamMembership.Role.CAPTAIN:
        raise ValidationError("A captain can't be removed here. Ask the host to choose a new captain first.")
    membership.status = TeamMembership.Status.REMOVED
    membership.save(update_fields=("status",))
    return membership


@transaction.atomic
def set_jersey_numbers(team, numbers):
    """numbers maps membership id to a shirt number, or None to clear it. All are saved or none are."""
    memberships = list(
        team.memberships.select_for_update().filter(status=TeamMembership.Status.ACTIVE, pk__in=numbers)
    )
    if len(memberships) != len(numbers):
        raise ValidationError("Someone in that list is no longer on your team.")
    chosen = [number for number in numbers.values() if number is not None]
    if any(not 0 <= number <= 99 for number in chosen):
        raise ValidationError("Shirt numbers go from 0 to 99.")
    if len(chosen) != len(set(chosen)):
        raise ValidationError("Two players can't wear the same number.")
    others = team.memberships.filter(status=TeamMembership.Status.ACTIVE, jersey_number__in=chosen).exclude(
        pk__in=numbers
    )
    if others.exists():
        raise ValidationError("That number is already taken by a player not in the list.")
    # Clear first so two players can swap numbers without touching the uniqueness rule.
    team.memberships.filter(pk__in=numbers).update(jersey_number=None)
    for membership in memberships:
        membership.jersey_number = numbers[membership.pk]
        membership.save(update_fields=("jersey_number",))


@transaction.atomic
def make_captain(membership):
    membership.team.memberships.filter(
        status=TeamMembership.Status.ACTIVE, role=TeamMembership.Role.CAPTAIN
    ).exclude(pk=membership.pk).update(role=TeamMembership.Role.PLAYER)
    membership.role = TeamMembership.Role.CAPTAIN
    membership.save(update_fields=("role",))
