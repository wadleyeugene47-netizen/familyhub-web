import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.hub.models import Feature
from apps.organizers.models import OrganizerPaymentAccount, OrganizerProfile
from apps.registrations.models import Registration

from .models import Payment


class PaymentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Feature.objects.filter(key="payments").update(enabled=True)
        User = get_user_model()
        cls.organizer_user = User.objects.create_user(username="host", password="strong-password-123")
        cls.organizer = OrganizerProfile.objects.create(user=cls.organizer_user, display_name="Host")
        OrganizerPaymentAccount.objects.create(
            organizer=cls.organizer,
            connected_account_id="acct_test_views",
            onboarding_status=OrganizerPaymentAccount.OnboardingStatus.ENABLED,
            charges_enabled=True,
            details_submitted=True,
        )
        cls.participant = User.objects.create_user(username="buyer", password="strong-password-123")
        cls.other_participant = User.objects.create_user(username="other-buyer", password="strong-password-123")
        category = EventCategory.objects.create(name="Concert", slug="concert")
        location = EventLocation.objects.create(address_line="1 Hall Road", city="Test City", country_code="US")
        starts_at = timezone.now() + timedelta(days=14)
        event = Event.objects.create(
            organizer=cls.organizer,
            category=category,
            location=location,
            title="Test Concert",
            description="Ticketed event.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=20,
            price=Decimal("20.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        cls.registration = Registration.objects.create(
            event=event,
            participant=cls.participant,
            status=Registration.Status.PENDING_PAYMENT,
        )
        cls.other_registration = Registration.objects.create(
            event=event,
            participant=cls.other_participant,
            status=Registration.Status.PENDING_PAYMENT,
        )

    def test_participant_cannot_checkout_another_users_registration(self):
        self.client.force_login(self.participant)

        response = self.client.post(
            reverse("payments:checkout", args=[self.other_registration.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Payment.objects.filter(registration=self.other_registration).exists())

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    @patch("apps.payments.views.create_checkout_session")
    def test_owner_checkout_redirects_to_stripe(self, create_session):
        create_session.return_value = type(
            "Attempt", (), {"checkout_url": "https://checkout.stripe.test/session"}
        )()
        self.client.force_login(self.participant)

        response = self.client.post(reverse("payments:checkout", args=[self.registration.pk]))

        self.assertRedirects(
            response,
            "https://checkout.stripe.test/session",
            fetch_redirect_response=False,
        )
        kwargs = create_session.call_args.kwargs
        self.assertEqual(kwargs["registration"].pk, self.registration.pk)
        self.assertIn("{CHECKOUT_SESSION_ID}", kwargs["success_url"])

    def test_webhook_rejects_bad_signature(self):
        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"not-json",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad-signature",
        )

        self.assertEqual(response.status_code, 503)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test_secret")
    def test_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid",
        )

        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test_secret")
    @patch("apps.payments.views.process_stripe_event")
    def test_webhook_accepts_valid_stripe_signature(self, process_event):
        payload = json.dumps(
            {
                "id": "evt_signed_test",
                "type": "ignored.test.event",
                "data": {"object": {}},
            }
        )
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{payload}".encode()
        signature_hash = hmac.new(
            b"whsec_test_secret",
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        signature = f"t={timestamp},v1={signature_hash}"

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        process_event.assert_called_once()

    def create_succeeded_payment(self):
        return Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("20.00"),
            currency="USD",
            status=Payment.Status.SUCCEEDED,
            stripe_payment_intent_id="pi_refund_auth_test",
        )

    def test_unrelated_user_cannot_refund_payment(self):
        payment = self.create_succeeded_payment()
        self.client.force_login(self.other_participant)

        response = self.client.post(
            reverse("payments:refund", args=[payment.pk]),
            {"amount": "5.00"},
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_user_cannot_refund_another_organizers_payment(self):
        payment = self.create_succeeded_payment()
        staff_user = get_user_model().objects.create_user(
            username="ordinary-staff",
            password="strong-password-123",
            is_staff=True,
            is_superuser=False,
        )
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse("payments:refund", args=[payment.pk]),
            {"amount": "5.00"},
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    @patch("apps.payments.views.create_refund")
    def test_owning_organizer_can_request_refund(self, create_refund):
        payment = self.create_succeeded_payment()
        create_refund.return_value = object()
        self.client.force_login(self.organizer_user)

        response = self.client.post(
            reverse("payments:refund", args=[payment.pk]),
            {"amount": "5.00", "reason": "Event change"},
        )

        self.assertRedirects(response, reverse("accounts:dashboard"))
        create_refund.assert_called_once()
