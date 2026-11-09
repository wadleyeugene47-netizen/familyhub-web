from collections import Counter
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.leagues.models import League, Match
from apps.organizers.models import OrganizerPaymentAccount, OrganizerProfile
from apps.registrations.models import Registration
from apps.registrations.services import (
    AlreadyRegistered,
    EventFull,
    EventUnavailable,
    register_participant,
)
from apps.notifications.services import queue_event_update_emails

from .forms import EventForm
from .models import Event, EventCategory
from .services import cancel_event, needs_owner_approval, submit_for_approval
from .themes import THEMES, theme_for


def _paid_event_publish_allowed(organizer, *, status, price):
    if status != Event.Status.PUBLISHED or price <= 0:
        return True
    payment_account = OrganizerPaymentAccount.objects.filter(organizer=organizer).first()
    return bool(payment_account and payment_account.charges_enabled)


def _upcoming_events():
    return Event.objects.filter(
        status=Event.Status.PUBLISHED,
        starts_at__gte=timezone.now(),
        category__is_active=True,
    ).select_related("category", "location", "organizer").prefetch_related("images")


def home(request):
    upcoming = _upcoming_events()
    featured = list(upcoming[:6])
    events_per_category = dict(
        upcoming.order_by().values_list("category_id").annotate(total=Count("id"))
    )
    counts = Counter()
    first_category = {}
    for category in EventCategory.objects.filter(is_active=True):
        key = theme_for(category)
        counts[key] += events_per_category.get(category.pk, 0)
        first_category.setdefault(key, category)
    category_tiles = []
    for key, label, blurb, keywords in THEMES:
        category = first_category.get(key)
        if category is not None:
            url = reverse("events:list") + "?" + urlencode({"category": category.slug})
        elif keywords:
            url = reverse("events:list") + "?" + urlencode({"q": keywords[0]})
        else:
            url = reverse("events:list")
        category_tiles.append(
            {"key": key, "label": label, "blurb": blurb, "url": url, "count": counts[key]}
        )
    spotlight_leagues = list(
        League.objects.filter(status=League.Status.ACTIVE).annotate(
            team_count=Count("teams", distinct=True),
            games_played=Count("matches", filter=Q(matches__status=Match.Status.COMPLETED), distinct=True),
        )[:3]
    )
    for league in spotlight_leagues:
        league.next_match = (
            league.matches.filter(status=Match.Status.SCHEDULED, event__starts_at__gte=timezone.now())
            .select_related("event", "home_team", "away_team")
            .order_by("event__starts_at")
            .first()
        )
    return render(
        request,
        "home.html",
        {
            "spotlight_leagues": spotlight_leagues,
            "featured_events": featured,
            "hero_events": featured[:3],
            "category_tiles": category_tiles,
            "upcoming_count": upcoming.count(),
            "city_count": upcoming.values("location__city").distinct().count(),
        },
    )


def event_list(request):
    events = _upcoming_events()
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    city = request.GET.get("city", "").strip()
    if query:
        events = events.filter(Q(title__icontains=query) | Q(description__icontains=query))
    if category:
        events = events.filter(category__slug=category)
    if city:
        events = events.filter(location__city__icontains=city)
    categories = EventCategory.objects.filter(is_active=True)
    page = Paginator(events, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "events/event_list.html",
        {
            "page": page,
            "categories": categories,
            "query": query,
            "selected_category": category,
            "city": city,
        },
    )


def event_detail(request, event_id):
    events = Event.objects.select_related(
        "category", "location", "organizer__user"
    ).prefetch_related("images")
    if request.user.is_superuser:
        visibility = Q()
    elif request.user.is_authenticated:
        visibility = Q(status=Event.Status.PUBLISHED) | Q(organizer__user=request.user)
    else:
        visibility = Q(status=Event.Status.PUBLISHED)
    event = get_object_or_404(events.filter(visibility), pk=event_id)
    location = event.location
    map_embed_url = None
    if location.latitude is not None and location.longitude is not None:
        latitude = location.latitude
        longitude = location.longitude
        offset = Decimal("0.01")
        bbox = ",".join(
            str(value)
            for value in (
                max(Decimal("-180"), longitude - offset),
                max(Decimal("-90"), latitude - offset),
                min(Decimal("180"), longitude + offset),
                min(Decimal("90"), latitude + offset),
            )
        )
        map_embed_url = "https://www.openstreetmap.org/export/embed.html?" + urlencode(
            {"bbox": bbox, "layer": "mapnik", "marker": f"{latitude},{longitude}"}
        )
        directions_url = "https://www.openstreetmap.org/directions?" + urlencode(
            {"to": f"{latitude},{longitude}"}
        )
    else:
        address = ", ".join(
            value
            for value in (
                location.address_line,
                location.city,
                location.region,
                location.postal_code,
                location.country_code,
            )
            if value
        )
        directions_url = "https://www.openstreetmap.org/search?" + urlencode(
            {"query": address}
        )
    registration = None
    if request.user.is_authenticated:
        registration = Registration.objects.filter(
            event=event,
            participant=request.user,
            status__in=[Registration.Status.PENDING_PAYMENT, Registration.Status.CONFIRMED],
        ).first()
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "registration": registration,
            "map_embed_url": map_embed_url,
            "directions_url": directions_url,
        },
    )


