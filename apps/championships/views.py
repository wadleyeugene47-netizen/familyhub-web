from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import Http404, HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.organizers.models import OrganizerProfile

from .forms import (
    BulkTeamForm,
    ChampionshipForm,
    IncidentFormSet,
    MatchScheduleForm,
    ResultForm,
    TeamForm,
    incident_initial,
    incidents_from_formset,
)
from .models import Championship, Match, Team
from .scheduling import ROUND_ORDER, configuration_errors, distribute_teams, ensure_groups, team_errors
from .services import (
    ProgressionConflict,
    champion,
    clear_fixtures,
    clear_result,
    generate_fixtures,
    publish,
    readiness_errors,
    record_result,
    update_schedule,
    update_team,
)
from .standings import championship_tables, team_records, tie_breaker_steps, top_scorers


def _messages_from(error):
    return error.messages if hasattr(error, "messages") else [str(error)]


def _managed(request, championship_id):
    championship = get_object_or_404(
        Championship.objects.select_related("organizer", "location", "entry_event"), pk=championship_id
    )
    if not championship.can_manage(request.user):
        raise Http404("Championship not found.")
    return championship


def _visible(request, championship_id):
    championship = get_object_or_404(
        Championship.objects.select_related("organizer", "location", "entry_event"), pk=championship_id
    )
    if not championship.is_public and not championship.can_manage(request.user):
        raise Http404("Championship not found.")
    return championship


def _map_urls(location):
    if location is None:
        return None, None
    if location.latitude is not None and location.longitude is not None:
        latitude, longitude = location.latitude, location.longitude
        offset = Decimal("0.01")
        bbox = ",".join(
            str(value)
            for value in (
                max(Decimal("-180"), longitude - offset),
                max(Decimal("-90"), latitude - offset),
                min(Decimal("180"), longitude + offset),
                min(Decimal("90"), latitude + offset),
            )
        )
        embed = "https://www.openstreetmap.org/export/embed.html?" + urlencode(
            {"bbox": bbox, "layer": "mapnik", "marker": f"{latitude},{longitude}"}
        )
        return embed, "https://www.openstreetmap.org/directions?" + urlencode({"to": f"{latitude},{longitude}"})
    address = ", ".join(
        value
        for value in (location.address_line, location.city, location.region, location.postal_code, location.country_code)
        if value
    )
    return None, "https://www.openstreetmap.org/search?" + urlencode({"query": address})


def _sorted_matches(championship):
    matches = list(
        championship.matches.select_related(
            "group", "home_team", "away_team", "winner", "home_source_group", "away_source_group",
            "home_source_match", "away_source_match",
        ).prefetch_related("incidents")
    )
    matches.sort(
        key=lambda match: (
            match.kickoff_at is None,
            match.kickoff_at or timezone.now(),
            match.stage != Match.Stage.GROUP,
            match.round_number,
            match.bracket_position or 0,
            match.pk,
        )
    )
    zone = championship.zone
    for match in matches:
        match.championship = championship
        match.local_day = match.kickoff_at.astimezone(zone).date() if match.kickoff_at else None
    return matches


def _bracket(matches):
    knockout = [match for match in matches if match.is_knockout]
    rounds = []
    for code in ROUND_ORDER:
        if code == Match.Round.THIRD_PLACE:
            continue
        in_round = sorted((match for match in knockout if match.knockout_round == code), key=lambda match: match.bracket_position)
        if in_round:
            rounds.append({"code": code, "label": Match.Round(code).label + ("" if code == Match.Round.FINAL else "s"), "matches": in_round})
    third = next((match for match in knockout if match.knockout_round == Match.Round.THIRD_PLACE), None)
    return rounds, third


def live_version(championship):
    latest = championship.matches.aggregate(latest=Max("updated_at"))["latest"]
    last_audit = championship.audit_entries.aggregate(latest=Max("pk"))["latest"] or 0
    stamp = latest.timestamp() if latest else 0
    return f"{championship.updated_at.timestamp():.0f}-{stamp:.3f}-{last_audit}"


