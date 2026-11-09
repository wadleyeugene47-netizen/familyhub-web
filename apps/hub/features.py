from .models import Feature


def is_enabled(key):
    """True when the owner has switched the feature on. Unknown keys count as off."""
    return Feature.objects.filter(key=key, enabled=True).exists()


def enabled_keys():
    return set(Feature.objects.filter(enabled=True).values_list("key", flat=True))


def choices(list_key):
    """(value, label) pairs of the active options of a choice list, for use as form field choices."""
    from .models import ChoiceOption

    return list(
        ChoiceOption.objects.filter(choice_list__key=list_key, is_active=True).values_list("value", "label")
    )
