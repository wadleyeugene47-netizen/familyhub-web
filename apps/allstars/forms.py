from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from apps.communities.forms import limit_community_field
from apps.hub.models import ChoiceOption

from .models import AllStarGame

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
TIME_FIELDS = ("starts_at", "registration_closes_at")


def choice_options(key):
    return list(
        ChoiceOption.objects.filter(choice_list__key=key, is_active=True).values_list("value", "label")
    )


def choice_labels(key):
    """Every label, hidden options included, so that past answers still display."""
    return dict(ChoiceOption.objects.filter(choice_list__key=key).values_list("value", "label"))


def _datetime_widget():
    return forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"})


class AllStarGameForm(forms.ModelForm):
    class Meta:
        model = AllStarGame
        fields = (
            "community",
            "title",
            "description",
            "starts_at",
            "registration_closes_at",
            "timezone",
            "venue",
            "team_count",
            "eligibility",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "starts_at": _datetime_widget(),
            "registration_closes_at": _datetime_widget(),
        }

    def __init__(self, *args, organizer, **kwargs):
        super().__init__(*args, **kwargs)
        limit_community_field(self, organizer, required=True)
        self.fields["eligibility"] = forms.ChoiceField(
            label="Who can play", choices=self._eligibility_choices()
        )
        self.fields["timezone"].help_text = "Times are entered in this timezone, such as America/New_York."
        for name in TIME_FIELDS:
            self.fields[name].input_formats = [DATETIME_FORMAT]
            value = getattr(self.instance, name)
            if self.instance.pk and value:
                self.initial[name] = value.astimezone(ZoneInfo(self.instance.timezone)).replace(tzinfo=None)

    def _eligibility_choices(self):
        choices = choice_options("player_eligibility")
        current = self.instance.eligibility
        if current not in dict(choices):
            choices.append((current, choice_labels("player_eligibility").get(current, current)))
        return choices

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean(self):
        cleaned_data = super().clean()
        timezone_name = cleaned_data.get("timezone")
        if not timezone_name:
            return cleaned_data
        try:
            zone = ZoneInfo(timezone_name)
        except (KeyError, ValueError, OSError, ZoneInfoNotFoundError):
            self.add_error("timezone", "Enter a valid IANA timezone, such as Europe/Lisbon.")
            return cleaned_data
        for name in TIME_FIELDS:
            value = cleaned_data.get(name)
            if value is None:
                continue
            local = value.replace(tzinfo=None).replace(tzinfo=zone)
            if local.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                self.add_error(name, "This local time does not exist in the selected timezone.")
            cleaned_data[name] = local
        starts_at, closes_at = cleaned_data.get("starts_at"), cleaned_data.get("registration_closes_at")
        if starts_at and closes_at and closes_at > starts_at:
            self.add_error("registration_closes_at", "Registration must close before kickoff.")
        return cleaned_data


class AllStarRegistrationForm(forms.Form):
    position = forms.ChoiceField()
    gender = forms.ChoiceField()
    note = forms.CharField(
        max_length=200,
        required=False,
        label="Note for the organizer",
        help_text="Optional: a friend to play with, a time you need to leave.",
    )

    def __init__(self, *args, game, **kwargs):
        self.game = game
        super().__init__(*args, **kwargs)
        self.fields["position"].choices = choice_options("player_position")
        self.fields["gender"].choices = choice_options("gender")

    def clean_gender(self):
        gender = self.cleaned_data["gender"]
        restricted_to = self.game.eligibility
        if restricted_to in choice_labels("gender") and gender != restricted_to:
            raise forms.ValidationError("This game is not open to your group.")
        return gender
