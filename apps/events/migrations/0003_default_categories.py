from django.db import migrations

# One starter category per homepage theme (see apps/events/themes.py). Admins can rename or add more.
DEFAULT_CATEGORIES = [
    ("Soccer & Sports", "soccer-sports", "Leagues, pickup games and tournaments."),
    ("Parties", "parties", "Birthdays, nights out and celebrations."),
    ("Picnics & Outdoors", "picnics-outdoors", "Picnics, hikes and days in the sun."),
    ("Social Gatherings", "social-gatherings", "Meetups, dinners and game nights."),
    ("Community Events", "community-events", "Volunteering, markets and local causes."),
    ("Other Experiences", "other-experiences", "Everything else worth showing up for."),
]


def create_default_categories(apps, schema_editor):
    EventCategory = apps.get_model("events", "EventCategory")
    for name, slug, description in DEFAULT_CATEGORIES:
        if EventCategory.objects.filter(slug=slug).exists() or EventCategory.objects.filter(name=name).exists():
            continue
        EventCategory.objects.create(name=name, slug=slug, description=description)


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_eventcancellationpolicy_eventimage"),
    ]

    operations = [
        migrations.RunPython(create_default_categories, migrations.RunPython.noop),
    ]
