from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile
from apps.registrations.models import Registration

from .models import EmailNotification
from .services import deliver_email, queue_registration_email


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.participant = User.objects.create_user(
            username="notice-recipient",
            email="attendee@example.com",
            password="strong-password-123",
        )
        organizer_user = User.objects.create_user(
            username="notice-organizer",
            password="strong-password-123",
        )
        organizer = OrganizerProfile.objects.create(
            user=organizer_user,
            display_name="Notice Organizer",
        )
        category = EventCategory.objects.create(name="Notification Event", slug="notification-event")
        location = EventLocation.objects.create(
            address_line="1 Event Road",
            city="Sample City",
            country_code="US",
        )
        starts_at = timezone.now() + timedelta(days=4)
        event = Event.objects.create(
            organizer=organizer,
            category=category,
            location=location,
            title="Notification Test Event",
            description="Testing email delivery.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=20,
            price=Decimal("0.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        cls.registration = Registration.objects.create(
            event=event,
            participant=cls.participant,
            status=Registration.Status.CONFIRMED,
        )

    def test_registration_email_is_sent_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            notification = queue_registration_email(self.registration)

        notification.refresh_from_db()
        self.assertEqual(notification.status, EmailNotification.Status.SENT)
        self.assertEqual(notification.attempts, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["attendee@example.com"])
        self.assertIn(self.registration.event.title, mail.outbox[0].subject)

    def test_duplicate_queue_request_does_not_send_duplicate_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = queue_registration_email(self.registration)
            second = queue_registration_email(self.registration)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EmailNotification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_email_is_recorded_and_can_be_retried(self):
        self.participant.email = ""
        self.participant.save(update_fields=["email"])
        notification = queue_registration_email(self.registration)

        delivered = deliver_email(notification.pk)

        self.assertFalse(delivered)
        notification.refresh_from_db()
        self.assertEqual(notification.status, EmailNotification.Status.FAILED)
        self.assertEqual(notification.attempts, 1)
        self.assertTrue(notification.last_error)

        self.participant.email = "updated@example.com"
        self.participant.save(update_fields=["email"])
        self.assertTrue(deliver_email(notification.pk))
        notification.refresh_from_db()
        self.assertEqual(notification.status, EmailNotification.Status.SENT)
        self.assertEqual(notification.attempts, 2)
        self.assertEqual(len(mail.outbox), 1)
