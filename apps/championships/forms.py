from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html

from apps.communities.forms import limit_community_field
from apps.events.models import Event, EventLocation

from .models import Championship, Match, MatchIncident, Team
from .scheduling import configuration_errors

DATETIME_WIDGET = forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})
STRUCTURAL_FIELDS = (
    "format",
    "team_count",
    "group_count",
    "advance_per_group",
    "group_legs",
    "third_place_match",
)
MAX_COVER_BYTES = 5 * 1024 * 1024


def _zone(name):
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, ZoneInfoNotFoundError, TypeError):
        return None


def localize(form, cleaned_data, field_names, zone):
    """Treat datetime-local input as wall-clock time in the championship timezone."""
    for field_name in field_names:
        value = cleaned_data.get(field_name)
        if value is None or zone is None:
            continue
        local_value = value.replace(tzinfo=None).replace(tzinfo=zone)
        round_trip = local_value.astimezone(datetime_timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) != value.replace(tzinfo=None):
            form.add_error(field_name, "This local time does not exist in the selected timezone.")
        cleaned_data[field_name] = local_value


class ChampionshipForm(forms.ModelForm):
    venue_name = forms.CharField(max_length=160, required=False, label="Venue name")
    address_line = forms.CharField(max_length=255, required=False)
    city = forms.CharField(max_length=100, required=False)
    region = forms.CharField(max_length=100, required=False)
    postal_code = forms.CharField(max_length=24, required=False)
    country_code = forms.CharField(max_length=2, min_length=2, required=False, label="Country code")
    latitude = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    longitude = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)

    SECTIONS = (
        ("Basics", "trophy", "Name, story and cover image.", ("name", "community", "description", "cover_image")),
        (
            "Format",
            "grid",
            "How teams are grouped and who advances.",
            ("format", "team_count", "group_count", "advance_per_group", "group_legs", "third_place_match"),
        ),
        (
            "Points and tie-breakers",
            "chart",
            "Calculated automatically from saved results.",
            ("points_for_win", "points_for_draw", "points_for_loss", "tie_breaker", "knockout_tie_rule"),
        ),
        (
            "Schedule",
            "calendar",
            "Used to generate kickoff times and fields.",
            (
                "first_kickoff_at",
                "timezone",
                "match_duration_minutes",
                "break_between_matches_minutes",
                "days_between_rounds",
                "field_names",
            ),
        ),
        (
            "Field location",
            "pin",
            "Add coordinates to show a map.",
            ("venue_name", "address_line", "city", "region", "postal_code", "country_code", "latitude", "longitude"),
        ),
        (
            "Registration and rules",
            "shield",
            "Entry requirements, optional entry fee and rules.",
            ("registration_deadline", "entry_requirements", "entry_event", "rules", "advancement_notes"),
        ),
    )

    class Meta:
        model = Championship
        fields = (
            "name",
            "community",
            "description",
            "cover_image",
            "format",
            "team_count",
            "group_count",
            "advance_per_group",
            "group_legs",
            "third_place_match",
            "points_for_win",
            "points_for_draw",
            "points_for_loss",
            "tie_breaker",
            "knockout_tie_rule",
            "first_kickoff_at",
            "timezone",
            "match_duration_minutes",
            "break_between_matches_minutes",
            "days_between_rounds",
            "field_names",
            "registration_deadline",
            "entry_requirements",
            "entry_event",
            "rules",
            "advancement_notes",
        )
        labels = {
            "team_count": "Number of teams",
            "group_count": "Number of groups",
            "advance_per_group": "Teams advancing per group",
            "group_legs": "Group meetings per pair",
            "third_place_match": "Play a third-place match",
            "knockout_tie_rule": "Level knockout matches",
            "first_kickoff_at": "First kickoff",
            "match_duration_minutes": "Match duration (minutes)",
            "break_between_matches_minutes": "Break between matches (minutes)",
            "field_names": "Fields / pitches",
            "entry_event": "Entry fee event",
            "advancement_notes": "Additional advancement rules",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "entry_requirements": forms.Textarea(attrs={"rows": 3}),
            "rules": forms.Textarea(attrs={"rows": 4}),
            "advancement_notes": forms.Textarea(attrs={"rows": 3}),
            "first_kickoff_at": DATETIME_WIDGET,
            "registration_deadline": DATETIME_WIDGET,
        }

    def __init__(self, *args, organizer, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.instance.organizer = organizer
        self.organizer = organizer
        limit_community_field(self, organizer)
        for field_name in ("first_kickoff_at", "registration_deadline"):
            self.fields[field_name].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["entry_event"].queryset = Event.objects.filter(
            organizer=organizer, status__in=[Event.Status.DRAFT, Event.Status.PUBLISHED]
        )
        self.fields["entry_event"].help_text = format_html(
            "Optional. Teams register and pay through this event using the existing checkout; "
            "its price is shown as the entry fee. No event to pick? "
            '<a href="{}" target="_blank" rel="noopener">Create a paid event</a> (opens in a new tab), '
            "then reload this page.",
            reverse("events:create"),
        )
        self.fields["cover_image"].widget.attrs["accept"] = "image/*"
        zone = _zone(self.instance.timezone)
        if self.instance.pk and zone:
            for field_name in ("first_kickoff_at", "registration_deadline"):
                value = getattr(self.instance, field_name)
                if value:
                    self.initial[field_name] = value.astimezone(zone).replace(tzinfo=None)
        if self.instance.location_id:
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
        self.locked = bool(self.instance.pk and self.instance.matches.exists())
        if self.locked:
            for field_name in STRUCTURAL_FIELDS:
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = "Locked while fixtures exist. Clear the fixtures to change it."

    def sections(self):
        for title, icon, blurb, names in self.SECTIONS:
            yield {"title": title, "icon": icon, "blurb": blurb, "fields": [self[name] for name in names]}

    def clean_cover_image(self):
        image = self.cleaned_data.get("cover_image")
        if image and getattr(image, "size", 0) > MAX_COVER_BYTES:
            raise forms.ValidationError("Cover images must be 5 MB or smaller.")
        return image

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = Championship.objects.filter(organizer=self.organizer, name__iexact=name)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("You already have a championship with this name.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        zone = _zone(cleaned_data.get("timezone"))
        if cleaned_data.get("timezone") and zone is None:
            self.add_error("timezone", "Enter a valid IANA timezone, such as Europe/Lisbon.")
        localize(self, cleaned_data, ("first_kickoff_at", "registration_deadline"), zone)
        deadline = cleaned_data.get("registration_deadline")
        kickoff = cleaned_data.get("first_kickoff_at")
        if deadline and kickoff and deadline > kickoff:
            self.add_error("registration_deadline", "Registration must close before the first kickoff.")

        venue_fields = ("venue_name", "address_line", "city", "region", "postal_code", "country_code", "latitude", "longitude")
        if any(cleaned_data.get(name) not in (None, "") for name in venue_fields):
            for name in ("address_line", "city", "country_code"):
                if not cleaned_data.get(name):
                    self.add_error(name, "Required when a field location is entered.")
        if (cleaned_data.get("latitude") is None) != (cleaned_data.get("longitude") is None):
            self.add_error("latitude", "Enter both coordinates, or leave both blank.")
            self.add_error("longitude", "Enter both coordinates, or leave both blank.")

        if not self.locked:
            draft = Championship(
                **{
                    name: cleaned_data.get(name, getattr(self.instance, name))
                    for name in STRUCTURAL_FIELDS + ("points_for_win", "points_for_draw")
                }
            )
            for error in configuration_errors(draft):
                self.add_error(None, error)
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        championship = super().save(commit=False)
        data = self.cleaned_data
        if data.get("address_line"):
            values = {
                "name": data["venue_name"],
                "address_line": data["address_line"],
                "city": data["city"],
                "region": data["region"],
                "postal_code": data["postal_code"],
                "country_code": data["country_code"].upper(),
                "latitude": data["latitude"],
                "longitude": data["longitude"],
            }
            location = championship.location
            shared = location is not None and (location.events.exists() or location.championships.exclude(pk=championship.pk).exists())
            if location is None or shared:
                championship.location = EventLocation.objects.create(**values)
            else:
                for field, value in values.items():
                    setattr(location, field, value)
                location.save()
        else:
            championship.location = None
        if commit:
            championship.save()
        return championship


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "short_name", "group", "seed", "lots_rank")
        labels = {"lots_rank": "Drawing of lots rank"}

    def __init__(self, *args, championship, **kwargs):
        super().__init__(*args, **kwargs)
        self.championship = championship
        self.instance.championship = championship
        self.fields["group"].queryset = championship.groups.all()
        self.fields["group"].help_text = "Leave empty to place the team automatically."
        if not championship.has_groups:
            del self.fields["group"]
        elif championship.matches.exists():
            self.fields["group"].disabled = True
            self.fields["group"].help_text = "Locked while fixtures exist."

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = self.championship.teams.filter(name__iexact=name)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("A team with this name is already registered.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        if self.instance._state.adding and self.championship.teams.count() >= self.championship.team_count:
            raise forms.ValidationError(
                f"All {self.championship.team_count} team places are filled. Increase the number of teams first."
            )
        return cleaned_data


class BulkTeamForm(forms.Form):
    names = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Harbor FC\nHill United\nRiverside"}),
        label="Team names",
        help_text="One team per line.",
    )

    def __init__(self, *args, championship, **kwargs):
        super().__init__(*args, **kwargs)
        self.championship = championship

    def clean_names(self):
        names = [line.strip() for line in self.cleaned_data["names"].splitlines() if line.strip()]
        if not names:
            raise forms.ValidationError("Enter at least one team name.")
        too_long = [name for name in names if len(name) > 120]
        if too_long:
            raise forms.ValidationError("Team names must be 120 characters or fewer.")
        lowered = [name.casefold() for name in names]
        if len(set(lowered)) != len(lowered):
            raise forms.ValidationError("The list contains the same team twice.")
        existing = {name.casefold() for name in self.championship.teams.values_list("name", flat=True)}
        clashes = [name for name in names if name.casefold() in existing]
        if clashes:
            raise forms.ValidationError(f"Already registered: {', '.join(clashes)}.")
        remaining = self.championship.team_count - len(existing)
        if len(names) > remaining:
            raise forms.ValidationError(f"Only {remaining} team place(s) remain.")
        return names


class ResultForm(forms.Form):
    home_score = forms.IntegerField(min_value=0, max_value=99)
    away_score = forms.IntegerField(min_value=0, max_value=99)
    extra_time = forms.BooleanField(required=False, label="Extra time played")
    home_penalties = forms.IntegerField(min_value=0, max_value=99, required=False)
    away_penalties = forms.IntegerField(min_value=0, max_value=99, required=False)
    winner_side = forms.ChoiceField(
        required=False,
        choices=(("", "—"), ("home", "Home team advances"), ("away", "Away team advances")),
        label="Organizer decision",
    )
    note = forms.CharField(max_length=255, required=False, label="Reason for change (optional)")
    confirm_reset = forms.BooleanField(required=False)

    def __init__(self, *args, match, **kwargs):
        super().__init__(*args, **kwargs)
        self.match = match
        if match.teams_known:
            self.fields["home_score"].label = f"{match.home_team.name} score"
            self.fields["away_score"].label = f"{match.away_team.name} score"
            self.fields["winner_side"].choices = (
                ("", "—"),
                ("home", f"{match.home_team.name} advance"),
                ("away", f"{match.away_team.name} advance"),
            )


class IncidentForm(forms.Form):
    side = forms.ChoiceField(choices=(("home", "Home"), ("away", "Away")), initial="home", label="Team")
    kind = forms.ChoiceField(choices=MatchIncident.Kind.choices, initial=MatchIncident.Kind.GOAL, label="Type")
    player_name = forms.CharField(max_length=120, required=False, label="Player")
    minute = forms.IntegerField(min_value=0, max_value=150, required=False)
    assist_name = forms.CharField(max_length=120, required=False, label="Assist")

    def __init__(self, *args, match=None, **kwargs):
        super().__init__(*args, **kwargs)
        if match is not None and match.teams_known:
            self.fields["side"].choices = (("home", match.home_team.name), ("away", match.away_team.name))


IncidentFormSet = forms.formset_factory(IncidentForm, extra=3, can_delete=True, max_num=40, validate_max=True)


def incident_initial(match):
    return [
        {
            "side": "home" if incident.team_id == match.home_team_id else "away",
            "kind": incident.kind,
            "player_name": incident.player_name,
            "minute": incident.minute,
            "assist_name": incident.assist_name,
        }
        for incident in match.incidents.all()
    ]


def incidents_from_formset(formset, match):
    incidents = []
    for form in formset.forms:
        data = getattr(form, "cleaned_data", None)
        if not data or data.get("DELETE"):
            continue
        # A row with neither a player nor a minute adds nothing to the score, so it is ignored.
        if not data.get("player_name") and data.get("minute") is None:
            continue
        incidents.append(
            {
                "team_id": match.home_team_id if data["side"] == "home" else match.away_team_id,
                "kind": data["kind"],
                "player_name": data.get("player_name", ""),
                "minute": data.get("minute"),
                "assist_name": data.get("assist_name", ""),
            }
        )
    return incidents


class MatchScheduleForm(forms.Form):
    kickoff_at = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"], widget=DATETIME_WIDGET, label="Kickoff")
    field_name = forms.CharField(max_length=80, required=False, label="Field")
    status = forms.ChoiceField()
    note = forms.CharField(max_length=255, required=False, label="Reason for change (optional)")
    confirm_reset = forms.BooleanField(required=False)

    def __init__(self, *args, match, **kwargs):
        super().__init__(*args, **kwargs)
        self.match = match
        statuses = [Match.Status.SCHEDULED, Match.Status.IN_PROGRESS, Match.Status.POSTPONED]
        if not match.is_knockout:
            statuses.append(Match.Status.CANCELLED)
        if match.is_completed:
            statuses = [Match.Status.COMPLETED]
        self.fields["status"].choices = [(status.value, status.label) for status in statuses]
        if not args or args[0] is None:
            self.initial.setdefault("status", match.status)
            self.initial.setdefault("field_name", match.field_name)
            if match.kickoff_at:
                self.initial.setdefault("kickoff_at", match.kickoff_at.astimezone(match.championship.zone).replace(tzinfo=None))

    def clean(self):
        cleaned_data = super().clean()
        localize(self, cleaned_data, ("kickoff_at",), self.match.championship.zone)
        return cleaned_data
