from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile

from .models import Registration
from .services import AlreadyRegistered, EventFull, register_participant


class RegistrationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="organizer",
            password="strong-password-123",
        )
        cls.organizer = OrganizerProfile.objects.create(
            user=user,
            display_name="Registration Test Organizer",
        )
        cls.category = EventCategory.objects.create(name="Meetup", slug="meetup")
        cls.location = EventLocation.objects.create(
            address_line="1 Main Street",
            city="Sampletown",
            country_code="US",
        )

    def make_event(self, *, capacity=2, price=Decimal("0.00")):
        starts_at = timezone.now() + timedelta(days=10)
        return Event.objects.create(
            organizer=self.organizer,
            category=self.category,
            location=self.location,
            title="Test Meetup",
            description="A test event.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=capacity,
            price=price,
            currency="USD",
            status=Event.Status.PUBLISHED,
        )

    def make_participant(self, username):
        return get_user_model().objects.create_user(
            username=username,
            password="participant-password-123",
        )

    def test_free_registration_is_confirmed(self):
        event = self.make_event()
        participant = self.make_participant("free-participant")

        registration = register_participant(event_id=event.pk, participant=participant)

        self.assertEqual(registration.status, Registration.Status.CONFIRMED)

    def test_paid_registration_stays_pending_payment(self):
        event = self.make_event(price=Decimal("15.00"))
        participant = self.make_participant("paid-participant")

        registration = register_participant(event_id=event.pk, participant=participant)

        self.assertEqual(registration.status, Registration.Status.PENDING_PAYMENT)

    def test_duplicate_active_registration_is_rejected(self):
        event = self.make_event()
        participant = self.make_participant("duplicate-participant")
        register_participant(event_id=event.pk, participant=participant)

        with self.assertRaises(AlreadyRegistered):
            register_participant(event_id=event.pk, participant=participant)

    def test_capacity_includes_pending_payment_reservations(self):
        event = self.make_event(capacity=1, price=Decimal("15.00"))
        first_participant = self.make_participant("first-participant")
        second_participant = self.make_participant("second-participant")
        register_participant(event_id=event.pk, participant=first_participant)

        with self.assertRaises(EventFull):
            register_participant(event_id=event.pk, participant=second_participant)

    def test_database_constraint_prevents_second_active_registration(self):
        event = self.make_event()
        participant = self.make_participant("unique-participant")
        Registration.objects.create(
            event=event,
            participant=participant,
            status=Registration.Status.CONFIRMED,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(
                event=event,
                participant=participant,
                status=Registration.Status.PENDING_PAYMENT,
            )
