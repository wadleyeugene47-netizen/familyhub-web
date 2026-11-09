from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("sender", "from_host", "body", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("member", "last_message_at", "created_at")
    search_fields = ("member__username", "member__email")
    readonly_fields = ("member", "host_last_read", "member_last_read", "last_message_at")
    inlines = [MessageInline]
