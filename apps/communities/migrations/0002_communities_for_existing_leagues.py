from django.db import migrations

from apps.communities.models import unique_slug


def create_communities(apps, schema_editor):
    """Leagues members can already see become communities, so their pages aren't empty on day one."""
    Community = apps.get_model("communities", "Community")
    League = apps.get_model("leagues", "League")
    for league in League.objects.filter(status__in=["active", "completed"], community__isnull=True):
        community, created = Community.objects.get_or_create(
            organizer_id=league.organizer_id,
            name=league.name,
            defaults={"slug": unique_slug(Community, league.name), "description": league.description},
        )
        league.community = community
        league.save(update_fields=["community"])


class Migration(migrations.Migration):
    dependencies = [
        ("communities", "0001_initial"),
        ("leagues", "0004_league_community"),
    ]

    operations = [migrations.RunPython(create_communities, migrations.RunPython.noop)]
