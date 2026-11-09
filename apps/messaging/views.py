from django.contrib import messages as flash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.hub.decorators import owner_required

from .models import Conversation
from .services import (
    EmptyMessage,
    conversations_for_host,
    mark_read,
    messages_json,
    parse_after,
    send_message,
)


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _send(request, *, member, redirect_to):
    try:
        send_message(member=member, sender=request.user, body=request.POST.get("body"))
    except EmptyMessage as error:
        if _wants_json(request):
            return JsonResponse({"error": str(error)}, status=400)
        flash.error(request, str(error))
    if _wants_json(request):
        return JsonResponse({"ok": True})
    return redirect_to


# Member side ----------------------------------------------------------------


@login_required
def member_chat(request):
    if request.user.is_superuser:
        return redirect("hub:messages")
    if request.method == "POST":
        return _send(request, member=request.user, redirect_to=redirect("messaging:chat"))
    conversation = Conversation.objects.filter(member=request.user).first()
    mark_read(conversation, as_host=False)
    return render(
        request,
        "messaging/chat.html",
        {"chat_messages": conversation.messages.select_related("sender") if conversation else []},
    )


@login_required
def member_poll(request):
    if request.user.is_superuser:
        return JsonResponse({"messages": []})
    conversation = Conversation.objects.filter(member=request.user).first()
    response = messages_json(conversation, viewer=request.user, after=parse_after(request))
    mark_read(conversation, as_host=False)
    return response


# Host side ------------------------------------------------------------------


def _member(user_id):
    return get_object_or_404(get_user_model(), pk=user_id, is_superuser=False)


@owner_required
def inbox(request):
    return render(request, "hub/messages.html", {"conversations": conversations_for_host()})


@owner_required
def thread(request, user_id):
    member = _member(user_id)
    if request.method == "POST":
        return _send(request, member=member, redirect_to=redirect("hub:message_thread", user_id=member.pk))
    conversation = Conversation.objects.filter(member=member).first()
    mark_read(conversation, as_host=True)
    return render(
        request,
        "hub/messages.html",
        {
            "conversations": conversations_for_host(),
            "member": member,
            "chat_messages": conversation.messages.select_related("sender") if conversation else [],
        },
    )


@owner_required
def thread_poll(request, user_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    conversation = Conversation.objects.filter(member=_member(user_id)).first()
    response = messages_json(conversation, viewer=request.user, after=parse_after(request))
    mark_read(conversation, as_host=True)
    return response
