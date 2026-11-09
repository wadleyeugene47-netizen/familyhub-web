from django.contrib import admin

from .models import League, Match, Team, TeamMembership


class TeamInline(admin.TabularInline):
    model = Team
    extra = 0


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "organizer", "status", "starts_on", "ends_on")
    list_filter = ("status", "format")
    search_fields = ("name", "organizer__display_name")
    inlines = (TeamInline,)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "league", "short_name")
    list_filter = ("league",)
    search_fields = ("name", "league__name")


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "role", "status", "jersey_number")
    list_filter = ("role", "status", "team__league")
    search_fields = ("user__username", "team__name", "team__league__name")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "league",
        "home_team",
        "away_team",
        "event",
        "status",
        "home_score",
        "away_score",
    )
    list_filter = ("league", "status")
    search_fields = ("event__title", "home_team__name", "away_team__name")
