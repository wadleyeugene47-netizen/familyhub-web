"""Forms the host uses in the host panel to run leagues."""
from datetime import timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth import get_user_model

from apps.communities.forms import limit_community_field

from .forms import available_members, member_label
from .models import League, Team, TeamMembership


class HostLeagueForm(forms.ModelForm):
    class Meta:
        model = League
        fields = ("name", "community", "description", "rules", "starts_on", "ends_on")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "rules": forms.Textarea(attrs={"rows": 3}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, organizer, **kwargs):
        self.organizer = organizer
        super().__init__(*args, **kwargs)
        limit_community_field(self, organizer)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = League.objects.filter(organizer=self.organizer, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have a league with this name.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        starts_on, ends_on = cleaned_data.get("starts_on"), cleaned_data.get("ends_on")
        if starts_on and ends_on and ends_on < starts_on:
            self.add_error("ends_on", "The end date must not be before the start date.")
        return cleaned_data


class HostTeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "short_name", "captain_fee", "fee_currency", "fee_paid")
        labels = {"fee_currency": "Currency"}

    def __init__(self, *args, league, **kwargs):
        self.league = league
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.league.teams.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another team in this league already has this name.")
        return name

    def clean_fee_currency(self):
        return self.cleaned_data["fee_currency"].strip().upper()


class AddPlayerForm(forms.Form):
    user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Member")
    team = forms.ModelChoiceField(queryset=Team.objects.none())
    role = forms.ChoiceField(choices=TeamMembership.Role.choices, initial=TeamMembership.Role.PLAYER)
    jersey_number = forms.IntegerField(min_value=0, max_value=999, required=False, label="Shirt number")

    def __init__(self, *args, league, **kwargs):
        self.league = league
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = available_members(league)
        self.fields["user"].label_from_instance = member_label
        self.fields["team"].queryset = league.teams.all()

    def clean(self):
        cleaned_data = super().clean()
        team, number = cleaned_data.get("team"), cleaned_data.get("jersey_number")
        if team and number is not None and team.active_memberships().filter(jersey_number=number).exists():
            self.add_error("jersey_number", "Someone on this team already wears this number.")
        return cleaned_data


class NextRoundForm(forms.Form):
    first_kickoff = forms.DateTimeField(
        label="First game starts",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    minutes_per_game = forms.IntegerField(
        min_value=10,
        max_value=240,
        initial=60,
        help_text="Games in the round are played one after another.",
    )
    timezone = forms.CharField(max_length=64, initial="UTC")
    venue_name = forms.CharField(max_length=160, required=False, label="Field or venue")
    address_line = forms.CharField(max_length=255, label="Address")
    city = forms.CharField(max_length=100)
    country_code = forms.CharField(max_length=2, min_length=2, label="Country code", help_text="For example US or BR.")

    def __init__(self, *args, league, **kwargs):
        super().__init__(*args, **kwargs)
        venue = league.home_venue
        if venue is not None and not self.is_bound:
            self.initial.update(
                venue_name=venue.name,
                address_line=venue.address_line,
                city=venue.city,
                country_code=venue.country_code,
            )
        last_match = league.matches.select_related("event").order_by("-event__starts_at").first()
        if last_match is not None and not self.is_bound:
            self.initial["timezone"] = last_match.event.timezone
            next_week = last_match.event.starts_at + timedelta(days=7)
            self.initial["first_kickoff"] = next_week.astimezone(ZoneInfo(last_match.event.timezone))

    def clean(self):
        cleaned_data = super().clean()
        name, kickoff = cleaned_data.get("timezone"), cleaned_data.get("first_kickoff")
        try:
            zone = ZoneInfo(name) if name else None
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            zone = None
            self.add_error("timezone", "Enter a valid timezone, such as America/New_York.")
        if zone and kickoff:
            local = kickoff.replace(tzinfo=None).replace(tzinfo=zone)
            if local.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                self.add_error("first_kickoff", "This local time does not exist in the selected timezone.")
            cleaned_data["first_kickoff"] = local
        if cleaned_data.get("country_code"):
            cleaned_data["country_code"] = cleaned_data["country_code"].upper()
        return cleaned_data
