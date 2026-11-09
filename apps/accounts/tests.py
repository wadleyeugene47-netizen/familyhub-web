from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class AccountFlowTests(TestCase):
    def test_registration_creates_pending_user_and_logs_them_in(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "participant1",
                "first_name": "Pat",
                "last_name": "Player",
                "email": "participant@example.com",
                "phone": "+1 617 555 0100",
                "password1": "A-strong-test-password-2048",
                "password2": "A-strong-test-password-2048",
            },
        )

        self.assertRedirects(response, reverse("accounts:pending"))
        user = User.objects.get(username="participant1")
        self.assertEqual(user.approval_status, User.Approval.PENDING)
        self.assertIn("_auth_user_id", self.client.session)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:dashboard')}",
        )

    def test_registered_user_can_log_in(self):
        User.objects.create_user(username="participant1", password="a-strong-password")

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "participant1", "password": "a-strong-password"},
        )

        self.assertRedirects(response, reverse("accounts:dashboard"))


class OneAccountTests(TestCase):
    password = "A-strong-test-password-2048"

    def _register(self, **overrides):
        data = {
            "username": "newperson",
            "first_name": "New",
            "last_name": "Person",
            "email": "new@example.com",
            "phone": "+1 617 555 0100",
            "password1": self.password,
            "password2": self.password,
        }
        data.update(overrides)
        return self.client.post(reverse("accounts:register"), data)

    def test_signup_stores_name_phone_and_email(self):
        self._register()
        user = User.objects.get(username="newperson")
        self.assertEqual(
            (user.get_full_name(), user.phone, user.email),
            ("New Person", "+1 617 555 0100", "new@example.com"),
        )

    def test_phone_and_name_are_required(self):
        response = self._register(phone="", first_name="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newperson").exists())

    def test_invalid_phone_is_rejected(self):
        self._register(phone="call me")
        self.assertFalse(User.objects.filter(username="newperson").exists())

    def test_email_can_only_be_used_by_one_account(self):
        User.objects.create_user("first", "Same@Example.com", self.password)
        response = self._register(email="same@example.com")
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.filter(email__iexact="same@example.com").count(), 1)

    def test_login_with_email_or_username(self):
        User.objects.create_user("pat", "pat@example.com", self.password)
        for login in ("pat", "PAT@example.com"):
            response = self.client.post(reverse("accounts:login"), {"username": login, "password": self.password})
            self.assertRedirects(response, reverse("accounts:dashboard"), msg_prefix=login)
            self.client.logout()