@login_required
def event_register(request, event_id):
    if request.method != "POST":
        return redirect("events:detail", event_id=event_id)
    try:
        registration = register_participant(
            event_id=event_id,
            participant=request.user,
        )
    except AlreadyRegistered as error:
        messages.warning(request, str(error))
    except EventFull as error:
        messages.error(request, str(error))
    except EventUnavailable as error:
        messages.error(request, str(error))
    else:
        if registration.status == Registration.Status.CONFIRMED:
            messages.success(request, "Your registration is confirmed.")
        else:
            messages.info(
                request,
                "Registration saved. Payment checkout will be available in a later step.",
            )
    return redirect("events:detail", event_id=event_id)


@login_required
def organizer_event_list(request):
    profile = OrganizerProfile.objects.filter(user=request.user).first()
    events = Event.objects.none() if profile is None else profile.events.select_related("category", "location")
    return render(request, "events/organizer_event_list.html", {"events": events})


@login_required
def event_create(request):
    profile, _ = OrganizerProfile.objects.get_or_create(
        user=request.user,
        defaults={"display_name": request.user.get_full_name() or request.user.username},
    )
    if not profile.is_active:
        return HttpResponseForbidden("This organizer profile is inactive.")
    needs_approval = needs_owner_approval(request.user)
    form = EventForm(
        request.POST or None,
        needs_approval=needs_approval,
        organizer=profile,
        initial={"community": request.GET.get("community")},
    )
    if form.is_valid():
        if not _paid_event_publish_allowed(
            profile,
            status=form.cleaned_data["status"],
            price=form.cleaned_data["price"],
        ):
            form.add_error(
                "status",
                "Complete Stripe onboarding and enable charges before publishing a paid event.",
            )
            return render(request, "events/event_form.html", {"form": form, "heading": "Create event"})
        event = form.save(commit=False)
        event.organizer = profile
        event.save()
        if needs_approval and event.status == Event.Status.PUBLISHED:
            submit_for_approval(request, event)
            messages.info(request, "Your event was sent to the owner. It goes live once it is approved.")
        return redirect("events:organizer_list")
    return render(request, "events/event_form.html", {"form": form, "heading": "Create event"})


@login_required
def event_update(request, event_id):
    event = get_object_or_404(Event, pk=event_id, organizer__user=request.user)
    if event.status in (Event.Status.CANCELLED, Event.Status.COMPLETED):
        return HttpResponseForbidden("Cancelled or completed events cannot be edited.")
    needs_approval = needs_owner_approval(request.user)
    was_published = event.status == Event.Status.PUBLISHED
    form = EventForm(
        request.POST or None, instance=event, needs_approval=needs_approval, organizer=event.organizer
    )
    if form.is_valid():
        notify_attendees = was_published and bool(
            set(form.changed_data)
            & {
                "title",
                "description",
                "starts_at",
                "ends_at",
                "timezone",
                "venue_name",
                "address_line",
                "city",
                "region",
                "postal_code",
                "country_code",
                "latitude",
                "longitude",
            }
        )
        if not _paid_event_publish_allowed(
            event.organizer,
            status=form.cleaned_data["status"],
            price=form.cleaned_data["price"],
        ):
            form.add_error(
                "status",
                "Complete Stripe onboarding and enable charges before publishing a paid event.",
            )
            return render(request, "events/event_form.html", {"form": form, "heading": "Edit event"})
        updated_event = form.save(commit=False)
        updated_event.save()
        if needs_approval and not was_published and updated_event.status == Event.Status.PUBLISHED:
            submit_for_approval(request, updated_event)
            messages.info(request, "Your event was sent to the owner. It goes live once it is approved.")
        if notify_attendees:
            queue_event_update_emails(updated_event)
        return redirect("events:organizer_list")
    return render(request, "events/event_form.html", {"form": form, "heading": "Edit event"})


@login_required
def event_cancel(request, event_id):
    if request.method != "POST":
        return HttpResponseForbidden("Use the cancellation form to cancel an event.")
    with transaction.atomic():
        event = get_object_or_404(
            Event.objects.select_for_update(),
            pk=event_id,
            organizer__user=request.user,
            status__in=[Event.Status.DRAFT, Event.Status.PENDING, Event.Status.PUBLISHED],
        )
        cancel_event(event)
    messages.info(
        request,
        "Event cancelled. Existing payments are not automatically refunded; review the cancellation policy and resolve them separately.",
    )
    return redirect("events:organizer_list")
