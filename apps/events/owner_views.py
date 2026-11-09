from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.hub.decorators import owner_required
from apps.notifications.services import queue_event_decision_email

from .models import Event
from .services import cancel_event


def _events():
    return Event.objects.select_related("organizer__user", "category", "location")


@owner_required
def event_review(request):
    return render(
        request,
        "hub/event_review.html",
        {
            "pending_events": _events().filter(status=Event.Status.PENDING).order_by("updated_at"),
            "live_events": _events().filter(
                status__in=[Event.Status.PUBLISHED, Event.Status.DRAFT],
                ends_at__gte=timezone.now(),
            ),
        },
    )


@owner_required
def event_decide(request, event_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    decision = request.POST.get("decision")
    note = request.POST.get("note", "").strip()
    with transaction.atomic():
        if decision in ("approve", "reject"):
            event = get_object_or_404(
                _events().select_for_update(of=("self",)), pk=event_id, status=Event.Status.PENDING
            )
            event.status = Event.Status.PUBLISHED if decision == "approve" else Event.Status.REJECTED
            event.review_note = note
            event.save(update_fields=("status", "review_note", "updated_at"))
        elif decision == "cancel":
            event = get_object_or_404(
                _events().select_for_update(of=("self",)),
                pk=event_id,
                status__in=[Event.Status.DRAFT, Event.Status.PENDING, Event.Status.PUBLISHED],
            )
            event.review_note = note
            cancel_event(event)
        else:
            messages.error(request, "Choose approve, decline or cancel.")
            return redirect("hub:event_review")
        if event.organizer.user_id != request.user.pk:
            queue_event_decision_email(event, cancelled=decision == "cancel")
    labels = {"approve": "is now published", "reject": "was declined", "cancel": "was cancelled"}
    messages.success(request, f"{event.title} {labels[decision]}.")
    return redirect("hub:event_review")
