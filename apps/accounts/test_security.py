from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_TIME=0.25)
class LoginThrottlingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_user(
            username="login-target",
            password="Correct-password-8294",
        )

    def test_repeated_bad_password_attempts_lock_out_login(self):
        login_url = reverse("accounts:login")
        for attempt in range(2):
            response = self.client.post(
                login_url,
                {"username": "login-target", "password": f"incorrect-{attempt}"},
                REMOTE_ADDR="192.0.2.10",
            )
            self.assertEqual(response.status_code, 200)

        blocked_response = self.client.post(
            login_url,
            {"username": "login-target", "password": "incorrect-final"},
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(blocked_response.status_code, 429)
        correct_password_response = self.client.post(
            login_url,
            {"username": "login-target", "password": "Correct-password-8294"},
            REMOTE_ADDR="192.0.2.10",
        )
        self.assertEqual(correct_password_response.status_code, 429)

    def test_lockout_applies_to_username_even_from_another_ip(self):
        login_url = reverse("accounts:login")
        for attempt in range(2):
            self.client.post(
                login_url,
                {"username": "login-target", "password": f"incorrect-{attempt}"},
                REMOTE_ADDR="192.0.2.20",
            )
        blocked_response = self.client.post(
            login_url,
            {"username": "login-target", "password": "incorrect-final"},
            REMOTE_ADDR="192.0.2.20",
        )
        self.assertEqual(blocked_response.status_code, 429)

        response = self.client.post(
            login_url,
            {"username": "login-target", "password": "Correct-password-8294"},
            REMOTE_ADDR="192.0.2.21",
        )

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)
