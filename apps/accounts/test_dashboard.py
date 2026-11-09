from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile
from apps.payments.models import Payment
from apps.registrations.models import Registration


class ParticipantDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.participant = User.objects.create_user(username="participant", password="strong-password-123")
        cls.other_participant = User.objects.create_user(username="other", password="strong-password-123")
        organizer_user = User.objects.create_user(username="organizer", password="strong-password-123")
        organizer = OrganizerProfile.objects.create(user=organizer_user, display_name="Organizer")
        category = EventCategory.objects.create(name="Meetup", slug="meetup")
        location = EventLocation.objects.create(address_line="1 Main Road", city="Town", country_code="US")
        starts_at = timezone.now() + timedelta(days=14)
        cls.own_event = Event.objects.create(
            organizer=organizer,
            category=category,
            location=location,
            title="Own registration event",
            description="Participant-specific.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=20,
            price=Decimal("12.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        cls.other_event = Event.objects.create(
            organizer=organizer,
            category=category,
            location=location,
            title="Other participant event",
            description="Should not appear.",
            starts_at=starts_at + timedelta(days=1),
            ends_at=starts_at + timedelta(days=1, hours=2),
            capacity=20,
            price=Decimal("0.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        cls.own_registration = Registration.objects.create(
            event=cls.own_event,
            participant=cls.participant,
            status=Registration.Status.PENDING_PAYMENT,
        )
        Registration.objects.create(
            event=cls.other_event,
            participant=cls.other_participant,
            status=Registration.Status.CONFIRMED,
        )
        Payment.objects.create(
            registration=cls.own_registration,
            organizer=organizer,
            amount=Decimal("12.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("12.00"),
            currency="USD",
            status=Payment.Status.PENDING,
        )

    def test_dashboard_shows_only_signed_in_participants_data(self):
        self.client.force_login(self.participant)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_event.title)
        self.assertNotContains(response, self.other_event.title)
        self.assertContains(response, "Continue to payment")

    def test_dashboard_login_required(self):
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:dashboard')}",
        )
