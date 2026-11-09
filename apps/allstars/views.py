from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from apps.organizers.models import OrganizerProfile

from . import services
from .forms import AllStarGameForm, AllStarRegistrationForm, choice_labels
from .models import MAX_RATING, MIN_RATING, AllStarGame, AllStarRegistration
from .services import AllStarError


def _post_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        return view(request, *args, **kwargs)

    return wrapper


def game_list(request):
    games = AllStarGame.objects.filter(status__in=AllStarGame.PUBLIC_STATUSES).select_related("community")
    return render(request, "allstars/game_list.html", {"games": games})


def _visible(request, game_id):
    game = get_object_or_404(AllStarGame.objects.select_related("community__organizer"), pk=game_id)
    if game.status not in AllStarGame.PUBLIC_STATUSES and not game.can_manage(request.user):
        raise Http404
    return game


def _detail_context(request, game, form=None):
    context = {
        "game": game,
        "player_count": game.registrations.filter(status=AllStarRegistration.Status.ACTIVE).count(),
        "can_manage": game.can_manage(request.user),
    }
    if not request.user.is_authenticated:
        return context
    mine = game.registrations.filter(user=request.user).select_related("team").first()
    context["my_registration"] = mine
    can_register = game.accepting_registrations and not (mine and mine.status == AllStarRegistration.Status.REMOVED)
    if can_register:
        initial = {"position": mine.position, "gender": mine.gender, "note": mine.note} if mine else None
        context["form"] = form or AllStarRegistrationForm(game=game, initial=initial)
    # Rosters are for signed-in members, and ratings never appear here.
    if game.status == AllStarGame.Status.PUBLISHED:
        labels = choice_labels("player_position")
        teams = list(game.teams.all())
        for team in teams:
            team.players = list(
                team.registrations.filter(status=AllStarRegistration.Status.ACTIVE)
                .select_related("user")
                .order_by("position", "user__first_name", "user__username")
            )
            for player in team.players:
                player.position_label = labels.get(player.position, player.position)
        context["teams"] = teams
    return context


def game_detail(request, game_id):
    game = _visible(request, game_id)
    return render(request, "allstars/game_detail.html", _detail_context(request, game))


@login_required
@_post_only
def game_register(request, game_id):
    game = _visible(request, game_id)
    form = AllStarRegistrationForm(request.POST, game=game)
    if not form.is_valid():
        return render(request, "allstars/game_detail.html", _detail_context(request, game, form=form))
    try:
        services.register(game, request.user, **form.cleaned_data)
    except AllStarError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"You're on the list for {game.title}.")
    return redirect("allstars:detail", game_id=game.pk)


@login_required
@_post_only
def game_withdraw(request, game_id):
    game = _visible(request, game_id)
    try:
        services.withdraw(game, request.user)
    except AllStarError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "You've been taken off the list.")
    return redirect("allstars:detail", game_id=game.pk)


def _active_profile(user):
    return OrganizerProfile.objects.filter(user=user, is_active=True).first()


@login_required
def game_create(request):
    profile = _active_profile(request.user)
    if profile is None or not profile.communities.filter(is_active=True).exists():
        messages.info(request, "An All-Star game belongs to a community, so start one first.")
        return redirect("communities:create")
    form = AllStarGameForm(request.POST or None, organizer=profile, initial={"community": request.GET.get("community")})
    if form.is_valid():
        game = form.save()
        messages.success(request, "All-Star game created. Open registration when you're ready.")
        return redirect("allstars:manage", game_id=game.pk)
    return render(request, "allstars/game_form.html", {"form": form, "heading": "Create an All-Star game"})


def _managed(request, game_id):
    game = get_object_or_404(AllStarGame.objects.select_related("community__organizer"), pk=game_id)
    if not game.can_manage(request.user):
        raise Http404
    return game


