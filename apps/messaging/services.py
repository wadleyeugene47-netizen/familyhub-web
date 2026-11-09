from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.utils import timezone

from .models import Conversation, Message


class EmptyMessage(ValueError):
    pass


def send_message(*, member, sender, body):
    body = (body or "").strip()[: Message.MAX_LENGTH]
    if not body:
        raise EmptyMessage("Write a message first.")
    conversation, _ = Conversation.objects.get_or_create(member=member)
    from_host = sender.pk != member.pk
    message = Message.objects.create(conversation=conversation, sender=sender, from_host=from_host, body=body)
    read_field = "host_last_read" if from_host else "member_last_read"
    Conversation.objects.filter(pk=conversation.pk).update(
        last_message_at=message.created_at, **{read_field: message.pk}
    )
    return message


def mark_read(conversation, *, as_host):
    """Mark everything in the conversation as seen by the host or by the member."""
    if conversation is None:
        return
    last_id = conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    field = "host_last_read" if as_host else "member_last_read"
    if getattr(conversation, field) < last_id:
        Conversation.objects.filter(pk=conversation.pk).update(**{field: last_id})
        setattr(conversation, field, last_id)


def conversations_for_host():
    return Conversation.objects.select_related("member").annotate(
        unread=Count("messages", filter=Q(messages__from_host=False, messages__id__gt=F("host_last_read")))
    )


def host_unread_count():
    return Message.objects.filter(from_host=False, id__gt=F("conversation__host_last_read")).count()


def member_unread_count(member):
    return Message.objects.filter(
        conversation__member=member, from_host=True, id__gt=F("conversation__member_last_read")
    ).count()


def messages_json(conversation, *, viewer, after=0):
    """New messages since `after`, shaped for the chat page's script."""
    messages = [] if conversation is None else conversation.messages.filter(id__gt=after).select_related("sender")
    return JsonResponse(
        {
            "messages": [
                {
                    "id": message.pk,
                    "body": message.body,
                    "mine": message.sender_id == viewer.pk,
                    "from_host": message.from_host,
                    "sender": message.sender.get_full_name() or message.sender.username,
                    "time": timezone.localtime(message.created_at).strftime("%d %b %H:%M"),
                }
                for message in messages
            ]
        }
    )


def parse_after(request):
    try:
        return max(0, int(request.GET.get("after", 0)))
    except ValueError:
        return 0
