from django import forms
from django.forms import inlineformset_factory

from .models import ChoiceList, ChoiceOption, SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ("site_name", "tagline", "default_fee_percent", "support_email", "terms_text", "privacy_text")


class ChoiceOptionForm(forms.ModelForm):
    class Meta:
        model = ChoiceOption
        fields = ("label", "value", "order", "is_active")
        labels = {"is_active": "Shown"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["value"].required = False
        if self.instance.pk:
            # Saved records refer to the value, so it never changes after creation.
            self.fields["value"].disabled = True
            self.fields["value"].help_text = ""

    def clean_value(self):
        from django.utils.text import slugify

        if self.instance.pk:
            return self.instance.value
        value = self.cleaned_data.get("value") or slugify(self.cleaned_data.get("label", "")).replace("-", "_")
        if not value:
            raise forms.ValidationError("Enter a label or a value.")
        return value


ChoiceOptionFormSet = inlineformset_factory(
    ChoiceList,
    ChoiceOption,
    form=ChoiceOptionForm,
    extra=2,
    can_delete=False,
)
