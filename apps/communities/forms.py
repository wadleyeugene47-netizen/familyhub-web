from django import forms
from django.db.models import Q

from .models import Community


def limit_community_field(form, organizer, *, required=False):
    """Offer only the organizer's own communities, so nothing can be attached to someone else's."""
    field = form.fields["community"]
    current = getattr(form.instance, "community_id", None)
    if organizer is None:
        field.queryset = Community.objects.none()
    else:
        field.queryset = Community.objects.filter(
            Q(organizer=organizer, is_active=True) | Q(pk=current)
        )
    field.required = required
    field.empty_label = "Pick a community" if required else "No community"
    if not required:
        field.help_text = "Optional. Shows this on the community's page."


class CommunityForm(forms.ModelForm):
    class Meta:
        model = Community
        fields = ("name", "tagline", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, organizer, **kwargs):
        self.organizer = organizer
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Community.objects.filter(organizer=self.organizer, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have a community with this name.")
        return name
