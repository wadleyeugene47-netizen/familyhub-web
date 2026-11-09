from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


class SiteSettings(models.Model):
    """Platform-wide settings the owner edits from the dashboard. There is only ever one row."""

    site_name = models.CharField(max_length=80, default="Family Hub")
    tagline = models.CharField(max_length=160, default="One account for every community you belong to.")
    default_fee_percent = models.DecimalField(
        "default platform fee (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("3.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99.99"))],
        help_text="Taken from each payment when the platform fee is switched on. Communities can override it.",
    )
    support_email = models.EmailField(blank=True)
    terms_text = models.TextField(
        "terms of service",
        blank=True,
        help_text="Paste your reviewed text. The page stays hidden until this has text.",
    )
    privacy_text = models.TextField(
        "privacy policy",
        blank=True,
        help_text="Paste your reviewed text. The page stays hidden until this has text.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class Feature(models.Model):
    """An on/off switch for a feature that is built but may not be live yet."""

    key = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=False, help_text="On for every community.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({'on' if self.enabled else 'off'})"


class ChoiceList(models.Model):
    """A named list of options used by forms, e.g. tournament formats. The owner adds options without code."""

    key = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def active_options(self):
        return self.options.filter(is_active=True)


class ChoiceOption(models.Model):
    choice_list = models.ForeignKey(ChoiceList, on_delete=models.CASCADE, related_name="options")
    value = models.SlugField(
        max_length=60,
        help_text="Stored in the database. Keep it stable once used; change the label instead.",
    )
    label = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "label")
        constraints = [
            models.UniqueConstraint(fields=("choice_list", "value"), name="hub_unique_option_value_per_list"),
        ]

    def __str__(self):
        return self.label


phone_validator = RegexValidator(
    r"^\+?[0-9][0-9 ().-]{5,24}$",
    "Enter a phone number using digits, spaces and an optional leading +.",
)
