from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerPaymentAccount
from apps.notifications.models import EmailNotification
from apps.registrations.models import Registration
from apps.organizers.models import OrganizerProfile


class EventWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer_user = get_user_model().objects.create_user(
            username="event-organizer",
            password="strong-password-123",
        )
        cls.organizer = OrganizerProfile.objects.create(
            user=cls.organizer_user,
            display_name="Local Organizer",
        )
        cls.category = EventCategory.objects.create(name="Picnic", slug="picnic")
        cls.location = EventLocation.objects.create(
            address_line="10 Park Lane",
            city="Sampletown",
            country_code="GB",
        )
        starts_at = timezone.now() + timedelta(days=14)
        cls.published_event = Event.objects.create(
            organizer=cls.organizer,
            category=cls.category,
            location=cls.location,
            title="Open Community Picnic",
            description="Bring a blanket.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=3),
            capacity=30,
            price=Decimal("0.00"),
            currency="GBP",
            status=Event.Status.PUBLISHED,
        )
        cls.draft_event = Event.objects.create(
            organizer=cls.organizer,
            category=cls.category,
            location=cls.location,
            title="Private Draft Picnic",
            description="Draft only.",
            starts_at=starts_at + timedelta(days=1),
            ends_at=starts_at + timedelta(days=1, hours=3),
            capacity=30,
            price=Decimal("0.00"),
            currency="GBP",
        )

    def test_discovery_lists_only_published_upcoming_events(self):
        response = self.client.get(reverse("events:list"))

        self.assertContains(response, self.published_event.title)
        self.assertNotContains(response, self.draft_event.title)

    def test_anonymous_visitor_cannot_view_draft(self):
        response = self.client.get(
            reverse("events:detail", args=[self.draft_event.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_can_view_draft_but_other_user_cannot(self):
        self.client.force_login(self.organizer_user)
        owner_response = self.client.get(
            reverse("events:detail", args=[self.draft_event.pk])
        )
        self.assertEqual(owner_response.status_code, 200)

        other_user = get_user_model().objects.create_user(
            username="another-user",
            password="another-strong-password",
        )
        self.client.force_login(other_user)
        other_response = self.client.get(
            reverse("events:detail", args=[self.draft_event.pk])
        )
        self.assertEqual(other_response.status_code, 404)

    def test_non_owner_cannot_edit_event(self):
        other_user = get_user_model().objects.create_user(
            username="non-owner",
            password="another-strong-password",
        )
        self.client.force_login(other_user)

        response = self.client.get(
            reverse("events:update", args=[self.published_event.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_organizer_can_create_draft_event(self):
        self.client.force_login(self.organizer_user)
        starts_at = timezone.localtime(timezone.now() + timedelta(days=30))
        ends_at = starts_at + timedelta(hours=2)
        response = self.client.post(
            reverse("events:create"),
            {
                "title": "Evening Picnic",
                "description": "A small local gathering.",
                "category": self.category.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": ends_at.strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
                "capacity": 18,
                "price": "0.00",
                "currency": "GBP",
                "status": Event.Status.DRAFT,
                "venue_name": "Central Park",
                "address_line": "10 Park Lane",
                "city": "Sampletown",
                "region": "",
                "postal_code": "",
                "country_code": "gb",
                "latitude": "51.507351",
                "longitude": "-0.127758",
            },
        )

        self.assertRedirects(response, reverse("events:organizer_list"))
        created_event = Event.objects.get(title="Evening Picnic")
        self.assertEqual(created_event.status, Event.Status.DRAFT)
        self.assertEqual(created_event.location.country_code, "GB")
        self.assertEqual(str(created_event.location.latitude), "51.507351")

    def test_event_detail_shows_map_for_geocoded_location(self):
        self.location.latitude = "51.507351"
        self.location.longitude = "-0.127758"
        self.location.save()

        response = self.client.get(
            reverse("events:detail", args=[self.published_event.pk])
        )

        self.assertContains(response, "<iframe", html=False)
        self.assertContains(response, "OpenStreetMap contributors")
        self.assertContains(response, "marker=51.507351%2C-0.127758")

    def test_event_creation_requires_both_coordinates(self):
        self.client.force_login(self.organizer_user)
        starts_at = timezone.localtime(timezone.now() + timedelta(days=30))
        response = self.client.post(
            reverse("events:create"),
            {
                "title": "Incomplete Location Event",
                "description": "A location test.",
                "category": self.category.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (starts_at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
                "capacity": 10,
                "price": "0.00",
                "currency": "GBP",
                "status": Event.Status.DRAFT,
                "venue_name": "Central Park",
                "address_line": "10 Park Lane",
                "city": "Sampletown",
                "region": "",
                "postal_code": "",
                "country_code": "gb",
                "latitude": "51.507351",
                "longitude": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter both coordinates, or leave both blank.")
        self.assertFalse(Event.objects.filter(title="Incomplete Location Event").exists())

    def test_paid_event_cannot_be_published_before_charges_are_enabled(self):
        self.client.force_login(self.organizer_user)
        starts_at = timezone.localtime(timezone.now() + timedelta(days=30))
        response = self.client.post(
            reverse("events:create"),
            {
                "title": "Paid Match",
                "description": "A paid event.",
                "category": self.category.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (starts_at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
                "capacity": 18,
                "price": "10.00",
                "currency": "GBP",
                "status": Event.Status.PUBLISHED,
                "venue_name": "Central Park",
                "address_line": "10 Park Lane",
                "city": "Sampletown",
                "region": "",
                "postal_code": "",
                "country_code": "gb",
                "latitude": "",
                "longitude": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete Stripe onboarding and enable charges")
        self.assertFalse(Event.objects.filter(title="Paid Match").exists())

    def test_paid_event_can_be_published_when_stripe_charges_enabled(self):
        OrganizerPaymentAccount.objects.create(
            organizer=self.organizer,
            connected_account_id="acct_test_enabled",
            onboarding_status=OrganizerPaymentAccount.OnboardingStatus.ENABLED,
            charges_enabled=True,
            details_submitted=True,
        )
        self.client.force_login(self.organizer_user)
        starts_at = timezone.localtime(timezone.now() + timedelta(days=30))
        response = self.client.post(
            reverse("events:create"),
            {
                "title": "Eligible Paid Match",
                "description": "A paid event.",
                "category": self.category.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (starts_at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
                "capacity": 18,
                "price": "10.00",
                "currency": "GBP",
                "status": Event.Status.PUBLISHED,
                "venue_name": "Central Park",
                "address_line": "10 Park Lane",
                "city": "Sampletown",
                "region": "",
                "postal_code": "",
                "country_code": "gb",
                "latitude": "",
                "longitude": "",
            },
        )

        self.assertRedirects(response, reverse("events:organizer_list"))
        # Members' events wait for the owner before going live.
        self.assertEqual(
            Event.objects.get(title="Eligible Paid Match").status,
            Event.Status.PENDING,
        )

    def test_owner_cancels_event_and_attendees_are_notified(self):
        attendee = get_user_model().objects.create_user(
            username="event-attendee",
            email="attendee@example.com",
            password="attendee-password-123",
        )
        registration = Registration.objects.create(
            event=self.published_event,
            participant=attendee,
            status=Registration.Status.CONFIRMED,
        )
        self.client.force_login(self.organizer_user)

        response = self.client.post(reverse("events:cancel", args=[self.published_event.pk]))

        self.assertRedirects(response, reverse("events:organizer_list"))
        self.published_event.refresh_from_db()
        registration.refresh_from_db()
        self.assertEqual(self.published_event.status, Event.Status.CANCELLED)
        self.assertEqual(registration.status, Registration.Status.CANCELLED)
        self.assertTrue(
            EmailNotification.objects.filter(
                registration=registration,
                kind=EmailNotification.Kind.EVENT_CANCELLED,
            ).exists()
        )

    def test_published_event_time_change_queues_attendee_update(self):
        attendee = get_user_model().objects.create_user(
            username="event-change-attendee",
            email="change-attendee@example.com",
            password="attendee-password-123",
        )
        registration = Registration.objects.create(
            event=self.published_event,
            participant=attendee,
            status=Registration.Status.CONFIRMED,
        )
        self.client.force_login(self.organizer_user)
        new_start = timezone.localtime(self.published_event.starts_at + timedelta(hours=1))
        new_end = timezone.localtime(self.published_event.ends_at + timedelta(hours=1))
        response = self.client.post(
            reverse("events:update", args=[self.published_event.pk]),
            {
                "title": self.published_event.title,
                "description": self.published_event.description,
                "category": self.category.pk,
                "starts_at": new_start.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": new_end.strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
                "capacity": self.published_event.capacity,
                "price": "0.00",
                "currency": "GBP",
                "status": Event.Status.PUBLISHED,
                "venue_name": self.location.name,
                "address_line": self.location.address_line,
                "city": self.location.city,
                "region": self.location.region,
                "postal_code": self.location.postal_code,
                "country_code": self.location.country_code,
                "latitude": "",
                "longitude": "",
            },
        )

        self.assertRedirects(response, reverse("events:organizer_list"))
        self.assertTrue(
            EmailNotification.objects.filter(
                registration=registration,
                kind=EmailNotification.Kind.EVENT_UPDATED,
            ).exists()
        )
