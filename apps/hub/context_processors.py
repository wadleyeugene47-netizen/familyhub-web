from django.utils.functional import SimpleLazyObject

from .features import enabled_keys
from .models import SiteSettings


def _host_counts():
    from apps.accounts.models import User
    from apps.events.models import Event
    from apps.leagues.models import LeagueJoinRequest
    from apps.messaging.services import host_unread_count

    counts = {
        "members": User.objects.filter(approval_status=User.Approval.PENDING, is_superuser=False).count(),
        "events": Event.objects.filter(status=Event.Status.PENDING).count(),
        "messages": host_unread_count(),
        "players": LeagueJoinRequest.objects.filter(status=LeagueJoinRequest.Status.PENDING).count(),
    }
    counts["total"] = sum(counts.values())
    return counts


def _member_unread(user):
    from apps.messaging.services import member_unread_count

    return member_unread_count(user)


def _community_leagues():
    from apps.leagues.models import League

    return list(League.objects.filter(status=League.Status.ACTIVE).only("pk", "name"))


def _is_captain(user):
    from apps.leagues.models import League, TeamMembership

    return TeamMembership.objects.filter(
        user=user,
        role=TeamMembership.Role.CAPTAIN,
        status=TeamMembership.Status.ACTIVE,
        team__league__status__in=[League.Status.ACTIVE, League.Status.COMPLETED],
    ).exists()


def hub(request):
    context = {
        "site": SimpleLazyObject(SiteSettings.load),
        "features_on": SimpleLazyObject(enabled_keys),
        "community_leagues": SimpleLazyObject(_community_leagues),
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        context["is_captain"] = SimpleLazyObject(lambda: _is_captain(user))
        if user.is_superuser:
            context["host_counts"] = SimpleLazyObject(_host_counts)
        else:
            context["chat_unread"] = SimpleLazyObject(lambda: _member_unread(user))
    return context
