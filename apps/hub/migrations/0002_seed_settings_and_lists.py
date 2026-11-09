from django.db import migrations

# (key, name, description, [(value, label), ...]). The owner edits these from the dashboard afterwards.
STARTER_LISTS = [
    (
        "tournament_format",
        "Tournament formats",
        "Formats offered when an organizer creates a tournament.",
        [
            ("groups_knockout", "Group stage + knockout"),
            ("knockout", "Straight knockout"),
            ("groups", "Round robin"),
        ],
    ),
    (
        "player_eligibility",
        "Who can play",
        "Who may register for a tournament.",
        [("male", "Male only"), ("female", "Female only"), ("mixed", "Male and female")],
    ),
    ("gender", "Gender", "Asked where a tournament or All-Star Game needs it.", [("male", "Male"), ("female", "Female")]),
    (
        "player_position",
        "Positions",
        "Positions a player can pick.",
        [("goalkeeper", "Goalkeeper"), ("outfield", "Outfield player")],
    ),
]


def seed(apps, schema_editor):
    SiteSettings = apps.get_model("hub", "SiteSettings")
    ChoiceList = apps.get_model("hub", "ChoiceList")
    ChoiceOption = apps.get_model("hub", "ChoiceOption")
    SiteSettings.objects.get_or_create(pk=1)
    for key, name, description, options in STARTER_LISTS:
        choice_list, created = ChoiceList.objects.get_or_create(
            key=key, defaults={"name": name, "description": description}
        )
        if not created:
            continue
        for order, (value, label) in enumerate(options):
            ChoiceOption.objects.create(choice_list=choice_list, value=value, label=label, order=order)


class Migration(migrations.Migration):
    dependencies = [("hub", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
