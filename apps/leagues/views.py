from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event
from apps.organizers.models import OrganizerProfile

from .forms import CaptainAddPlayerForm, CaptainTeamForm, LeagueForm, MatchScheduleForm, TeamForm
from .models import League, LeagueChatMessage, LeagueJoinRequest, Match, TeamMembership
from .services import captain_add_player, captain_remove_player, set_jersey_numbers
from .standings import calculate_standings


def league_list(request):
    leagues = League.objects.filter(
        status__in=[League.Status.ACTIVE, League.Status.COMPLETED]
    ).select_related("organizer")
    return render(request, "leagues/league_list.html", {"leagues": leagues})


def _visible_league(league_id):
    return get_object_or_404(
        League.objects.select_related("organizer"),
        pk=league_id,
        status__in=[League.Status.ACTIVE, League.Status.COMPLETED],
    )


def league_detail(request, league_id):
    league = _visible_league(league_id)
    matches = league.matches.select_related(
        "event", "event__location", "home_team", "away_team"
    ).order_by("round_number", "event__starts_at")
    context = {
        "league": league,
        "matches": matches,
        "standings": calculate_standings(league),
    }
    # Teams, members and the chat are only for signed-in (and therefore approved) members.
    if request.user.is_authenticated:
        memberships = TeamMembership.objects.filter(
            team__league=league, status=TeamMembership.Status.ACTIVE
        ).select_related("team", "user")
        teams = {team.pk: team for team in league.teams.all()}
        for team in teams.values():
            team.roster = []
        for membership in memberships:
            teams[membership.team_id].roster.append(membership)
        User = get_user_model()
        context.update(
            {
                "teams": teams.values(),
                "my_membership": next((m for m in memberships if m.user_id == request.user.pk), None),
                "supporters": User.objects.filter(is_active=True, approval_status=User.Approval.APPROVED)
                .exclude(pk__in=[m.user_id for m in memberships])
                .order_by("first_name", "username"),
                "chat_messages": list(league.chat_messages.select_related("sender").order_by("-id")[:50])[::-1],
                "my_join_request": league.join_requests.filter(user=request.user).order_by("-created_at").first(),
            }
        )
    return render(request, "leagues/league_detail.html", context)


@login_required
def league_join(request, league_id):
    if request.method != "POST":
        return redirect("leagues:detail", league_id=league_id)
    league = _visible_league(league_id)
    playing = TeamMembership.objects.filter(
        team__league=league, user=request.user, status=TeamMembership.Status.ACTIVE
    ).exists()
    if playing:
        messages.info(request, "You're already on a team.")
    elif league.join_requests.filter(user=request.user, status=LeagueJoinRequest.Status.PENDING).exists():
        messages.info(request, "Your request is already with the host.")
    else:
        LeagueJoinRequest.objects.create(
            league=league, user=request.user, note=request.POST.get("note", "").strip()[:200]
        )
        messages.success(request, "Request sent! The host will put you on a team.")
    return redirect("leagues:detail", league_id=league.pk)


@login_required
def league_chat(request, league_id):
    league = _visible_league(league_id)
    if request.method == "POST":
        body = request.POST.get("body", "").strip()[: LeagueChatMessage.MAX_LENGTH]
        if body:
            LeagueChatMessage.objects.create(league=league, sender=request.user, body=body)
        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({"ok": bool(body)}, status=200 if body else 400)
        return redirect(reverse("leagues:detail", args=[league.pk]) + "#chat")
    try:
        after = max(0, int(request.GET.get("after", 0)))
    except ValueError:
        after = 0
    new_messages = league.chat_messages.filter(id__gt=after).select_related("sender")[:100]
    return JsonResponse(
        {
            "messages": [
                {
                    "id": message.pk,
                    "body": message.body,
                    "mine": message.sender_id == request.user.pk,
                    "sender": message.sender.get_full_name() or message.sender.username,
                    "time": timezone.localtime(message.created_at).strftime("%d %b %H:%M"),
                }
                for message in new_messages
            ]
        }
    )


@login_required
def organizer_leagues(request):
    profile = OrganizerProfile.objects.filter(user=request.user, is_active=True).first()
    leagues = League.objects.none() if profile is None else profile.leagues.prefetch_related("teams")
    return render(request, "leagues/organizer_leagues.html", {"leagues": leagues})


