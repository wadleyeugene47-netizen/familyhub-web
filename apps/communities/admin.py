from django.contrib import admin

from .models import Community


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "organizer", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
