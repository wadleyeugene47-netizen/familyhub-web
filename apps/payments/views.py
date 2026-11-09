from decimal import Decimal, InvalidOperation

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, HttpResponseServerError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from apps.hub.features import is_enabled
from apps.registrations.models import Registration

from .models import Payment
from .services import (
    CheckoutAlreadyStarted,
    CheckoutUnavailable,
    InvalidWebhook,
    PaymentError,
    create_checkout_session,
    create_refund,
    process_stripe_event,
)


@login_required
def checkout(request, registration_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    registration = get_object_or_404(
        Registration.objects.select_related("event"),
        pk=registration_id,
        participant=request.user,
        status=Registration.Status.PENDING_PAYMENT,
    )
    if not is_enabled("payments"):
        messages.error(request, "Online payments are not available yet.")
        return redirect("events:detail", event_id=registration.event_id)
    return_path = reverse("payments:checkout_return", args=[registration.pk])
    success_url = (
        request.build_absolute_uri(return_path)
        + "?checkout=success&session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = request.build_absolute_uri(return_path) + "?checkout=cancelled"
    try:
        attempt = create_checkout_session(
            registration=registration,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except (ImproperlyConfigured, CheckoutUnavailable, CheckoutAlreadyStarted) as error:
        messages.error(request, str(error))
        return redirect("events:detail", event_id=registration.event_id)
    return redirect(attempt.checkout_url)


@login_required
def checkout_return(request, registration_id):
    registration = get_object_or_404(
        Registration.objects.select_related("event"),
        pk=registration_id,
        participant=request.user,
    )
    payment = Payment.objects.filter(registration=registration).first()
    return render(
        request,
        "payments/checkout_return.html",
        {"registration": registration, "payment": payment},
    )


@login_required
def refund_payment(request, payment_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    payment = get_object_or_404(
        Payment.objects.select_related("organizer__user"),
        pk=payment_id,
    )
    if not (request.user.is_superuser or payment.organizer.user_id == request.user.id):
        return HttpResponse("Not authorized to refund this payment.", status=403)
    try:
        amount = Decimal(request.POST.get("amount", ""))
    except (InvalidOperation, TypeError):
        messages.error(request, "Enter a valid refund amount.")
        return redirect("accounts:dashboard")
    try:
        create_refund(
            payment=payment,
            amount=amount,
            reason=request.POST.get("reason", "")[:200],
        )
    except (ImproperlyConfigured, PaymentError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Refund request sent to Stripe; its final status will be confirmed by webhook.")
    return redirect("accounts:dashboard")


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse("Webhook signing secret is not configured.", status=503)
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            request.body,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponseBadRequest("Invalid webhook signature or payload.")
    try:
        process_stripe_event(event)
    except (InvalidWebhook, PaymentError):
        return HttpResponseServerError("Webhook processing failed.")
    return HttpResponse(status=200)
