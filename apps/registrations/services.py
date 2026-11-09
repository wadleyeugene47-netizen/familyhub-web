from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.events.models import Event
from apps.notifications.services import queue_registration_email

from .models import Registration


class RegistrationError(Exception):
    """Base exception for registration rule violations."""


class EventUnavailable(RegistrationError):
    pass


class EventFull(RegistrationError):
    pass


class AlreadyRegistered(RegistrationError):
    pass


@transaction.atomic
def register_participant(*, event_id, participant):
    try:
        event = Event.objects.select_for_update().get(pk=event_id)
    except Event.DoesNotExist as error:
        raise EventUnavailable("This event is not available.") from error

    if event.status != Event.Status.PUBLISHED or event.starts_at <= timezone.now():
        raise EventUnavailable("This event is not open for registration.")

    active_statuses = [
        Registration.Status.PENDING_PAYMENT,
        Registration.Status.CONFIRMED,
    ]
    active_registrations = Registration.objects.filter(
        event=event,
        status__in=active_statuses,
    )
    if active_registrations.filter(participant=participant).exists():
        raise AlreadyRegistered("You already have an active registration for this event.")

    occupied = active_registrations.count()
    if occupied >= event.capacity:
        raise EventFull("This event has reached capacity.")

    status = (
        Registration.Status.CONFIRMED
        if event.is_free
        else Registration.Status.PENDING_PAYMENT
    )
    try:
        with transaction.atomic():
            registration = Registration.objects.create(
                event=event,
                participant=participant,
                status=status,
            )
            queue_registration_email(registration)
            return registration
    except IntegrityError as error:
        raise AlreadyRegistered("You already have an active registration for this event.") from error
