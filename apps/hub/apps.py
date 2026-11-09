from django.apps import AppConfig
from django.db.models.signals import post_migrate


class HubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hub"
    label = "hub"
    verbose_name = "Family Hub"

    def ready(self):
        from . import checks, registry  # noqa: F401 (importing checks registers them)

        registry.register_feature(
            "payments",
            "Payments",
            "Online payments through each community's own Stripe payout account.",
        )
        registry.register_feature(
            "platform_fee",
            "Platform fee",
            "Take the platform's percentage from every payment as it happens.",
        )
        registry.register_feature(
            "custom_domains",
            "Custom domains",
            "Let a community load on its own domain instead of a Family Hub address.",
        )
        post_migrate.connect(registry.sync_features, dispatch_uid="hub.sync_features")
