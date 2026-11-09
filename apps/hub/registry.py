"""Registry of switchable features.

Any module (a Django app) can declare a feature from its AppConfig.ready():

    from apps.hub.registry import register_feature
    register_feature("my_feature", "My feature", "What it does.")

After `migrate` the feature appears, switched off, in the owner dashboard. Code then checks it with
apps.hub.features.is_enabled("my_feature"). Nothing in the core has to change to add one.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    name: str
    description: str
    default_enabled: bool = False


_FEATURES: dict[str, FeatureSpec] = {}


def register_feature(key, name, description="", *, default_enabled=False):
    _FEATURES[key] = FeatureSpec(key, name, description, default_enabled)


def registered_features():
    return list(_FEATURES.values())


def sync_features(**kwargs):
    """Create a switch row for every registered feature that has none. Existing rows keep their state."""
    from .models import Feature

    for spec in _FEATURES.values():
        Feature.objects.get_or_create(
            key=spec.key,
            defaults={"name": spec.name, "description": spec.description, "enabled": spec.default_enabled},
        )
