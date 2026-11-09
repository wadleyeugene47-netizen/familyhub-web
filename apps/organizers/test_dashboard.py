from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.payments.models import Payment
from apps.registrations.models import Registration

from .models import OrganizerProfile


class OrganizerDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organizer_user = User.objects.create_user(username="host-one", password="strong-password-123")
        cls.other_organizer_user = User.objects.create_user(username="host-two", password="strong-password-123")
        cls.participant = User.objects.create_user(username="attendee", password="strong-password-123")
        cls.organizer = OrganizerProfile.objects.create(user=cls.organizer_user, display_name="Host One")
        cls.other_organizer = OrganizerProfile.objects.create(
            user=cls.other_organizer_user,
            display_name="Host Two",
        )
        category = EventCategory.objects.create(name="Sports", slug="sports")
        location = EventLocation.objects.create(address_line="1 Field Lane", city="Town", country_code="US")
        starts_at = timezone.now() + timedelta(days=14)
        cls.own_event = Event.objects.create(
            organizer=cls.organizer,
            category=category,
            location=location,
            title="Host One Event",
            description="Owned by host one.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=20,
            price=Decimal("20.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        other_event = Event.objects.create(
            organizer=cls.other_organizer,
            category=category,
            location=location,
            title="Host Two Secret Event",
            description="Must not leak.",
            starts_at=starts_at + timedelta(days=1),
            ends_at=starts_at + timedelta(days=1, hours=2),
            capacity=20,
            price=Decimal("30.00"),
            currency="EUR",
            status=Event.Status.PUBLISHED,
        )
        cls.own_registration = Registration.objects.create(
            event=cls.own_event,
            participant=cls.participant,
            status=Registration.Status.CONFIRMED,
        )
        other_registration = Registration.objects.create(
            event=other_event,
            participant=cls.participant,
            status=Registration.Status.CONFIRMED,
        )
        Payment.objects.create(
            registration=cls.own_registration,
            organizer=cls.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("1.00"),
            organizer_amount=Decimal("19.00"),
            currency="USD",
            status=Payment.Status.SUCCEEDED,
        )
        Payment.objects.create(
            registration=other_registration,
            organizer=cls.other_organizer,
            amount=Decimal("30.00"),
            platform_fee=Decimal("1.50"),
            organizer_amount=Decimal("28.50"),
            currency="EUR",
            status=Payment.Status.SUCCEEDED,
        )

    def test_dashboard_contains_only_organizers_own_events_and_revenue(self):
        self.client.force_login(self.organizer_user)

        response = self.client.get(reverse("organizers:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_event.title)
        self.assertContains(response, "19")
        self.assertNotContains(response, "Host Two Secret Event")
        self.assertNotContains(response, "28.50")
        self.assertNotContains(response, "EUR")

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("organizers:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('organizers:dashboard')}",
        )

    def test_user_without_organizer_profile_is_not_promoted(self):
        self.client.force_login(self.participant)

        response = self.client.get(reverse("organizers:dashboard"))

        self.assertRedirects(response, reverse("accounts:dashboard"))
        self.assertFalse(OrganizerProfile.objects.filter(user=self.participant).exists())
