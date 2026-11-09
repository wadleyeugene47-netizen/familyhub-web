from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.leagues.models import League
from apps.organizers.models import OrganizerProfile

from .models import Community

User = get_user_model()


class CommunityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", "owner@example.com", "x-Strong-pass-1")
        cls.stranger = User.objects.create_user("stranger", "stranger@example.com", "x-Strong-pass-1")
        cls.profile = OrganizerProfile.objects.create(user=cls.owner, display_name="Owner")
        cls.stranger_profile = OrganizerProfile.objects.create(user=cls.stranger, display_name="Stranger")
        cls.community = Community.objects.create(organizer=cls.profile, name="Brothers and Goals")

    def test_page_is_public_and_hides_drafts(self):
        League.objects.create(organizer=self.profile, community=self.community, name="Live", status="active")
        League.objects.create(organizer=self.profile, community=self.community, name="Secret", status="draft")
        page = self.client.get(reverse("communities:detail", args=[self.community.slug]))
        self.assertContains(page, "Live")
        self.assertNotContains(page, "Secret")

    def test_list_is_public(self):
        self.assertContains(self.client.get(reverse("communities:list")), "Brothers and Goals")

    def test_inactive_community_is_gone(self):
        Community.objects.filter(pk=self.community.pk).update(is_active=False)
        self.assertEqual(self.client.get(reverse("communities:detail", args=[self.community.slug])).status_code, 404)

    def test_any_member_can_start_one(self):
        self.client.force_login(self.stranger)
        self.client.post(reverse("communities:create"), {"name": "New Crew"})
        self.assertEqual(Community.objects.get(name="New Crew").organizer, self.stranger_profile)

    def test_slug_never_collides_with_the_create_page(self):
        community = Community.objects.create(organizer=self.profile, name="Create")
        self.assertNotEqual(community.slug, "create")

    def test_same_name_gets_a_different_address(self):
        other = Community.objects.create(organizer=self.stranger_profile, name="Brothers and Goals")
        self.assertNotEqual(other.slug, self.community.slug)

    def test_dashboard_is_only_for_its_organizer_and_the_host(self):
        url = reverse("communities:manage", args=[self.community.slug])
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("communities:update", args=[self.community.slug])).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        host = User.objects.create_superuser("host", "host@example.com", "x-Strong-pass-1")
        self.client.force_login(host)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_dashboard_shows_leagues_and_waiting_players(self):
        league = League.objects.create(organizer=self.profile, community=self.community, name="Live", status="active")
        league.join_requests.create(user=self.stranger)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("communities:manage", args=[self.community.slug]))
        self.assertContains(page, "Live")
        self.assertContains(page, "1 want to play")

    def test_league_cannot_join_someone_elses_community(self):
        self.client.force_login(self.stranger)
        self.client.post(
            reverse("leagues:create"),
            {"name": "Sneaky", "community": self.community.pk, "format": "round_robin"},
        )
        self.assertFalse(League.objects.filter(name="Sneaky").exists())
        self.client.post(reverse("leagues:create"), {"name": "Plain", "format": "round_robin"})
        self.assertIsNone(League.objects.get(name="Plain").community)