@login_required
def game_update(request, game_id):
    game = _managed(request, game_id)
    if game.status in (AllStarGame.Status.PUBLISHED, AllStarGame.Status.CANCELLED):
        return HttpResponseForbidden("Announced or cancelled games cannot be edited.")
    form = AllStarGameForm(request.POST or None, instance=game, organizer=game.community.organizer)
    if form.is_valid():
        form.save()
        messages.success(request, "Game details saved.")
        return redirect("allstars:manage", game_id=game.pk)
    return render(request, "allstars/game_form.html", {"form": form, "heading": f"Edit {game.title}", "game": game})


@login_required
def game_manage(request, game_id):
    game = _managed(request, game_id)
    position_labels = choice_labels("player_position")
    gender_labels = choice_labels("gender")
    registrations = list(
        game.registrations.filter(status=AllStarRegistration.Status.ACTIVE)
        .select_related("user", "team")
        .order_by("user__first_name", "user__username")
    )
    for registration in registrations:
        registration.position_label = position_labels.get(registration.position, registration.position)
        registration.gender_label = gender_labels.get(registration.gender, registration.gender)

    teams = list(game.teams.all())
    for team in teams:
        team.players = [r for r in registrations if r.team_id == team.pk]
        team.total = sum(r.skill_rating or 0 for r in team.players)
        counts = {}
        for player in team.players:
            counts[player.position_label] = counts.get(player.position_label, 0) + 1
        team.position_counts = sorted(counts.items())
    assigned = [team for team in teams if team.players]
    return render(
        request,
        "allstars/game_manage.html",
        {
            "game": game,
            "registrations": registrations,
            "teams": teams,
            "rating_options": range(MIN_RATING, MAX_RATING + 1),
            "unrated_count": sum(r.skill_rating is None for r in registrations),
            "unassigned_count": sum(r.team_id is None for r in registrations) if teams else 0,
            "spread": max(t.total for t in assigned) - min(t.total for t in assigned) if assigned else None,
            "can_edit": game.status not in (AllStarGame.Status.PUBLISHED, AllStarGame.Status.CANCELLED),
        },
    )


def _run(request, game, action, success):
    try:
        action()
    except AllStarError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, success)
    return redirect("allstars:manage", game_id=game.pk)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


STATUS_MESSAGES = {
    "open": "Registration is open.",
    "close": "Registration is closed. Rate the players, then balance the teams.",
    "publish": "Teams announced. Players can now see them.",
    "unpublish": "Teams taken back so you can change them.",
    "cancel": "The game was cancelled.",
}


@login_required
@_post_only
def game_status(request, game_id):
    game = _managed(request, game_id)
    action = request.POST.get("action", "")
    return _run(request, game, lambda: services.change_status(game, action), STATUS_MESSAGES.get(action, "Done."))


@login_required
@_post_only
def game_ratings(request, game_id):
    game = _managed(request, game_id)
    ratings = {}
    for key, value in request.POST.items():
        if not key.startswith("rating_"):
            continue
        registration_id = _int(key.removeprefix("rating_"))
        rating = None if value == "" else _int(value)
        if registration_id is None or (value != "" and rating is None):
            messages.error(request, "Those ratings could not be read. Nothing was saved.")
            return redirect("allstars:manage", game_id=game.pk)
        ratings[registration_id] = rating
    return _run(request, game, lambda: services.save_ratings(game, ratings), "Ratings saved.")


@login_required
@_post_only
def game_balance(request, game_id):
    game = _managed(request, game_id)
    return _run(request, game, lambda: services.balance_teams(game), "Teams balanced. Move players by hand if you want.")


@login_required
@_post_only
def player_move(request, game_id, registration_id):
    game = _managed(request, game_id)
    team_id = _int(request.POST.get("team"))
    return _run(request, game, lambda: services.move_player(game, registration_id, team_id), "Player moved.")


@login_required
@_post_only
def player_remove(request, game_id, registration_id):
    game = _managed(request, game_id)
    return _run(request, game, lambda: services.remove_player(game, registration_id), "Player removed from the game.")


@login_required
@_post_only
def team_rename(request, game_id, team_id):
    game = _managed(request, game_id)
    return _run(
        request, game, lambda: services.rename_team(game, team_id, request.POST.get("name", "")), "Team renamed."
    )