@login_required
def league_create(request):
    profile = OrganizerProfile.objects.filter(user=request.user, is_active=True).first()
    if profile is None:
        messages.error(request, "Create an active organizer profile before creating a league.")
        return redirect("accounts:dashboard")
    form = LeagueForm(request.POST or None, organizer=profile, initial={"community": request.GET.get("community")})
    if form.is_valid():
        league = form.save(commit=False)
        league.organizer = profile
        league.save()
        return redirect("leagues:manage", league_id=league.pk)
    return render(request, "leagues/league_form.html", {"form": form, "heading": "Create league"})


def _captain_memberships(user):
    return TeamMembership.objects.filter(
        user=user,
        role=TeamMembership.Role.CAPTAIN,
        status=TeamMembership.Status.ACTIVE,
        team__league__status__in=[League.Status.ACTIVE, League.Status.COMPLETED],
    ).select_related("team", "team__league")


@login_required
def captain_dashboard(request):
    teams = []
    standings_by_league = {}
    now = timezone.now()
    for membership in _captain_memberships(request.user):
        team = membership.team
        league = team.league
        table = standings_by_league.setdefault(league.pk, calculate_standings(league))
        team.position, team.row = next(
            (place, row) for place, row in enumerate(table, start=1) if row["team"].pk == team.pk
        )
        team.league_size = len(table)
        team.roster = list(team.active_memberships().order_by("jersey_number", "user__first_name", "user__username"))
        matches = Match.objects.filter(Q(home_team=team) | Q(away_team=team)).select_related(
            "event", "event__location", "home_team", "away_team"
        )
        team.upcoming = matches.filter(status=Match.Status.SCHEDULED, event__starts_at__gte=now).order_by(
            "event__starts_at"
        )[:5]
        team.results = matches.filter(status=Match.Status.COMPLETED).order_by("-event__starts_at")[:5]
        team.can_edit_roster = league.status == League.Status.ACTIVE
        if team.can_edit_roster:
            team.edit_form = CaptainTeamForm(instance=team, prefix=f"team-{team.pk}")
            team.add_form = CaptainAddPlayerForm(league=league, prefix=f"add-{team.pk}")
            team.join_requests = league.join_requests.filter(status=LeagueJoinRequest.Status.PENDING).select_related(
                "user"
            )
        teams.append(team)
    return render(request, "leagues/captain_dashboard.html", {"teams": teams})


def _captained_team(request, team_id):
    """The team, if this person captains it and its league is still running. Anyone else gets a 404."""
    return get_object_or_404(
        _captain_memberships(request.user), team_id=team_id, team__league__status=League.Status.ACTIVE
    ).team


def _first_error(form):
    return next(iter(form.errors.values()))[0]


@login_required
def captain_team_edit(request, team_id):
    if request.method != "POST":
        return redirect("leagues:captain")
    team = _captained_team(request, team_id)
    form = CaptainTeamForm(request.POST, instance=team, prefix=f"team-{team.pk}")
    if form.is_valid():
        form.save()
        messages.success(request, "Team details saved.")
    else:
        messages.error(request, _first_error(form))
    return redirect("leagues:captain")


@login_required
def captain_player_add(request, team_id):
    if request.method != "POST":
        return redirect("leagues:captain")
    team = _captained_team(request, team_id)
    form = CaptainAddPlayerForm(request.POST, league=team.league, prefix=f"add-{team.pk}")
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("leagues:captain")
    user = form.cleaned_data["user"]
    try:
        captain_add_player(team, user, jersey_number=form.cleaned_data["jersey_number"])
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"{user.display_name} now plays for {team.name}.")
    return redirect("leagues:captain")


@login_required
def captain_player_remove(request, team_id, membership_id):
    if request.method != "POST":
        return redirect("leagues:captain")
    team = _captained_team(request, team_id)
    try:
        membership = captain_remove_player(team, membership_id)
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        messages.info(request, f"{membership.user.display_name} was taken off {team.name}.")
    return redirect("leagues:captain")


