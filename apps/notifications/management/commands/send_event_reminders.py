from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.events.models import Event
from apps.notifications.models import EmailNotification
from apps.notifications.services import queue_event_reminder
from apps.registrations.models import Registration


class Command(BaseCommand):
    help = "Queue reminders for confirmed registrations about 24 hours before events."

    def add_arguments(self, parser):
        parser.add_argument("--window-hours", type=int, default=1)

    def handle(self, *args, **options):
        window = max(1, options["window_hours"])
        now = timezone.now()
        events = Event.objects.filter(
            status=Event.Status.PUBLISHED,
            starts_at__gt=now + timedelta(hours=24 - window),
            starts_at__lte=now + timedelta(hours=24 + window),
        )
        registrations = Registration.objects.filter(
            event__in=events,
            status=Registration.Status.CONFIRMED,
        ).select_related("event", "event__location", "participant")
        queued = 0
        for registration in registrations:
            dedupe_key = f"event-reminder-24h:{registration.pk}"
            if not EmailNotification.objects.filter(dedupe_key=dedupe_key).exists():
                queue_event_reminder(registration)
                queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {registrations.count()} registrations; queued {queued} reminders."
            )
        )
