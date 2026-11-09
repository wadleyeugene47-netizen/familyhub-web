"""The host panel's league pages. The host can run every league, whoever organizes it."""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event
from apps.events.services import cancel_event
from apps.hub.decorators import owner_required
from apps.organizers.models import OrganizerProfile

from .host_forms import AddPlayerForm, HostLeagueForm, HostTeamForm, NextRoundForm
from .models import League, LeagueChatMessage, LeagueJoinRequest, Match, Team, TeamMembership
from .services import RoundError, activate_league, add_player, generate_next_round, make_captain
from .standings import calculate_standings


def _post_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        return view(request, *args, **kwargs)

    return wrapper


def _host_organizer(user):
    profile, _ = OrganizerProfile.objects.get_or_create(
        user=user, defaults={"display_name": user.get_full_name() or user.username}
    )
    return profile


def _back(league, anchor=""):
    return redirect(reverse("hub:league", args=[league.pk]) + (f"#{anchor}" if anchor else ""))


@owner_required
def league_list(request):
    organizer = _host_organizer(request.user)
    form = HostLeagueForm(
        request.POST or None,
        organizer=organizer,
        initial={} if League.objects.exists() else {"name": "Brothers and Goals"},
    )
    if request.method == "POST" and form.is_valid():
        league = form.save(commit=False)
        league.organizer = organizer
        league.save()
        messages.success(request, f"{league.name} was created. Add teams next.")
        return _back(league)
    leagues = League.objects.annotate(
        team_count=Count("teams", distinct=True),
        played=Count("matches", filter=Q(matches__status=Match.Status.COMPLETED), distinct=True),
        waiting=Count("join_requests", filter=Q(join_requests__status=LeagueJoinRequest.Status.PENDING), distinct=True),
        match_count=Count("matches", distinct=True),
    ).select_related("organizer")
    return render(request, "hub/leagues.html", {"leagues": leagues, "form": form})


@owner_required
def league_panel(request, league_id, **forms):
    league = get_object_or_404(League.objects.select_related("organizer", "home_venue"), pk=league_id)
    teams = league.teams.prefetch_related("memberships__user")
    for team in teams:
        team.roster = [m for m in team.memberships.all() if m.status == TeamMembership.Status.ACTIVE]
        team.form = forms.get("team_forms", {}).get(team.pk) or HostTeamForm(
            instance=team, league=league, prefix=f"team-{team.pk}"
        )
    matches = league.matches.select_related("event", "home_team", "away_team").order_by(
        "round_number", "event__starts_at"
    )
    return render(
        request,
        "hub/league_panel.html",
        {
            "league": league,
            "teams": teams,
            "matches": matches,
            "standings": calculate_standings(league),
            "league_form": forms.get("league_form") or HostLeagueForm(instance=league, organizer=league.organizer),
            "new_team_form": forms.get("new_team_form") or HostTeamForm(league=league, prefix="new"),
            "player_form": forms.get("player_form") or AddPlayerForm(league=league),
            "round_form": forms.get("round_form") or NextRoundForm(league=league),
            "chat_messages": league.chat_messages.select_related("sender").order_by("-id")[:30],
            "join_requests": league.join_requests.filter(status=LeagueJoinRequest.Status.PENDING).select_related("user"),
        },
    )


def _league(league_id):
    return get_object_or_404(League, pk=league_id)


@owner_required
@_post_only
def league_edit(request, league_id):
    league = _league(league_id)
    form = HostLeagueForm(request.POST, instance=league, organizer=league.organizer)
    if not form.is_valid():
        return league_panel(request, league_id, league_form=form)
    form.save()
    messages.success(request, "League details saved.")
    return _back(league)


@owner_required
@_post_only
def league_start(request, league_id):
    league = _league(league_id)
    try:
        activate_league(league)
    except RoundError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{league.name} has started. Its games are now public.")
    return _back(league)


@owner_required
@_post_only
def team_add(request, league_id):
    league = _league(league_id)
    form = HostTeamForm(request.POST, league=league, prefix="new")
    if not form.is_valid():
        return league_panel(request, league_id, new_team_form=form)
    team = form.save(commit=False)
    team.league = league
    team.save()
    messages.success(request, f"Team {team.name} added.")
    return _back(league, "teams")


@owner_required
@_post_only
def team_edit(request, league_id, team_id):
    league = _league(league_id)
    team = get_object_or_404(Team, pk=team_id, league=league)
    form = HostTeamForm(request.POST, instance=team, league=league, prefix=f"team-{team.pk}")
    if not form.is_valid():
        return league_panel(request, league_id, team_forms={team.pk: form})
    form.save()
    messages.success(request, f"{team.name} saved.")
    return _back(league, f"team-{team.pk}")


@owner_required
@_post_only
def team_delete(request, league_id, team_id):
    league = _league(league_id)
    team = get_object_or_404(Team, pk=team_id, league=league)
    if Match.objects.filter(Q(home_team=team) | Q(away_team=team)).exists():
        messages.error(request, f"{team.name} already has games, so it can't be deleted.")
        return _back(league, "teams")
    with transaction.atomic():
        team.memberships.all().delete()
        team.delete()
    messages.info(request, f"Team {team.name} deleted.")
    return _back(league, "teams")