@login_required
def captain_roster(request, team_id):
    if request.method != "POST":
        return redirect("leagues:captain")
    team = _captained_team(request, team_id)
    numbers = {}
    try:
        for key, value in request.POST.items():
            if key.startswith("jersey_"):
                numbers[int(key.removeprefix("jersey_"))] = int(value) if value.strip() else None
    except ValueError:
        messages.error(request, "Shirt numbers must be whole numbers.")
        return redirect("leagues:captain")
    try:
        set_jersey_numbers(team, numbers)
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Shirt numbers saved.")
    return redirect("leagues:captain")


def _get_owned_league(request, league_id):
    return get_object_or_404(League, pk=league_id, organizer__user=request.user)


@login_required
def league_manage(request, league_id):
    league = _get_owned_league(request, league_id)
    teams = league.teams.all()
    matches = league.matches.select_related("event", "home_team", "away_team")
    return render(
        request,
        "leagues/league_manage.html",
        {"league": league, "teams": teams, "matches": matches},
    )


@login_required
def league_update(request, league_id):
    league = _get_owned_league(request, league_id)
    if league.status in [League.Status.COMPLETED, League.Status.CANCELLED]:
        return HttpResponseForbidden("Completed or cancelled leagues cannot be edited.")
    form = LeagueForm(request.POST or None, instance=league, organizer=league.organizer)
    if form.is_valid():
        league = form.save()
        return redirect("leagues:manage", league_id=league.pk)
    return render(request, "leagues/league_form.html", {"form": form, "heading": "Edit league"})


@login_required
def team_create(request, league_id):
    league = _get_owned_league(request, league_id)
    if league.status not in [League.Status.DRAFT, League.Status.ACTIVE]:
        return HttpResponseForbidden("Teams cannot be added to this league.")
    form = TeamForm(request.POST or None)
    if form.is_valid():
        team = form.save(commit=False)
        team.league = league
        team.save()
        return redirect("leagues:manage", league_id=league.pk)
    return render(
        request,
        "leagues/team_form.html",
        {"form": form, "league": league},
    )


@login_required
def league_activate(request, league_id):
    if request.method != "POST":
        return HttpResponseForbidden("Use the activation form to activate a league.")
    with transaction.atomic():
        league = get_object_or_404(
            League.objects.select_for_update(),
            pk=league_id,
            organizer__user=request.user,
            status=League.Status.DRAFT,
        )
        if league.teams.count() < 2:
            messages.error(request, "Add at least two teams before activating the league.")
            return redirect("leagues:manage", league_id=league.pk)
        league.status = League.Status.ACTIVE
        league.save(update_fields=("status", "updated_at"))
        Event.objects.filter(soccer_match__league=league, status=Event.Status.DRAFT).update(
            status=Event.Status.PUBLISHED,
            updated_at=timezone.now(),
        )
    messages.success(request, "League activated and scheduled fixtures published.")
    return redirect("leagues:manage", league_id=league.pk)


@login_required
def match_schedule(request, league_id):
    league = _get_owned_league(request, league_id)
    if league.status not in [League.Status.DRAFT, League.Status.ACTIVE]:
        return HttpResponseForbidden("Matches cannot be scheduled for this league.")
    form = MatchScheduleForm(request.POST or None, league=league)
    if form.is_valid():
        match = form.save(league=league, organizer=league.organizer)
        messages.success(request, "Match scheduled.")
        return redirect("leagues:manage", league_id=league.pk)
    return render(
        request,
        "leagues/match_form.html",
        {"form": form, "league": league, "heading": "Schedule match"},
    )


@login_required
def match_result(request, league_id, match_id):
    league = _get_owned_league(request, league_id)
    match = get_object_or_404(Match, pk=match_id, league=league)
    if request.method != "POST":
        return render(request, "leagues/result_form.html", {"league": league, "match": match})
    try:
        home_score = int(request.POST.get("home_score", ""))
        away_score = int(request.POST.get("away_score", ""))
        if home_score < 0 or away_score < 0:
            raise ValueError
        match.record_result(home_score=home_score, away_score=away_score)
    except (ValueError, ValidationError):
        messages.error(request, "Enter whole, non-negative scores for both teams.")
        return render(request, "leagues/result_form.html", {"league": league, "match": match})
    messages.success(request, "Match result recorded.")
    return redirect("leagues:manage", league_id=league.pk)
