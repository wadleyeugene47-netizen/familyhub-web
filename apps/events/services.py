from django.urls import reverse
from django.utils import timezone

from apps.hub.decorators import is_owner
from apps.notifications.services import queue_event_submitted_emails, queue_event_update_emails
from apps.registrations.models import Registration

from .models import Event


def needs_owner_approval(user):
    """Everyone except the owner sends events to the owner before they go live."""
    return not is_owner(user)


def submit_for_approval(request, event):
    """Hold an event the organizer asked to publish until the owner approves it."""
    event.status = Event.Status.PENDING
    event.review_note = ""
    event.save(update_fields=("status", "review_note", "updated_at"))
    queue_event_submitted_emails(
        event, review_url=request.build_absolute_uri(reverse("hub:event_review"))
    )


def cancel_event(event):
    """Cancel an event the caller has locked with select_for_update() inside a transaction."""
    event.status = Event.Status.CANCELLED
    event.save(update_fields=("status", "review_note", "updated_at"))
    queue_event_update_emails(event, cancelled=True)
    Registration.objects.filter(
        event=event,
        status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING_PAYMENT],
    ).update(status=Registration.Status.CANCELLED, updated_at=timezone.now())
