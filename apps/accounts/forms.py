from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import PreApprovedPhone, User, normalize_phone


class RegistrationForm(UserCreationForm):
    """Sign-up collects everything once; joining a community later never asks for these again."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email", "phone", "username")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "email", "phone"):
            self.fields[name].required = True
        self.fields["phone"].help_text = "Include the country code, for example +1 617 555 0100."
        self.fields["username"].help_text = "You can log in with this or your email."

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Log in instead.")
        return email


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Username or email"


class PreApprovedPhoneForm(forms.ModelForm):
    class Meta:
        model = PreApprovedPhone
        fields = ("phone", "name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].help_text = (
            "Include the country code, for example +1 617 555 0100. "
            "Whoever signs up with this number joins right away."
        )

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if PreApprovedPhone.objects.filter(phone_digits=normalize_phone(phone)).exists():
            raise forms.ValidationError("This number is already on your list.")
        return phone
