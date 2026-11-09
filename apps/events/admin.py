from django.contrib import admin

from .models import (
    Event,
    EventCancellationPolicy,
    EventCategory,
    EventImage,
    EventLocation,
)


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(EventLocation)
class EventLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "country_code")
    search_fields = ("name", "city", "address_line")


class EventImageInline(admin.TabularInline):
    model = EventImage
    extra = 0


class EventCancellationPolicyInline(admin.StackedInline):
    model = EventCancellationPolicy
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "category", "starts_at", "status")
    list_filter = ("status", "category", "starts_at")
    search_fields = ("title", "description", "organizer__display_name")
    inlines = (EventImageInline, EventCancellationPolicyInline)
