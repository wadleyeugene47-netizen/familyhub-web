from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerPaymentAccount, OrganizerProfile
from apps.registrations.models import Registration

from .models import Payment, PaymentAttempt, WebhookEvent
from .services import (
    CheckoutUnavailable,
    PaymentError,
    UnsupportedCurrency,
    calculate_platform_fee,
    create_checkout_session,
    create_refund,
    process_stripe_event,
    to_minor_units,
)


class PaymentServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organizer_user = User.objects.create_user(
            username="payment-organizer",
            password="strong-password-123",
        )
        cls.organizer = OrganizerProfile.objects.create(
            user=cls.organizer_user,
            display_name="Payment Organizer",
        )
        OrganizerPaymentAccount.objects.create(
            organizer=cls.organizer,
            connected_account_id="acct_test_organizer",
            onboarding_status=OrganizerPaymentAccount.OnboardingStatus.ENABLED,
            charges_enabled=True,
            payouts_enabled=True,
            details_submitted=True,
        )
        cls.participant = User.objects.create_user(
            username="payment-participant",
            password="strong-password-123",
        )
        category = EventCategory.objects.create(name="Paid Event", slug="paid-event")
        location = EventLocation.objects.create(
            address_line="1 Market Street",
            city="Test City",
            country_code="US",
        )
        starts_at = timezone.now() + timedelta(days=14)
        cls.event = Event.objects.create(
            organizer=cls.organizer,
            category=category,
            location=location,
            title="Test Paid Event",
            description="Payment test.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=10,
            price=Decimal("20.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )
        cls.registration = Registration.objects.create(
            event=cls.event,
            participant=cls.participant,
            status=Registration.Status.PENDING_PAYMENT,
        )

    @override_settings(STRIPE_PLATFORM_FEE_BPS=500)
    def test_fee_and_currency_minor_units_are_calculated_from_backend_amount(self):
        self.assertEqual(calculate_platform_fee(Decimal("20.00"), "USD"), Decimal("1.00"))
        self.assertEqual(to_minor_units(Decimal("20.00"), "USD"), 2000)
        self.assertEqual(to_minor_units(Decimal("100"), "JPY"), 100)
        with self.assertRaises(UnsupportedCurrency):
            to_minor_units(Decimal("2.00"), "XXX")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_PLATFORM_FEE_BPS=500)
    @patch("apps.payments.services.stripe.StripeClient")
    def test_checkout_uses_event_price_platform_fee_and_connected_destination(self, stripe_client):
        session_create = stripe_client.return_value.v1.checkout.sessions.create
        session_create.return_value = type(
            "CheckoutSession",
            (),
            {"id": "cs_test_123", "url": "https://checkout.stripe.test/session", "expires_at": 2000000000},
        )()

        attempt = create_checkout_session(
            registration=self.registration,
            success_url="https://marketplace.test/payment-return/",
            cancel_url="https://marketplace.test/payment-cancel/",
        )

        arguments = session_create.call_args.args[0]
        intent_data = arguments["payment_intent_data"]
        self.assertEqual(arguments["line_items"][0]["price_data"]["unit_amount"], 2000)
        self.assertEqual(intent_data["application_fee_amount"], 100)
        self.assertEqual(intent_data["transfer_data"]["destination"], "acct_test_organizer")
        self.assertEqual(attempt.checkout_url, "https://checkout.stripe.test/session")
        self.assertEqual(Payment.objects.get(registration=self.registration).organizer_amount, Decimal("19.00"))

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_checkout_rejects_registration_without_enabled_organizer_account(self):
        OrganizerPaymentAccount.objects.filter(organizer=self.organizer).update(charges_enabled=False)

        with self.assertRaises(CheckoutUnavailable):
            create_checkout_session(
                registration=self.registration,
                success_url="https://marketplace.test/return/",
                cancel_url="https://marketplace.test/cancel/",
            )

    def test_checkout_completion_confirms_only_when_stripe_says_paid(self):
        payment = Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("20.00"),
            currency="USD",
        )
        attempt = PaymentAttempt.objects.create(
            payment=payment,
            stripe_checkout_session_id="cs_test_pending",
            checkout_url="https://checkout.stripe.test/pending",
        )
        event = {
            "id": "evt_test_pending",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_pending",
                    "currency": "usd",
                    "amount_total": 2000,
                    "payment_status": "unpaid",
                    "payment_intent": "pi_test_pending",
                }
            },
        }

        process_stripe_event(event)

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, Registration.Status.PENDING_PAYMENT)
        self.assertEqual(attempt.payment.status, Payment.Status.PENDING)

        event["id"] = "evt_test_paid"
        event["data"]["object"]["payment_status"] = "paid"
        process_stripe_event(event)
        self.registration.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)

    def test_duplicate_webhook_is_processed_once(self):
        payment = Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("20.00"),
            currency="USD",
        )
        PaymentAttempt.objects.create(
            payment=payment,
            stripe_checkout_session_id="cs_test_duplicate",
        )
        event = {
            "id": "evt_test_duplicate",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_duplicate",
                    "currency": "usd",
                    "amount_total": 2000,
                    "payment_status": "paid",
                    "payment_intent": "pi_test_duplicate",
                }
            },
        }

        self.assertTrue(process_stripe_event(event))
        self.assertFalse(process_stripe_event(event))
        self.assertEqual(WebhookEvent.objects.filter(stripe_event_id="evt_test_duplicate").count(), 1)

    def test_payment_intent_success_can_arrive_before_checkout_event(self):
        payment = Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("20.00"),
            currency="USD",
        )
        intent_event = {
            "id": "evt_intent_first",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_intent_first",
                    "metadata": {"payment_id": str(payment.pk)},
                }
            },
        }

        process_stripe_event(intent_event)

        payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.stripe_payment_intent_id, "pi_intent_first")
        self.assertEqual(self.registration.status, Registration.Status.CONFIRMED)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_REFUND_APPLICATION_FEE=True)
    @patch("apps.payments.services.stripe.StripeClient")
    def test_refund_reverses_destination_transfer_and_uses_configured_fee_policy(self, stripe_client):
        payment = Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("1.00"),
            organizer_amount=Decimal("19.00"),
            currency="USD",
            status=Payment.Status.SUCCEEDED,
            stripe_payment_intent_id="pi_refund_test",
        )
        stripe_client.return_value.v1.refunds.create.return_value = type(
            "RefundResponse", (), {"id": "re_test_123", "status": "pending"}
        )()

        refund = create_refund(payment=payment, amount=Decimal("5.00"), reason="Event change")

        arguments = stripe_client.return_value.v1.refunds.create.call_args.args[0]
        self.assertEqual(arguments["amount"], 500)
        self.assertTrue(arguments["reverse_transfer"])
        self.assertTrue(arguments["refund_application_fee"])
        self.assertEqual(refund.stripe_refund_id, "re_test_123")

    def test_refund_cannot_exceed_payment_total(self):
        payment = Payment.objects.create(
            registration=self.registration,
            organizer=self.organizer,
            amount=Decimal("20.00"),
            platform_fee=Decimal("0.00"),
            organizer_amount=Decimal("20.00"),
            currency="USD",
            status=Payment.Status.SUCCEEDED,
            stripe_payment_intent_id="pi_refund_limit",
        )

        with self.assertRaises(PaymentError):
            create_refund(payment=payment, amount=Decimal("21.00"))
