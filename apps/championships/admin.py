from django.contrib import admin

from .models import Championship, Group, Match, MatchIncident, ResultAudit, Team


class GroupInline(admin.TabularInline):
    model = Group
    extra = 0


class TeamInline(admin.TabularInline):
    model = Team
    extra = 0
    fields = ("name", "short_name", "group", "seed", "lots_rank")


@admin.register(Championship)
class ChampionshipAdmin(admin.ModelAdmin):
    list_display = ("name", "organizer", "format", "status", "team_count", "first_kickoff_at")
    list_filter = ("status", "format")
    search_fields = ("name", "organizer__display_name")
    inlines = (GroupInline, TeamInline)


class MatchIncidentInline(admin.TabularInline):
    model = MatchIncident
    extra = 0


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Read-only here: results must go through the organizer dashboard so standings, bracket and audit stay in sync."""

    list_display = ("championship", "stage", "knockout_round", "home_team", "away_team", "status", "home_score", "away_score")
    list_filter = ("championship", "stage", "status")
    search_fields = ("home_team__name", "away_team__name", "championship__name")
    inlines = (MatchIncidentInline,)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(ResultAudit)
class ResultAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "championship", "action", "summary", "user")
    list_filter = ("action", "championship")
    search_fields = ("summary", "note", "user__username")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
