from django.contrib import admin

from .models import AllStarGame, AllStarRegistration, AllStarTeam


@admin.register(AllStarGame)
class AllStarGameAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "status", "starts_at", "team_count")
    list_filter = ("status",)
    search_fields = ("title",)


@admin.register(AllStarRegistration)
class AllStarRegistrationAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "position", "skill_rating", "team", "status")
    list_filter = ("status",)
    raw_id_fields = ("user",)


admin.site.register(AllStarTeam)