def _championship_context(championship):
    matches = _sorted_matches(championship)
    tables = championship_tables(championship) if championship.has_groups else []
    rounds, third_place = _bracket(matches)
    now = timezone.now()
    open_statuses = (Match.Status.SCHEDULED, Match.Status.IN_PROGRESS, Match.Status.POSTPONED)
    upcoming = [
        match
        for match in matches
        if match.status in (Match.Status.SCHEDULED, Match.Status.IN_PROGRESS)
        and (match.kickoff_at is None or match.kickoff_at >= now - timedelta(hours=3))
    ]
    upcoming.sort(key=lambda match: (match.status != Match.Status.IN_PROGRESS, match.kickoff_at is None, match.kickoff_at or now))
    completed = [match for match in matches if match.is_completed]
    map_embed_url, directions_url = _map_urls(championship.location)
    return {
        "championship": championship,
        "matches": matches,
        "tables": tables,
        "bracket_rounds": rounds,
        "third_place": third_place,
        "next_matches": upcoming[:4],
        "recent_results": sorted(completed, key=lambda match: match.result_recorded_at or now, reverse=True)[:4],
        "team_records": team_records(championship, matches),
        "top_scorers": top_scorers(championship)[:10],
        "champion": champion(championship),
        "tie_breaker_steps": tie_breaker_steps(championship),
        "completed_count": len(completed),
        "open_count": sum(1 for match in matches if match.status in open_statuses),
        "map_embed_url": map_embed_url,
        "directions_url": directions_url,
        "live_version": live_version(championship),
    }


# Public pages -----------------------------------------------------------


def championship_list(request):
    championships = Championship.objects.filter(
        status__in=[Championship.Status.PUBLISHED, Championship.Status.COMPLETED]
    ).select_related("organizer", "location")
    return render(request, "championships/championship_list.html", {"championships": championships})


def championship_detail(request, championship_id):
    championship = _visible(request, championship_id)
    context = _championship_context(championship)
    context["can_manage"] = championship.can_manage(request.user)
    return render(request, "championships/championship_detail.html", context)


def championship_live(request, championship_id):
    championship = _visible(request, championship_id)
    return JsonResponse({"version": live_version(championship), "status": championship.status})


# Organizer pages --------------------------------------------------------


@login_required
def organizer_championships(request):
    championships = Championship.objects.filter(organizer__user=request.user).prefetch_related("teams", "matches")
    return render(request, "championships/organizer_championships.html", {"championships": championships})


def _organizer_profile(request):
    profile, _created = OrganizerProfile.objects.get_or_create(
        user=request.user,
        defaults={"display_name": request.user.get_full_name() or request.user.username},
    )
    return profile


@login_required
def championship_create(request):
    profile = _organizer_profile(request)
    if not profile.is_active:
        return HttpResponseForbidden("This organizer profile is inactive.")
    form = ChampionshipForm(
        request.POST or None,
        request.FILES or None,
        organizer=profile,
        initial={"community": request.GET.get("community")},
    )
    if form.is_valid():
        with transaction.atomic():
            championship = form.save()
            ensure_groups(championship)
        messages.success(request, "Championship created. Register the teams next.")
        return redirect("championships:manage", championship_id=championship.pk)
    return render(
        request,
        "championships/championship_form.html",
        {"form": form, "heading": "Create championship", "championship": None},
    )


@login_required
def championship_update(request, championship_id):
    championship = _managed(request, championship_id)
    if championship.status == Championship.Status.CANCELLED:
        return HttpResponseForbidden("Cancelled championships cannot be edited.")
    form = ChampionshipForm(request.POST or None, request.FILES or None, instance=championship, organizer=championship.organizer)
    if form.is_valid():
        with transaction.atomic():
            championship = form.save()
            if not championship.matches.exists():
                ensure_groups(championship)
        messages.success(request, "Championship settings saved.")
        return redirect("championships:manage", championship_id=championship.pk)
    return render(
        request,
        "championships/championship_form.html",
        {"form": form, "heading": f"Edit {championship.name}", "championship": championship},
    )


@login_required
def championship_manage(request, championship_id):
    championship = _managed(request, championship_id)
    context = _championship_context(championship)
    status_filter = request.GET.get("show", "all")
    matches = context["matches"]
    if status_filter == "open":
        matches = [match for match in matches if not match.is_completed and match.status != Match.Status.CANCELLED]
    elif status_filter == "completed":
        matches = [match for match in matches if match.is_completed]
    teams = list(championship.teams.select_related("group"))
    context.update(
        {
            "managed_matches": matches,
            "status_filter": status_filter,
            "teams": teams,
            "unassigned_teams": [team for team in teams if team.group_id is None],
            "groups": list(championship.groups.all()),
            "has_fixtures": bool(context["matches"]),
            "has_results": context["completed_count"] > 0,
            "configuration_errors": configuration_errors(championship),
            "team_errors": team_errors(championship),
            "readiness_errors": readiness_errors(championship),
            "audit_entries": championship.audit_entries.select_related("user", "match")[:25],
            "places_left": max(championship.team_count - len(teams), 0),
        }
    )
    return render(request, "championships/championship_manage.html", context)


