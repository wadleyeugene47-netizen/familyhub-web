from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone as datetime_timezone

import stripe
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.notifications.services import queue_payment_email, queue_refund_email
from apps.organizers.models import OrganizerPaymentAccount, OrganizerProfile
from apps.registrations.models import Registration

from .models import (
    OrganizerPayout,
    Payment,
    PaymentAttempt,
    PaymentDispute,
    Refund,
    TransferRecord,
    WebhookEvent,
)


ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG",
    "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
TWO_DECIMAL_CURRENCIES = {
    "AED", "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "IDR", "ILS", "INR", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN",
    "SAR", "SEK", "SGD", "THB", "TRY", "TWD", "USD", "ZAR",
}


class PaymentError(Exception):
    """Base exception for payment workflow errors."""


class CheckoutUnavailable(PaymentError):
    pass


class CheckoutAlreadyStarted(PaymentError):
    pass


class UnsupportedCurrency(PaymentError):
    pass


class InvalidWebhook(PaymentError):
    pass


def currency_exponent(currency):
    currency = currency.upper()
    if len(currency) != 3 or not currency.isalpha():
        raise UnsupportedCurrency("Use a three-letter ISO currency code.")
    if currency in ZERO_DECIMAL_CURRENCIES:
        return 0
    if currency in TWO_DECIMAL_CURRENCIES:
        return 2
    raise UnsupportedCurrency("Checkout is not configured for this currency's minor-unit rules.")


def to_minor_units(amount, currency):
    exponent = currency_exponent(currency)
    scale = Decimal(10) ** exponent
    scaled = amount * scale
    if scaled != scaled.to_integral_value():
        raise UnsupportedCurrency("This amount cannot be represented in the currency's smallest unit.")
    return int(scaled)


def from_minor_units(amount, currency):
    return Decimal(amount).scaleb(-currency_exponent(currency)).quantize(Decimal("0.01"))


def calculate_platform_fee(amount, currency):
    basis_points = settings.STRIPE_PLATFORM_FEE_BPS
    if not 0 <= basis_points < 10000:
        raise ImproperlyConfigured("STRIPE_PLATFORM_FEE_BPS must be between 0 and 9999.")
    amount_minor = to_minor_units(amount, currency)
    fee_minor = int(
        (Decimal(amount_minor) * Decimal(basis_points) / Decimal(10000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return from_minor_units(fee_minor, currency)


def _stripe_client():
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("Set STRIPE_SECRET_KEY to a Stripe test-mode secret key.")
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


def create_checkout_session(*, registration, success_url, cancel_url):
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("Set STRIPE_SECRET_KEY to a Stripe test-mode secret key.")

    with transaction.atomic():
        locked_registration = (
            Registration.objects.select_for_update()
            .select_related("event__organizer")
            .get(pk=registration.pk)
        )
        event = locked_registration.event
        if (
            locked_registration.status != Registration.Status.PENDING_PAYMENT
            or event.status != event.Status.PUBLISHED
            or event.price <= 0
        ):
            raise CheckoutUnavailable("This registration is not eligible for checkout.")
        account = OrganizerPaymentAccount.objects.filter(
            organizer=event.organizer,
            charges_enabled=True,
        ).first()
        if account is None:
            raise CheckoutUnavailable("The organizer is not currently able to accept payments.")

        currency = event.currency.upper()
        total_minor = to_minor_units(event.price, currency)
        fee = calculate_platform_fee(event.price, currency)
        fee_minor = to_minor_units(fee, currency)
        payment, _ = Payment.objects.get_or_create(
            registration=locked_registration,
            defaults={
                "organizer": event.organizer,
                "amount": event.price,
                "platform_fee": fee,
                "organizer_amount": event.price - fee,
                "currency": currency,
            },
        )
        if payment.status == Payment.Status.SUCCEEDED:
            raise CheckoutUnavailable("This registration has already been paid.")
        if payment.amount != event.price or payment.currency != currency:
            raise CheckoutUnavailable("The event price changed. Please contact the organizer.")

        active_attempt = payment.attempts.filter(
            status=PaymentAttempt.Status.OPEN,
            expires_at__gt=timezone.now(),
        ).order_by("-created_at").first()
        if active_attempt:
            if active_attempt.checkout_url:
                return active_attempt
            raise CheckoutAlreadyStarted("Checkout is already being prepared. Try again shortly.")
        attempt = PaymentAttempt.objects.create(payment=payment)
        payment_id = str(payment.pk)
        attempt_id = str(attempt.pk)
        event_title = event.title
        connected_account_id = account.connected_account_id

    try:
        client = _stripe_client()
        session = client.v1.checkout.sessions.create(
            {
                "mode": "payment",
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency.lower(),
                            "unit_amount": total_minor,
                            "product_data": {"name": event_title[:250]},
                        },
                        "quantity": 1,
                    }
                ],
                "client_reference_id": str(locked_registration.pk),
                "metadata": {
                    "payment_id": payment_id,
                    "payment_attempt_id": attempt_id,
                    "registration_id": str(locked_registration.pk),
                },
                "payment_intent_data": {
                    "application_fee_amount": fee_minor,
                    "transfer_data": {"destination": connected_account_id},
                    "metadata": {"payment_id": payment_id},
                },
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
            options={"idempotency_key": str(attempt.idempotency_key)},
        )
    except stripe.StripeError as error:
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            failure_code=getattr(error, "code", "stripe_error") or "stripe_error",
        )
        Payment.objects.filter(pk=payment_id).update(
            status=Payment.Status.FAILED,
            updated_at=timezone.now(),
        )
        raise CheckoutUnavailable("Stripe could not start checkout. Try again later.") from error

    expires_at = datetime.fromtimestamp(session.expires_at, tz=datetime_timezone.utc)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(
        stripe_checkout_session_id=session.id,
        checkout_url=session.url,
        expires_at=expires_at,
        updated_at=timezone.now(),
    )
    attempt.refresh_from_db()
    return attempt


