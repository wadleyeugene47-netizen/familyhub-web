from django import template
from django.templatetags.static import static

from apps.events.themes import THEME_PHOTOS, theme_for

register = template.Library()


@register.filter
def event_theme(category):
    return theme_for(category)


@register.filter
def theme_photo(theme):
    """URL of the built-in photo for a theme key, or "" when the theme has none."""
    path = THEME_PHOTOS.get(theme)
    return static(path) if path else ""
