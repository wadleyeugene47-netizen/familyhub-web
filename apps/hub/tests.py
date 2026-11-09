from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.hub import registry
from apps.hub.features import choices, is_enabled
from apps.hub.models import ChoiceList, ChoiceOption, Feature, SiteSettings

User = get_user_model()


class FeatureSwitchTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pass-12345-strong")
        self.member = User.objects.create_user("member", "member@example.com", "pass-12345-strong")

    def test_launch_features_exist_and_start_switched_off(self):
        for key in ("payments", "platform_fee", "custom_domains"):
            self.assertTrue(Feature.objects.filter(key=key).exists(), key)
            self.assertFalse(is_enabled(key), key)

    def test_unknown_feature_counts_as_off(self):
        self.assertFalse(is_enabled("does_not_exist"))

    def test_registered_module_feature_is_synced_without_touching_existing_state(self):
        Feature.objects.filter(key="payments").update(enabled=True)
        registry.register_feature("test_module", "Test module", "From a plugin.")
        try:
            registry.sync_features()
            self.assertFalse(is_enabled("test_module"))
            self.assertTrue(Feature.objects.filter(key="test_module").exists())
            self.assertTrue(is_enabled("payments"))
        finally:
            registry._FEATURES.pop("test_module")

    def test_owner_switches_feature_on_and_off(self):
        self.client.force_login(self.owner)
        url = reverse("hub:toggle_feature", args=["payments"])
        self.client.post(url, {"enabled": "on"})
        self.assertTrue(is_enabled("payments"))
        self.client.post(url, {})
        self.assertFalse(is_enabled("payments"))

    def test_non_owner_cannot_see_or_use_dashboard(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("hub:dashboard")).status_code, 404)
        response = self.client.post(reverse("hub:toggle_feature", args=["payments"]), {"enabled": "on"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(is_enabled("payments"))

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("hub:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_owner_dashboard_lists_features_and_lists(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("hub:dashboard"))
        self.assertContains(response, "Custom domains")
        self.assertContains(response, "Tournament formats")

    def test_payment_setup_is_hidden_until_payments_are_switched_on(self):
        from apps.organizers.models import OrganizerProfile

        OrganizerProfile.objects.create(user=self.member, display_name="Member FC")
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("organizers:payment_onboarding")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("accounts:dashboard")), "Payment setup")
        Feature.objects.filter(key="payments").update(enabled=True)
        self.assertEqual(self.client.get(reverse("organizers:payment_onboarding")).status_code, 200)


class SiteSettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pass-12345-strong")
        self.client.force_login(self.owner)

    def test_defaults(self):
        settings = SiteSettings.load()
        self.assertEqual(settings.site_name, "Family Hub")
        self.assertEqual(settings.default_fee_percent, Decimal("3.00"))

    def test_only_one_settings_row(self):
        SiteSettings(site_name="Other").save()
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.load().site_name, "Other")

    def test_renaming_the_site_changes_every_page(self):
        response = self.client.post(
            reverse("hub:settings"),
            {"site_name": "Brothers Hub", "tagline": "Hello", "default_fee_percent": "2.5", "support_email": ""},
        )
        self.assertRedirects(response, reverse("hub:dashboard"))
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Brothers Hub")
        self.assertNotContains(page, "Family Hub")
        self.assertEqual(SiteSettings.load().default_fee_percent, Decimal("2.5"))

    def test_fee_cannot_be_negative(self):
        response = self.client.post(
            reverse("hub:settings"),
            {"site_name": "Family Hub", "tagline": "", "default_fee_percent": "-1", "support_email": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSettings.load().default_fee_percent, Decimal("3.00"))


class ChoiceListTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pass-12345-strong")
        self.client.force_login(self.owner)

    def test_starter_lists_are_seeded(self):
        self.assertEqual(
            [value for value, _ in choices("tournament_format")], ["groups_knockout", "knockout", "groups"]
        )
        self.assertEqual(dict(choices("player_eligibility"))["mixed"], "Male and female")
        self.assertIn("goalkeeper", dict(choices("player_position")))

    def _post(self, choice_list, rows):
        data = {
            "options-TOTAL_FORMS": str(len(rows)),
            "options-INITIAL_FORMS": str(sum(1 for row in rows if row.get("id"))),
            "options-MIN_NUM_FORMS": "0",
            "options-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            for name, value in row.items():
                data[f"options-{i}-{name}"] = value
        return self.client.post(reverse("hub:choice_list", args=[choice_list.key]), data)

    def test_owner_adds_renames_and_hides_options_without_code(self):
        positions = ChoiceList.objects.get(key="player_position")
        keeper, outfield = positions.options.order_by("order")
        response = self._post(
            positions,
            [
                {"id": keeper.pk, "label": "Keeper", "value": "changed", "order": "0", "is_active": "on"},
                {"id": outfield.pk, "label": "Outfield player", "order": "1"},
                {"label": "Defender", "value": "", "order": "2", "is_active": "on"},
            ],
        )
        self.assertRedirects(response, reverse("hub:choice_list", args=["player_position"]))
        self.assertEqual(choices("player_position"), [("goalkeeper", "Keeper"), ("defender", "Defender")])
        self.assertTrue(ChoiceOption.objects.filter(value="outfield", is_active=False).exists())