def _value(obj, key, default=None):
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _minor_to_payment_currency(amount, currency):
    return from_minor_units(int(amount), currency.upper())


def _find_payment_for_intent(payment_intent_id):
    if not payment_intent_id:
        return None
    return Payment.objects.select_for_update().filter(
        stripe_payment_intent_id=payment_intent_id
    ).first()


def _succeed_payment(payment, *, payment_intent_id=None):
    was_succeeded = payment.status == Payment.Status.SUCCEEDED
    if payment_intent_id:
        payment.stripe_payment_intent_id = payment_intent_id
    payment.status = Payment.Status.SUCCEEDED
    payment.save(update_fields=("stripe_payment_intent_id", "status", "updated_at"))
    registration = payment.registration
    if registration.status == Registration.Status.PENDING_PAYMENT:
        registration.status = Registration.Status.CONFIRMED
        registration.save(update_fields=("status", "updated_at"))
    payment.attempts.filter(status__in=[PaymentAttempt.Status.OPEN, PaymentAttempt.Status.PROCESSING]).update(
        status=PaymentAttempt.Status.SUCCEEDED,
        updated_at=timezone.now(),
    )
    if not was_succeeded:
        queue_payment_email(payment, succeeded=True)


def _fail_attempt(attempt, *, expired=False):
    attempt.status = PaymentAttempt.Status.EXPIRED if expired else PaymentAttempt.Status.FAILED
    attempt.save(update_fields=("status", "updated_at"))
    payment = attempt.payment
    was_failed = payment.status == Payment.Status.FAILED
    if payment.status != Payment.Status.SUCCEEDED:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=("status", "updated_at"))
        if not was_failed:
            queue_payment_email(payment, succeeded=False)
        registration = payment.registration
        if registration.status == Registration.Status.PENDING_PAYMENT:
            registration.status = Registration.Status.CANCELLED
            registration.save(update_fields=("status", "updated_at"))


def _process_checkout_session(event_type, session):
    session_id = _value(session, "id")
    attempt = PaymentAttempt.objects.select_for_update().filter(
        stripe_checkout_session_id=session_id
    ).select_related("payment__registration").first()
    if attempt is None:
        raise InvalidWebhook("Checkout session is not associated with a local payment.")
    payment = attempt.payment
    currency = (_value(session, "currency", "") or "").upper()
    amount_total = _value(session, "amount_total")
    if currency != payment.currency or amount_total != to_minor_units(payment.amount, payment.currency):
        raise InvalidWebhook("Checkout session amount does not match the stored payment.")

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        intent_id = _value(session, "payment_intent")
        payment.stripe_payment_intent_id = intent_id or payment.stripe_payment_intent_id
        payment.save(update_fields=("stripe_payment_intent_id", "updated_at"))
        if _value(session, "payment_status") == "paid" or event_type.endswith("async_payment_succeeded"):
            _succeed_payment(payment, payment_intent_id=intent_id)
        else:
            attempt.status = PaymentAttempt.Status.PROCESSING
            attempt.save(update_fields=("status", "updated_at"))
            payment.status = Payment.Status.PENDING
            payment.save(update_fields=("status", "updated_at"))
    elif event_type in ("checkout.session.async_payment_failed", "checkout.session.expired"):
        _fail_attempt(attempt, expired=event_type.endswith("expired"))


