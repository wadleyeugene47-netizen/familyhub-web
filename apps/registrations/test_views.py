from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile

from .models import Registration


class RegistrationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organizer_user = get_user_model().objects.create_user(
            username="event-host",
            password="host-password-123",
        )
        organizer = OrganizerProfile.objects.create(
            user=organizer_user,
            display_name="Event Host",
        )
        category = EventCategory.objects.create(name="Workshop", slug="workshop")
        location = EventLocation.objects.create(
            address_line="20 Learning Way",
            city="Sampletown",
            country_code="US",
        )
        starts_at = timezone.now() + timedelta(days=12)
        cls.event = Event.objects.create(
            organizer=organizer,
            category=category,
            location=location,
            title="Free Workshop",
            description="Learn something useful.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=5,
            price=Decimal("0.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )

    def test_anonymous_registration_redirects_to_login(self):
        response = self.client.post(reverse("events:register", args=[self.event.pk]))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('events:register', args=[self.event.pk])}",
        )
        self.assertEqual(Registration.objects.count(), 0)

    def test_free_registration_redirects_and_is_visible_on_detail(self):
        participant = get_user_model().objects.create_user(
            username="attendee",
            password="attendee-password-123",
        )
        self.client.force_login(participant)

        response = self.client.post(
            reverse("events:register", args=[self.event.pk]),
            follow=True,
        )

        self.assertRedirects(response, reverse("events:detail", args=[self.event.pk]))
        self.assertContains(response, "Your registration: Confirmed")
        self.assertContains(response, "Your registration is confirmed.")

    def test_get_request_does_not_create_registration(self):
        participant = get_user_model().objects.create_user(
            username="attendee-get",
            password="attendee-password-123",
        )
        self.client.force_login(participant)

        self.client.get(reverse("events:register", args=[self.event.pk]))

        self.assertEqual(Registration.objects.count(), 0)
