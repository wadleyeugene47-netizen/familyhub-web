from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.events.models import (
    Event,
    EventCancellationPolicy,
    EventCategory,
    EventImage,
    EventLocation,
)
from apps.organizers.models import OrganizerProfile


class EventModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="organizer",
            password="test-password",
        )
        cls.organizer = OrganizerProfile.objects.create(
            user=user,
            display_name="Community Sports Group",
        )
        cls.category = EventCategory.objects.create(
            name="Soccer",
            slug="soccer",
        )
        cls.location = EventLocation.objects.create(
            address_line="1 Field Road",
            city="Example City",
            country_code="US",
        )

    def make_event(self, **overrides):
        starts_at = timezone.now() + timedelta(days=7)
        fields = {
            "organizer": self.organizer,
            "category": self.category,
            "location": self.location,
            "title": "Community Match",
            "description": "A friendly match.",
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(hours=2),
            "capacity": 22,
            "price": Decimal("0.00"),
            "currency": "USD",
        }
        fields.update(overrides)
        return Event.objects.create(**fields)

    def test_free_event_has_explicit_currency_and_uuid(self):
        event = self.make_event()

        self.assertTrue(event.is_free)
        self.assertEqual(event.currency, "USD")
        self.assertIsNotNone(event.pk)

    def test_event_end_must_be_after_start(self):
        starts_at = timezone.now() + timedelta(days=7)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_event(ends_at=starts_at)

    def test_event_capacity_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_event(capacity=0)

    def test_event_price_cannot_be_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_event(price=Decimal("-1.00"))

    def test_organizer_profile_is_unique_per_user(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizerProfile.objects.create(
                user=self.organizer.user,
                display_name="Duplicate Profile",
            )

    def test_location_rejects_out_of_range_latitude(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventLocation.objects.create(
                address_line="2 Field Road",
                city="Example City",
                country_code="US",
                latitude=Decimal("91.000000"),
            )

    def test_event_has_configurable_cancellation_policy(self):
        event = self.make_event()
        policy = EventCancellationPolicy.objects.create(
            event=event,
            refund_deadline_hours=48,
            policy_text="Cancellations are accepted up to 48 hours before start.",
        )

        self.assertEqual(event.cancellation_policy, policy)
        self.assertTrue(policy.cancellations_allowed)

    def test_event_image_accepts_uploaded_image(self):
        event = self.make_event()
        image = SimpleUploadedFile(
            "event.png",
            (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
                b"\x00\x00\x00\x0bIDAT\x08\xd7c``\x00\x02\x00\x00\x05"
                b"\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            content_type="image/png",
        )
        event_image = EventImage.objects.create(
            event=event,
            image=image,
            alt_text="Players on the field",
        )

        self.assertEqual(event.images.get(), event_image)