def _process_payment_intent(event_type, intent):
    intent_id = _value(intent, "id")
    payment = _find_payment_for_intent(intent_id)
    if payment is None:
        metadata = _value(intent, "metadata", {}) or {}
        payment_id = _value(metadata, "payment_id")
        payment = Payment.objects.select_for_update().filter(pk=payment_id).first()
    if payment is None:
        return
    if intent_id and payment.stripe_payment_intent_id != intent_id:
        payment.stripe_payment_intent_id = intent_id
    if event_type == "payment_intent.succeeded":
        _succeed_payment(payment, payment_intent_id=intent_id)
    elif event_type == "payment_intent.payment_failed" and payment.status != Payment.Status.SUCCEEDED:
        was_failed = payment.status == Payment.Status.FAILED
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=("stripe_payment_intent_id", "status", "updated_at"))
        if not was_failed:
            queue_payment_email(payment, succeeded=False)
    elif event_type != "payment_intent.payment_failed":
        payment.save(update_fields=("stripe_payment_intent_id", "updated_at"))


def _process_charge_refund(charge):
    payment_intent_id = _value(charge, "payment_intent")
    payment = _find_payment_for_intent(payment_intent_id)
    if payment is None:
        return
    refunds = _value(_value(charge, "refunds", {}), "data", []) or []
    currency = (_value(charge, "currency", payment.currency) or payment.currency).upper()
    for stripe_refund in refunds:
        refund_id = _value(stripe_refund, "id")
        if not refund_id:
            continue
        refund, _ = Refund.objects.update_or_create(
            stripe_refund_id=refund_id,
            defaults={
                "payment": payment,
                "amount": _minor_to_payment_currency(_value(stripe_refund, "amount", 0), currency),
                "currency": currency,
                "status": Refund.Status.SUCCEEDED
                if _value(stripe_refund, "status") == "succeeded"
                else Refund.Status.PENDING,
                "reason": _value(stripe_refund, "reason", "") or "",
            },
        )
        queue_refund_email(refund)
    amount_refunded = _value(charge, "amount_refunded", 0)
    total_minor = to_minor_units(payment.amount, payment.currency)
    if amount_refunded >= total_minor:
        payment.status = Payment.Status.REFUNDED
        if payment.registration.status in (Registration.Status.CONFIRMED, Registration.Status.PENDING_PAYMENT):
            payment.registration.status = Registration.Status.REFUNDED
            payment.registration.save(update_fields=("status", "updated_at"))
    elif amount_refunded > 0:
        payment.status = Payment.Status.PARTIALLY_REFUNDED
    payment.save(update_fields=("status", "updated_at"))


def _process_dispute(event_type, dispute):
    payment_intent_id = _value(dispute, "payment_intent")
    payment = _find_payment_for_intent(payment_intent_id)
    if payment is None:
        return
    dispute_status = _value(dispute, "status", "unknown")
    if event_type == "charge.dispute.created":
        payment.status = Payment.Status.DISPUTED
    elif event_type == "charge.dispute.closed":
        payment.status = Payment.Status.FAILED if dispute_status == "lost" else Payment.Status.SUCCEEDED
    payment.save(update_fields=("status", "updated_at"))
    PaymentDispute.objects.update_or_create(
        stripe_dispute_id=_value(dispute, "id"),
        defaults={
            "payment": payment,
            "amount": _minor_to_payment_currency(_value(dispute, "amount", 0), payment.currency),
            "currency": (_value(dispute, "currency", payment.currency) or payment.currency).upper(),
            "reason": _value(dispute, "reason", "") or "",
            "status": dispute_status,
        },
    )


def _process_transfer(event_type, transfer):
    metadata = _value(transfer, "metadata", {}) or {}
    payment_id = _value(metadata, "payment_id")
    payment = Payment.objects.select_for_update().filter(pk=payment_id).first()
    transfer_id = _value(transfer, "id")
    if payment is None or not transfer_id:
        return
    status = TransferRecord.Status.PENDING
    if event_type == "transfer.reversed":
        status = TransferRecord.Status.REVERSED
    TransferRecord.objects.update_or_create(
        payment=payment,
        defaults={
            "stripe_transfer_id": transfer_id,
            "amount": _minor_to_payment_currency(_value(transfer, "amount", 0), payment.currency),
            "currency": (_value(transfer, "currency", payment.currency) or payment.currency).upper(),
            "status": status,
        },
    )


def _process_charge_succeeded(charge):
    payment_intent_id = _value(charge, "payment_intent")
    payment = _find_payment_for_intent(payment_intent_id)
    transfer_id = _value(charge, "transfer")
    if payment is None or not transfer_id:
        return
    TransferRecord.objects.update_or_create(
        payment=payment,
        defaults={
            "stripe_transfer_id": transfer_id,
            "amount": payment.organizer_amount,
            "currency": payment.currency,
            "status": TransferRecord.Status.PENDING,
        },
    )


