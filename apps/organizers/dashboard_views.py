from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render

from apps.events.models import Event
from apps.payments.models import Payment
from apps.registrations.models import Registration

from .models import OrganizerProfile


@login_required
def organizer_dashboard(request):
    organizer = OrganizerProfile.objects.filter(user=request.user, is_active=True).first()
    if organizer is None:
        return redirect("accounts:dashboard")

    events = organizer.events.select_related("category", "location").annotate(
        confirmed_registrations=Count(
            "registrations",
            filter=Q(registrations__status=Registration.Status.CONFIRMED),
            distinct=True,
        ),
        pending_registrations=Count(
            "registrations",
            filter=Q(registrations__status=Registration.Status.PENDING_PAYMENT),
            distinct=True,
        ),
    )
    organizer_registrations = Registration.objects.filter(event__organizer=organizer)
    registrations = (
        organizer_registrations
        .select_related("event", "participant")
        .order_by("-registered_at")[:30]
    )
    succeeded_payments = Payment.objects.filter(
        organizer=organizer,
        status=Payment.Status.SUCCEEDED,
    )
    totals = succeeded_payments.values("currency").annotate(
        gross=Sum("amount"),
        platform_fees=Sum("platform_fee"),
        organizer_proceeds=Sum("organizer_amount"),
    ).order_by("currency")

    return render(
        request,
        "organizers/dashboard.html",
        {
            "organizer": organizer,
            "communities": organizer.communities.filter(is_active=True),
            "events": events,
            "registrations": registrations,
            "event_count": events.count(),
            "published_count": events.filter(status=Event.Status.PUBLISHED).count(),
            "total_confirmed": organizer_registrations.filter(
                status=Registration.Status.CONFIRMED
            ).count(),
            "revenue_by_currency": totals,
        },
    )
