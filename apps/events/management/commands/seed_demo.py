from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.organizers.models import OrganizerProfile

DEMO_USERNAME = "demo_organizer"
DEMO_TAG = "[Demo]"

CATEGORIES = [
    ("Music", "music", "Concerts, festivals and live sets."),
    ("Sports", "sports", "Tournaments, pickup games and races."),
    ("Workshops", "workshops", "Hands-on classes and skill sessions."),
    ("Food & Drink", "food-drink", "Tastings, markets and pop-up dinners."),
]

LOCATIONS = [
    ("Riverside Park", "100 River Rd", "Boston", "MA", "US"),
    ("Downtown Hall", "25 Main St", "New York", "NY", "US"),
    ("Community Center", "8 Oak Ave", "Miami", "FL", "US"),
]

# (title, category slug, location index, days from now, hours long, price, capacity, description)
EVENTS = [
    ("Summer Jazz Night", "music", 0, 5, 3, "25.00", 200,
     "An evening of live jazz by the river with local bands."),
    ("Indie Rock Showcase", "music", 1, 12, 4, "18.50", 150,
     "Five up-and-coming indie bands on one stage."),
    ("5K Fun Run", "sports", 0, 8, 2, "0", 300,
     "A relaxed 5K around the park. All paces welcome."),
    ("3v3 Basketball Tournament", "sports", 2, 15, 6, "40.00", 48,
     "Bring your team of three and compete for the trophy."),
    ("Intro to Pottery", "workshops", 2, 3, 2, "35.00", 12,
     "Learn the basics of wheel throwing. Materials included."),
    ("Photography Walk", "workshops", 1, 20, 3, "0", 25,
     "Explore the city with a pro photographer and sharpen your eye."),
    ("Street Food Market", "food-drink", 1, 6, 5, "0", 500,
     "Twenty vendors, live music and plenty of seating."),
    ("Wine Tasting Evening", "food-drink", 0, 25, 2, "45.00", 40,
     "Sample eight wines paired with small bites."),
]


class Command(BaseCommand):
    help = "Create sample categories and published events for local testing (use --clear to remove them)."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete the demo data instead.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            self.clear()
            return

        user, created = get_user_model().objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={"email": "demo_organizer@example.com"},
        )
        if created:
            user.set_unusable_password()
            user.save()
        organizer, _ = OrganizerProfile.objects.get_or_create(
            user=user,
            defaults={"display_name": "Demo Organizer"},
        )

        categories = {}
        for name, slug, description in CATEGORIES:
            categories[slug], _ = EventCategory.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "description": description},
            )

        locations = []
        for name, address, city, region, country in LOCATIONS:
            location, _ = EventLocation.objects.get_or_create(
                name=f"{name} {DEMO_TAG}",
                defaults={
                    "address_line": address,
                    "city": city,
                    "region": region,
                    "country_code": country,
                },
            )
            locations.append(location)

        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        created_count = 0
        for title, slug, loc, days, hours, price, capacity, description in EVENTS:
            starts_at = (now + timedelta(days=days)).replace(hour=18)
            _, created = Event.objects.get_or_create(
                organizer=organizer,
                title=title,
                defaults={
                    "category": categories[slug],
                    "location": locations[loc],
                    "description": description,
                    "starts_at": starts_at,
                    "ends_at": starts_at + timedelta(hours=hours),
                    "capacity": capacity,
                    "price": Decimal(price),
                    "status": Event.Status.PUBLISHED,
                },
            )
            created_count += created

        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({created_count} new events)."))

    def clear(self):
        user = get_user_model().objects.filter(username=DEMO_USERNAME).first()
        if user is None:
            self.stdout.write("No demo data found.")
            return
        events = Event.objects.filter(organizer__user=user)
        deleted = events.count()
        events.delete()
        EventLocation.objects.filter(name__endswith=DEMO_TAG, events__isnull=True).delete()
        EventCategory.objects.filter(
            slug__in=[slug for _, slug, _ in CATEGORIES],
            events__isnull=True,
        ).delete()
        OrganizerProfile.objects.filter(user=user).delete()
        user.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {deleted} demo events."))