def _process_payout(event_type, payout, connected_account_id):
    if not connected_account_id:
        return
    organizer = OrganizerProfile.objects.select_for_update().filter(
        payment_account__connected_account_id=connected_account_id
    ).first()
    payout_id = _value(payout, "id")
    if organizer is None or not payout_id:
        return
    status_by_event = {
        "payout.paid": OrganizerPayout.Status.PAID,
        "payout.failed": OrganizerPayout.Status.FAILED,
        "payout.canceled": OrganizerPayout.Status.CANCELED,
        "payout.created": OrganizerPayout.Status.PENDING,
    }
    currency = (_value(payout, "currency", "") or "").upper()
    arrival_timestamp = _value(payout, "arrival_date")
    arrival_date = (
        datetime.fromtimestamp(arrival_timestamp, tz=datetime_timezone.utc).date()
        if arrival_timestamp
        else None
    )
    OrganizerPayout.objects.update_or_create(
        stripe_payout_id=payout_id,
        defaults={
            "organizer": organizer,
            "amount": _minor_to_payment_currency(_value(payout, "amount", 0), currency),
            "currency": currency,
            "status": status_by_event[event_type],
            "arrival_date": arrival_date,
        },
    )


@transaction.atomic
def process_stripe_event(event):
    event_id = _value(event, "id")
    event_type = _value(event, "type")
    if not event_id or not event_type:
        raise InvalidWebhook("Stripe event is missing its identifier or type.")
    webhook, created = WebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={"event_type": event_type},
    )
    webhook = WebhookEvent.objects.select_for_update().get(pk=webhook.pk)
    if not created and webhook.status == WebhookEvent.Status.PROCESSED:
        return False

    data = _value(_value(event, "data", {}), "object", {})
    if event_type.startswith("checkout.session."):
        _process_checkout_session(event_type, data)
    elif event_type in ("payment_intent.succeeded", "payment_intent.payment_failed"):
        _process_payment_intent(event_type, data)
    elif event_type == "charge.refunded":
        _process_charge_refund(data)
    elif event_type == "charge.succeeded":
        _process_charge_succeeded(data)
    elif event_type in ("charge.dispute.created", "charge.dispute.closed"):
        _process_dispute(event_type, data)
    elif event_type in ("transfer.created", "transfer.reversed"):
        _process_transfer(event_type, data)
    elif event_type in ("payout.created", "payout.paid", "payout.failed", "payout.canceled"):
        _process_payout(event_type, data, _value(event, "account"))

    webhook.status = WebhookEvent.Status.PROCESSED
    webhook.processed_at = timezone.now()
    webhook.save(update_fields=("status", "processed_at"))
    return True


def create_refund(*, payment, amount, reason=""):
    if amount <= 0:
        raise PaymentError("Refund amount must be greater than zero.")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status not in (Payment.Status.SUCCEEDED, Payment.Status.PARTIALLY_REFUNDED):
            raise PaymentError("Only successful, not-fully-refunded payments can be refunded.")
        if not payment.stripe_payment_intent_id:
            raise PaymentError("The Stripe payment intent is not available for this payment.")
        refunded = sum(
            (
                refund.amount
                for refund in payment.refunds.filter(
                    status__in=[Refund.Status.PENDING, Refund.Status.SUCCEEDED]
                )
            ),
            Decimal("0.00"),
        )
        if refunded + amount > payment.amount:
            raise PaymentError("Refund amount exceeds the remaining refundable balance.")
        refund_record = payment.refunds.filter(
            amount=amount,
            reason=reason[:255],
            status=Refund.Status.PENDING,
        ).order_by("created_at").first()
        if refund_record is None:
            refund_record = Refund.objects.create(
                payment=payment,
                amount=amount,
                currency=payment.currency,
                status=Refund.Status.PENDING,
                reason=reason[:255],
            )
        elif refund_record.stripe_refund_id:
            return refund_record

    client = _stripe_client()
    amount_minor = to_minor_units(amount, payment.currency)
    try:
        stripe_refund = client.v1.refunds.create(
            {
                "payment_intent": payment.stripe_payment_intent_id,
                "amount": amount_minor,
                "reason": "requested_by_customer",
                "reverse_transfer": True,
                "refund_application_fee": settings.STRIPE_REFUND_APPLICATION_FEE,
                "metadata": {"payment_id": str(payment.pk), "reason": reason[:200]},
            },
            options={"idempotency_key": str(refund_record.idempotency_key)},
        )
    except stripe.StripeError as error:
        raise PaymentError("Stripe could not create the refund.") from error

    refund_record.stripe_refund_id = stripe_refund.id
    refund_record.save(update_fields=("stripe_refund_id", "updated_at"))
    return refund_record
