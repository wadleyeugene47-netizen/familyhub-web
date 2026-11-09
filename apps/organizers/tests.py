from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.hub.models import Feature

from .models import OrganizerProfile
from .services import create_or_refresh_onboarding_link


class OrganizerPaymentOnboardingTests(TestCase):
    def setUp(self):
        Feature.objects.filter(key="payments").update(enabled=True)
        self.user = get_user_model().objects.create_user(
            username="organizer",
            password="strong-password-123",
        )
        self.organizer = OrganizerProfile.objects.create(
            user=self.user,
            display_name="Payment Organizer",
        )

    def test_onboarding_page_requires_login(self):
        response = self.client.get(reverse("organizers:payment_onboarding"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('organizers:payment_onboarding')}",
        )

    @override_settings(STRIPE_SECRET_KEY="")
    def test_missing_secret_is_explained_without_external_request(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("organizers:payment_onboarding"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set STRIPE_SECRET_KEY")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake_for_unit_test")
    @patch("apps.organizers.views.create_or_refresh_onboarding_link")
    def test_successful_onboarding_redirects_to_stripe_hosted_flow(self, create_link):
        create_link.return_value = "https://connect.stripe.test/onboarding/session"
        self.client.force_login(self.user)

        response = self.client.post(reverse("organizers:payment_onboarding"))

        self.assertRedirects(
            response,
            "https://connect.stripe.test/onboarding/session",
            fetch_redirect_response=False,
        )
        create_link.assert_called_once_with(self.organizer)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_service_requires_stripe_secret_key(self):
        with self.assertRaises(ImproperlyConfigured):
            create_or_refresh_onboarding_link(self.organizer)
