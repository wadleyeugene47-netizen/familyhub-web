from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.communities.forms import limit_community_field
from apps.events.models import Event, EventCategory, EventLocation

from .models import League, Match, Team, TeamMembership


class LeagueForm(forms.ModelForm):
    class Meta:
        model = League
        fields = ("name", "community", "description", "format", "rules", "starts_on", "ends_on")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "rules": forms.Textarea(attrs={"rows": 4}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, organizer=None, **kwargs):
        super().__init__(*args, **kwargs)
        limit_community_field(self, organizer)

    def clean(self):
        cleaned_data = super().clean()
        starts_on = cleaned_data.get("starts_on")
        ends_on = cleaned_data.get("ends_on")
        if starts_on and ends_on and ends_on < starts_on:
            self.add_error("ends_on", "League end date must not be before its start date.")
        return cleaned_data


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "short_name")


def available_members(league):
    """Approved members who aren't playing for any team in this league."""
    playing = TeamMembership.objects.filter(
        team__league=league, status=TeamMembership.Status.ACTIVE
    ).values("user_id")
    User = get_user_model()
    return (
        User.objects.filter(is_active=True, approval_status=User.Approval.APPROVED)
        .exclude(pk__in=playing)
        .order_by("first_name", "username")
    )


def member_label(user):
    return f"{user.display_name} (@{user.username})"


class CaptainTeamForm(TeamForm):
    """What a captain may change. Fees and the paid mark stay with the host."""

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.instance.league.teams.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another team in this league already has this name.")
        return name


class CaptainAddPlayerForm(forms.Form):
    user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Member", empty_label="Pick a member")
    jersey_number = forms.IntegerField(min_value=0, max_value=99, required=False, label="Shirt number")

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = available_members(league)
        self.fields["user"].label_from_instance = member_label


class MatchScheduleForm(forms.Form):
    title = forms.CharField(max_length=180, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    home_team = forms.ModelChoiceField(queryset=Team.objects.none())
    away_team = forms.ModelChoiceField(queryset=Team.objects.none())
    category = forms.ModelChoiceField(queryset=EventCategory.objects.none())
    starts_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    timezone = forms.CharField(max_length=64, initial="UTC")
    venue_name = forms.CharField(max_length=160, required=False)
    address_line = forms.CharField(max_length=255)
    city = forms.CharField(max_length=100)
    region = forms.CharField(max_length=100, required=False)
    postal_code = forms.CharField(max_length=24, required=False)
    country_code = forms.CharField(max_length=2, min_length=2)
    latitude = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    longitude = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)
    capacity = forms.IntegerField(min_value=1, initial=100)

    def __init__(self, *args, league, **kwargs):
        self.league = league
        super().__init__(*args, **kwargs)
        self.fields["home_team"].queryset = league.teams.all()
        self.fields["away_team"].queryset = league.teams.all()
        self.fields["category"].queryset = EventCategory.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        home_team = cleaned_data.get("home_team")
        away_team = cleaned_data.get("away_team")
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        timezone_name = cleaned_data.get("timezone")
        if home_team and away_team and home_team.pk == away_team.pk:
            self.add_error("away_team", "A team cannot play itself.")
        zone = None
        try:
            zone = ZoneInfo(timezone_name)
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            self.add_error("timezone", "Enter a valid IANA timezone, such as Europe/Lisbon.")
        if starts_at and ends_at and zone:
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
            self.add_error("ends_at", "Match end time must be after its start time.")
        if (cleaned_data.get("latitude") is None) != (cleaned_data.get("longitude") is None):
            self.add_error("latitude", "Enter both coordinates, or leave both blank.")
            self.add_error("longitude", "Enter both coordinates, or leave both blank.")
        return cleaned_data

    @transaction.atomic
    def save(self, *, league, organizer):
        if league.pk != self.league.pk or league.organizer_id != organizer.pk:
            raise ValidationError("The league and organizer do not match this form.")
        data = self.cleaned_data
        home_team = data["home_team"]
        away_team = data["away_team"]
        title = data["title"].strip() or f"{home_team.name} vs {away_team.name}"
        location = EventLocation.objects.create(
            name=data["venue_name"],
            address_line=data["address_line"],
            city=data["city"],
            region=data["region"],
            postal_code=data["postal_code"],
            country_code=data["country_code"].upper(),
            latitude=data["latitude"],
            longitude=data["longitude"],
        )
        event = Event.objects.create(
            organizer=organizer,
            category=data["category"],
            location=location,
            title=title,
            description=data["description"],
            starts_at=data["starts_at"],
            ends_at=data["ends_at"],
            timezone=data["timezone"],
            capacity=data["capacity"],
            price=0,
            currency="USD",
            status=Event.Status.PUBLISHED if league.status == League.Status.ACTIVE else Event.Status.DRAFT,
        )
        match = Match(
            league=league,
            event=event,
            home_team=home_team,
            away_team=away_team,
        )
        match.full_clean()
        match.save()
        return match