@owner_required
@_post_only
def player_add(request, league_id):
    league = _league(league_id)
    form = AddPlayerForm(request.POST, league=league)
    if not form.is_valid():
        return league_panel(request, league_id, player_form=form)
    data = form.cleaned_data
    add_player(team=data["team"], user=data["user"], role=data["role"], jersey_number=data["jersey_number"])
    messages.success(request, f"{data['user'].display_name} now plays for {data['team'].name}.")
    return _back(league, f"team-{data['team'].pk}")


def _membership(league, membership_id):
    return get_object_or_404(
        TeamMembership.objects.select_related("team", "user"),
        pk=membership_id,
        team__league=league,
        status=TeamMembership.Status.ACTIVE,
    )


@owner_required
@_post_only
def player_captain(request, league_id, membership_id):
    league = _league(league_id)
    membership = _membership(league, membership_id)
    make_captain(membership)
    messages.success(request, f"{membership.user.display_name} is now captain of {membership.team.name}.")
    return _back(league, f"team-{membership.team_id}")


@owner_required
@_post_only
def player_remove(request, league_id, membership_id):
    league = _league(league_id)
    membership = _membership(league, membership_id)
    membership.status = TeamMembership.Status.REMOVED
    membership.save(update_fields=("status",))
    messages.info(request, f"{membership.user.display_name} was taken off {membership.team.name}.")
    return _back(league, f"team-{membership.team_id}")


@owner_required
@_post_only
def round_generate(request, league_id):
    league = _league(league_id)
    form = NextRoundForm(request.POST, league=league)
    if not form.is_valid():
        return league_panel(request, league_id, round_form=form)
    try:
        number, matches = generate_next_round(league, form.cleaned_data)
    except (RoundError, ValidationError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        return _back(league, "games")
    messages.success(request, f"Round {number} created with {len(matches)} game{'s' if len(matches) != 1 else ''}.")
    return _back(league, "games")


@owner_required
@_post_only
def match_score(request, league_id, match_id):
    league = _league(league_id)
    match = get_object_or_404(Match.objects.select_related("home_team", "away_team"), pk=match_id, league=league)
    if match.status == Match.Status.CANCELLED:
        messages.error(request, "This game was cancelled.")
        return _back(league, "games")
    try:
        home_score = int(request.POST.get("home_score", ""))
        away_score = int(request.POST.get("away_score", ""))
        if home_score < 0 or away_score < 0:
            raise ValueError
        match.record_result(home_score=home_score, away_score=away_score)
    except (ValueError, ValidationError):
        messages.error(request, "Type a whole number of goals (0 or more) for both teams.")
        return _back(league, f"match-{match.pk}")
    messages.success(request, f"Saved: {match.home_team} {home_score}–{away_score} {match.away_team}. The table is updated.")
    return _back(league, f"match-{match.pk}")


@owner_required
@_post_only
def match_cancel(request, league_id, match_id):
    league = _league(league_id)
    with transaction.atomic():
        match = get_object_or_404(
            Match.objects.select_for_update(), pk=match_id, league=league, status=Match.Status.SCHEDULED
        )
        match.status = Match.Status.CANCELLED
        match.save(update_fields=("status",))
        event = Event.objects.select_for_update().get(pk=match.event_id)
        if event.status in (Event.Status.DRAFT, Event.Status.PENDING, Event.Status.PUBLISHED):
            cancel_event(event)
    messages.info(request, "Game cancelled. It no longer counts in the table.")
    return _back(league, "games")


@owner_required
@_post_only
def chat_delete(request, league_id, message_id):
    league = _league(league_id)
    get_object_or_404(LeagueChatMessage, pk=message_id, league=league).delete()
    messages.info(request, "Message deleted.")
    return _back(league, "chat")


@owner_required
@_post_only
def join_request_decide(request, league_id, request_id):
    league = _league(league_id)
    join_request = get_object_or_404(
        LeagueJoinRequest.objects.select_related("user"),
        pk=request_id,
        league=league,
        status=LeagueJoinRequest.Status.PENDING,
    )
    player = join_request.user
    if request.POST.get("decision") == "decline":
        join_request.status = LeagueJoinRequest.Status.DECLINED
        messages.info(request, f"{player.display_name}'s request was declined.")
    else:
        team = Team.objects.filter(pk=request.POST.get("team") or 0, league=league).first()
        if team is None:
            messages.error(request, "Pick a team first.")
            return _back(league, "requests")
        if TeamMembership.objects.filter(team__league=league, user=player, status=TeamMembership.Status.ACTIVE).exists():
            messages.error(request, f"{player.display_name} is already on a team.")
            return _back(league, "requests")
        role = request.POST.get("role")
        add_player(team=team, user=player, role=role if role in TeamMembership.Role.values else TeamMembership.Role.PLAYER)
        join_request.status = LeagueJoinRequest.Status.ACCEPTED
        messages.success(request, f"{player.display_name} now plays for {team.name}.")
    join_request.decided_at = timezone.now()
    join_request.save(update_fields=("status", "decided_at"))
    return _back(league, "requests")
