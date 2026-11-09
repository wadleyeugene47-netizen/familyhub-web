from django import forms
from django.db import transaction
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.communities.forms import limit_community_field

from .models import Event, EventCategory, EventLocation


class EventForm(forms.ModelForm):
    address_line = forms.CharField(max_length=255)
    venue_name = forms.CharField(max_length=160, required=False)
    city = forms.CharField(max_length=100)
    region = forms.CharField(max_length=100, required=False)
    postal_code = forms.CharField(max_length=24, required=False)
    country_code = forms.CharField(max_length=2, min_length=2)
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        min_value=-90,
        max_value=90,
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        min_value=-180,
        max_value=180,
    )

    status = forms.ChoiceField(
        choices=(
            (Event.Status.DRAFT, "Save as draft"),
            (Event.Status.PUBLISHED, "Publish event"),
        ),
    )

    class Meta:
        model = Event
        fields = (
            "title",
            "community",
            "description",
            "category",
            "starts_at",
            "ends_at",
            "timezone",
            "capacity",
            "price",
            "currency",
            "status",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "starts_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "ends_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
        }

    def __init__(self, *args, needs_approval=False, organizer=None, **kwargs):
        super().__init__(*args, **kwargs)
        limit_community_field(self, organizer)
        if needs_approval and self.instance.status != Event.Status.PUBLISHED:
            self.fields["status"].choices = (
                (Event.Status.DRAFT, "Save as draft"),
                (Event.Status.PUBLISHED, "Send to the owner for approval"),
            )
        if self.instance.status in (Event.Status.PENDING, Event.Status.REJECTED):
            self.initial["status"] = Event.Status.PUBLISHED
        self.fields["starts_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["ends_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["category"].queryset = EventCategory.objects.filter(is_active=True)
        if self.instance.pk and self.instance.location_id:
            location = self.instance.location
            self.initial.update(
                {
                    "venue_name": location.name,
                    "address_line": location.address_line,
                    "city": location.city,
                    "region": location.region,
                    "postal_code": location.postal_code,
                    "country_code": location.country_code,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                }
            )

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        timezone_name = cleaned_data.get("timezone")
        zone = None
        if timezone_name:
            try:
                zone = ZoneInfo(timezone_name)
            except (KeyError, ValueError, ZoneInfoNotFoundError):
                self.add_error("timezone", "Enter a valid IANA timezone, such as Europe/Lisbon.")
        if zone:
            for field_name in ("starts_at", "ends_at"):
                value = cleaned_data.get(field_name)
                if value:
                    local_value = value.replace(tzinfo=None).replace(tzinfo=zone)
                    round_trip = local_value.astimezone(datetime_timezone.utc).astimezone(zone)
                    if round_trip.replace(tzinfo=None) != value.replace(tzinfo=None):
                        self.add_error(field_name, "This local time does not exist in the selected timezone.")
                    cleaned_data[field_name] = local_value
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "The end time must be after the start time.")
        latitude = cleaned_data.get("latitude")
        longitude = cleaned_data.get("longitude")
        if (latitude is None) != (longitude is None):
            self.add_error("latitude", "Enter both coordinates, or leave both blank.")
            self.add_error("longitude", "Enter both coordinates, or leave both blank.")
        currency = cleaned_data.get("currency")
        if currency and currency != currency.upper():
            self.add_error("currency", "Use uppercase ISO currency codes, such as USD or EUR.")
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        event = super().save(commit=False)
        location_values = {
            "name": self.cleaned_data["venue_name"],
            "address_line": self.cleaned_data["address_line"],
            "city": self.cleaned_data["city"],
            "region": self.cleaned_data["region"],
            "postal_code": self.cleaned_data["postal_code"],
            "country_code": self.cleaned_data["country_code"].upper(),
            "latitude": self.cleaned_data["latitude"],
            "longitude": self.cleaned_data["longitude"],
        }
        if not event._state.adding and event.location.events.exclude(pk=event.pk).exists():
            event.location = EventLocation.objects.create(**location_values)
        elif not event._state.adding:
            for field, value in location_values.items():
                setattr(event.location, field, value)
            event.location.save()
        else:
            event.location = EventLocation.objects.create(**location_values)
        if commit:
            event.save()
            self.save_m2m()
        return event
