"""Warnings from `manage.py check --deploy` about things that break quietly for real users."""
from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=True)
def launch_settings(app_configs, **kwargs):
    problems = []
    if "console" in settings.EMAIL_BACKEND or "locmem" in settings.EMAIL_BACKEND:
        problems.append(
            Warning(
                "Emails are not really being sent.",
                hint="Set EMAIL_BACKEND to the SMTP backend. Password resets and approval emails depend on it.",
                id="hub.W001",
            )
        )
    if "example.com" in settings.DEFAULT_FROM_EMAIL:
        problems.append(
            Warning(
                "Emails would be sent from a placeholder address.",
                hint="Set DEFAULT_FROM_EMAIL to an address on a domain you have verified with your email provider.",
                id="hub.W002",
            )
        )
    if not settings.MEDIA_S3_BUCKET:
        problems.append(
            Warning(
                "Uploaded images have nowhere permanent to go.",
                hint="Set MEDIA_S3_BUCKET and its related settings, or uploaded images will not display.",
                id="hub.W003",
            )
        )
    return problems
