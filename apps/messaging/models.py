from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """The private chat between one member and the site host. Any owner account can answer it."""

    member = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="host_conversation",
    )
    # The id of the last message each side has seen; anything newer from the other side is unread.
    host_last_read = models.PositiveBigIntegerField(default=0)
    member_last_read = models.PositiveBigIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = [models.F("last_message_at").desc(nulls_last=True)]

    def __str__(self):
        return f"Chat with {self.member}"


class Message(models.Model):
    MAX_LENGTH = 2000

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    from_host = models.BooleanField()
    body = models.TextField(max_length=MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "from_host", "id"])]

    def __str__(self):
        return f"{self.sender}: {self.body[:40]}"