def _require_post(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    return None


def _manage_url(championship, anchor=""):
    url = reverse("championships:manage", args=[championship.pk])
    return f"{url}#{anchor}" if anchor else url


@login_required
def team_add(request, championship_id):
    championship = _managed(request, championship_id)
    if championship.matches.exists():
        messages.error(request, "Clear the fixtures before changing the registered teams.")
        return redirect(_manage_url(championship, "teams"))
    mode = request.POST.get("mode", "single") if request.method == "POST" else "single"
    form = TeamForm(request.POST if mode == "single" else None, championship=championship)
    bulk_form = BulkTeamForm(request.POST if mode == "bulk" else None, championship=championship)
    if request.method == "POST":
        if mode == "single" and form.is_valid():
            form.save()
            messages.success(request, f"{form.instance.name} registered.")
            return redirect(_manage_url(championship, "teams"))
        if mode == "bulk" and bulk_form.is_valid():
            with transaction.atomic():
                Team.objects.bulk_create(
                    [Team(championship=championship, name=name) for name in bulk_form.cleaned_data["names"]]
                )
            messages.success(request, f"{len(bulk_form.cleaned_data['names'])} teams registered.")
            return redirect(_manage_url(championship, "teams"))
    return render(
        request,
        "championships/team_form.html",
        {"championship": championship, "form": form, "bulk_form": bulk_form, "heading": "Register teams"},
    )


@login_required
def team_update(request, championship_id, team_id):
    championship = _managed(request, championship_id)
    team = get_object_or_404(Team, pk=team_id, championship=championship)
    form = TeamForm(request.POST or None, instance=team, championship=championship)
    if form.is_valid():
        try:
            update_team(form, user=request.user)
        except ProgressionConflict as error:
            form.add_error(None, f"{error} Clear those results first, then change the team.")
        else:
            messages.success(request, f"{team.name} updated. Standings and bracket use the new details immediately.")
            return redirect(_manage_url(championship, "teams"))
    return render(
        request,
        "championships/team_form.html",
        {"championship": championship, "form": form, "bulk_form": None, "heading": f"Edit {team.name}", "team": team},
    )


@login_required
def team_delete(request, championship_id, team_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    team = get_object_or_404(Team, pk=team_id, championship=championship)
    if championship.matches.exists():
        messages.error(request, "Clear the fixtures before removing a team.")
    else:
        team.delete()
        messages.success(request, f"{team.name} removed.")
    return redirect(_manage_url(championship, "teams"))


@login_required
def groups_distribute(request, championship_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    if championship.matches.exists():
        messages.error(request, "Clear the fixtures before redrawing the groups.")
    elif not championship.has_groups:
        messages.error(request, "This format has no groups.")
    else:
        with transaction.atomic():
            distribute_teams(championship, reset=True)
        messages.success(request, "Teams distributed into groups by seed.")
    return redirect(_manage_url(championship, "teams"))


@login_required
def fixtures_generate(request, championship_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    try:
        created = generate_fixtures(championship, user=request.user)
    except ValidationError as error:
        for message in _messages_from(error):
            messages.error(request, message)
    else:
        messages.success(request, f"{len(created)} fixtures generated. Review the schedule before publishing.")
    return redirect(_manage_url(championship, "fixtures"))


@login_required
def fixtures_clear(request, championship_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    try:
        clear_fixtures(championship, user=request.user)
    except ValidationError as error:
        for message in _messages_from(error):
            messages.error(request, message)
    else:
        messages.success(request, "Fixtures cleared.")
    return redirect(_manage_url(championship, "fixtures"))


@login_required
def championship_publish(request, championship_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    try:
        publish(championship, user=request.user)
    except ValidationError as error:
        for message in _messages_from(error):
            messages.error(request, message)
    else:
        messages.success(request, "Championship published. Fixtures and standings are now public.")
    return redirect(_manage_url(championship))


def _get_match(championship, match_id):
    match = get_object_or_404(
        Match.objects.select_related("group", "home_team", "away_team", "winner"), pk=match_id, championship=championship
    )
    match.championship = championship
    return match


def _saved_messages(request, match, reset):
    messages.success(request, f"Result saved: {match.home_team.name} {match.home_score}–{match.away_score} {match.away_team.name}. Standings and bracket updated.")
    if reset:
        messages.warning(
            request,
            f"{len(reset)} later knockout result(s) were cleared because their teams changed. Re-enter them.",
        )


@login_required
def match_result(request, championship_id, match_id):
    championship = _managed(request, championship_id)
    match = _get_match(championship, match_id)
    conflict = None
    if request.method == "POST":
        form = ResultForm(request.POST, match=match)
        formset = IncidentFormSet(request.POST, prefix="incidents", form_kwargs={"match": match})
        if form.is_valid() and formset.is_valid():
            data = form.cleaned_data
            try:
                saved, reset = record_result(
                    match,
                    user=request.user,
                    home_score=data["home_score"],
                    away_score=data["away_score"],
                    extra_time=data["extra_time"],
                    home_penalties=data["home_penalties"],
                    away_penalties=data["away_penalties"],
                    winner_side=data["winner_side"],
                    incidents=incidents_from_formset(formset, match),
                    allow_reset=data["confirm_reset"],
                    note=data["note"],
                )
            except ProgressionConflict as error:
                conflict = error.matches
            except ValidationError as error:
                for message in _messages_from(error):
                    form.add_error(None, message)
            else:
                _saved_messages(request, saved, reset)
                return redirect(_manage_url(championship, f"match-{saved.pk}"))
    else:
        initial = {}
        if match.is_completed:
            initial = {
                "home_score": match.home_score,
                "away_score": match.away_score,
                "extra_time": match.decided_by == Match.Decision.EXTRA_TIME
                or (
                    match.decided_by == Match.Decision.PENALTIES
                    and championship.knockout_tie_rule == Championship.KnockoutTieRule.EXTRA_TIME_PENALTIES
                ),
                "home_penalties": match.home_penalties,
                "away_penalties": match.away_penalties,
                "winner_side": ("home" if match.winner_id == match.home_team_id else "away")
                if match.decided_by == Match.Decision.ORGANIZER
                else "",
            }
        form = ResultForm(initial=initial, match=match)
        formset = IncidentFormSet(prefix="incidents", initial=incident_initial(match), form_kwargs={"match": match})
    return render(
        request,
        "championships/match_result.html",
        {
            "championship": championship,
            "match": match,
            "form": form,
            "formset": formset,
            "conflict": conflict,
            "clear_conflict": None,
        },
    )


@login_required
def match_clear(request, championship_id, match_id):
    if (response := _require_post(request)) is not None:
        return response
    championship = _managed(request, championship_id)
    match = _get_match(championship, match_id)
    try:
        clear_result(
            match,
            user=request.user,
            allow_reset=request.POST.get("confirm_reset") == "on",
            note=request.POST.get("note", "")[:255],
        )
    except ProgressionConflict as error:
        return render(
            request,
            "championships/match_result.html",
            {
                "championship": championship,
                "match": match,
                "form": ResultForm(match=match),
                "formset": IncidentFormSet(prefix="incidents", initial=incident_initial(match), form_kwargs={"match": match}),
                "conflict": None,
                "clear_conflict": error.matches,
            },
        )
    except ValidationError as error:
        for message in _messages_from(error):
            messages.error(request, message)
        return redirect(_manage_url(championship, f"match-{match.pk}"))
    messages.success(request, "Result cleared. Standings and bracket recalculated.")
    return redirect(_manage_url(championship, f"match-{match.pk}"))


@login_required
def match_schedule(request, championship_id, match_id):
    championship = _managed(request, championship_id)
    match = _get_match(championship, match_id)
    form = MatchScheduleForm(request.POST or None, match=match)
    conflict = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_schedule(
                match,
                user=request.user,
                kickoff_at=data["kickoff_at"],
                field_name=data["field_name"].strip(),
                status=data["status"],
                allow_reset=data["confirm_reset"],
                note=data["note"],
            )
        except ProgressionConflict as error:
            conflict = error.matches
        except ValidationError as error:
            for message in _messages_from(error):
                form.add_error(None, message)
        else:
            messages.success(request, "Match schedule saved.")
            return redirect(_manage_url(championship, f"match-{match.pk}"))
    return render(
        request,
        "championships/match_schedule.html",
        {"championship": championship, "match": match, "form": form, "conflict": conflict},
    )
