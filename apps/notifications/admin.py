from django.contrib import admin

from .models import EmailNotification


@admin.register(EmailNotification)
class EmailNotificationAdmin(admin.ModelAdmin):
    list_display = ("kind", "recipient", "status", "attempts", "created_at", "sent_at")
    list_filter = ("kind", "status", "created_at")
    search_fields = ("recipient__username", "recipient__email", "dedupe_key", "subject")
    readonly_fields = (
        "recipient",
        "registration",
        "payment",
        "kind",
        "dedupe_key",
        "subject",
        "body",
        "status",
        "attempts",
        "last_error",
        "created_at",
        "sent_at",
        "updated_at",
    )
