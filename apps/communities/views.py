from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.allstars.models import AllStarGame
from apps.championships.models import Championship
from apps.events.models import Event
from apps.leagues.models import League, LeagueJoinRequest
from apps.organizers.models import OrganizerProfile

from .forms import CommunityForm
from .models import Community

PUBLIC_LEAGUES = [League.Status.ACTIVE, League.Status.COMPLETED]
PUBLIC_TOURNAMENTS = [Championship.Status.PUBLISHED, Championship.Status.COMPLETED]


def community_list(request):
    communities = (
        Community.objects.filter(is_active=True)
        .select_related("organizer")
        .annotate(
            league_count=Count("leagues", filter=Q(leagues__status__in=PUBLIC_LEAGUES), distinct=True),
            tournament_count=Count(
                "championships", filter=Q(championships__status__in=PUBLIC_TOURNAMENTS), distinct=True
            ),
            all_star_count=Count(
                "all_star_games", filter=Q(all_star_games__status__in=AllStarGame.PUBLIC_STATUSES), distinct=True
            ),
        )
    )
    mine = []
    if request.user.is_authenticated:
        mine = [community for community in communities if community.is_organized_by(request.user)]
    return render(request, "communities/community_list.html", {"communities": communities, "mine": mine})


def community_detail(request, slug):
    community = get_object_or_404(Community.objects.select_related("organizer"), slug=slug, is_active=True)
    events = community.events.filter(
        status=Event.Status.PUBLISHED, starts_at__gte=timezone.now(), soccer_match__isnull=True
    ).select_related("location")[:6]
    return render(
        request,
        "communities/community_detail.html",
        {
            "community": community,
            "leagues": community.leagues.filter(status__in=PUBLIC_LEAGUES),
            "tournaments": community.championships.filter(status__in=PUBLIC_TOURNAMENTS),
            "all_star_games": community.all_star_games.filter(status__in=AllStarGame.PUBLIC_STATUSES),
            "events": events,
            "can_manage": community.can_manage(request.user),
        },
    )


def _organizer_profile(user):
    profile, _ = OrganizerProfile.objects.get_or_create(
        user=user, defaults={"display_name": user.get_full_name() or user.username}
    )
    return profile


@login_required
def community_create(request):
    profile = _organizer_profile(request.user)
    if not profile.is_active:
        return HttpResponseForbidden("This organizer profile is inactive.")
    form = CommunityForm(request.POST or None, organizer=profile)
    if form.is_valid():
        community = form.save(commit=False)
        community.organizer = profile
        community.save()
        messages.success(request, f"{community.name} was created. Add a league, tournament or All-Star game next.")
        return redirect("communities:manage", slug=community.slug)
    return render(request, "communities/community_form.html", {"form": form, "heading": "Start a community"})


def _managed(request, slug):
    community = get_object_or_404(Community.objects.select_related("organizer"), slug=slug)
    if not community.can_manage(request.user):
        raise Http404
    return community


@login_required
def community_update(request, slug):
    community = _managed(request, slug)
    form = CommunityForm(request.POST or None, instance=community, organizer=community.organizer)
    if form.is_valid():
        form.save()
        messages.success(request, "Community details saved.")
        return redirect("communities:manage", slug=community.slug)
    return render(
        request,
        "communities/community_form.html",
        {"form": form, "heading": f"Edit {community.name}", "community": community},
    )


@login_required
def community_manage(request, slug):
    community = _managed(request, slug)
    leagues = community.leagues.annotate(
        team_count=Count("teams", distinct=True),
        waiting=Count("join_requests", filter=Q(join_requests__status=LeagueJoinRequest.Status.PENDING), distinct=True),
    )
    tournaments = community.championships.annotate(team_total=Count("teams", distinct=True))
    all_star_games = community.all_star_games.annotate(
        player_total=Count("registrations", filter=Q(registrations__status="active"), distinct=True)
    )
    events = community.events.filter(soccer_match__isnull=True).select_related("location")
    return render(
        request,
        "communities/community_manage.html",
        {
            "community": community,
            "leagues": leagues,
            "tournaments": tournaments,
            "all_star_games": all_star_games,
            "events": events,
            "waiting_total": sum(league.waiting for league in leagues),
            "is_organizer": community.is_organized_by(request.user),
        },
    )
