from django.contrib import admin

from .models import OrganizerPaymentAccount, OrganizerProfile


@admin.register(OrganizerProfile)
class OrganizerProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("display_name", "user__username", "user__email")


@admin.register(OrganizerPaymentAccount)
class OrganizerPaymentAccountAdmin(admin.ModelAdmin):
    list_display = (
        "organizer",
        "provider",
        "onboarding_status",
        "charges_enabled",
        "payouts_enabled",
        "updated_at",
    )
    list_filter = ("provider", "onboarding_status", "charges_enabled", "payouts_enabled")
    search_fields = ("organizer__display_name", "connected_account_id")
    readonly_fields = ("connected_account_id", "requirements_due", "updated_at")
