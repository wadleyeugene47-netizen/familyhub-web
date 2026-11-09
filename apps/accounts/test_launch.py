import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.hub.checks import launch_settings
from apps.hub.models import SiteSettings

User = get_user_model()
PASSWORD = "Correct-password-8294"
NEW_PASSWORD = "Brand-new-password-5512"


def fail_login(client, username, times, ip="192.0.2.10"):
    response = None
    for attempt in range(times):
        response = client.post(
            reverse("accounts:login"),
            {"username": username, "password": f"wrong-{attempt}"},
            REMOTE_ADDR=ip,
        )
    return response


@override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_TIME=0.25)
class PasswordResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", PASSWORD, first_name="Ana")

    def reset_link(self):
        match = re.search(r"http\S+/password/reset/\S+", mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(0)

    def request_reset(self, email="ana@example.com"):
        return self.client.post(reverse("accounts:password_reset"), {"email": email}, follow=True)

    def choose_password(self, link):
        page = self.client.get(link, follow=True)
        return self.client.post(
            page.redirect_chain[-1][0],
            {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
            follow=True,
        )

    def test_full_reset_lets_the_new_password_in(self):
        self.request_reset()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Reset your", mail.outbox[0].subject)
        self.choose_password(self.reset_link())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_login_page_links_to_reset(self):
        self.assertContains(self.client.get(reverse("accounts:login")), reverse("accounts:password_reset"))

    def test_unknown_email_looks_the_same_and_sends_nothing(self):
        known = self.request_reset().content.decode()
        self.assertIn("If that email has an account", known)
        mail.outbox.clear()
        unknown = self.request_reset("nobody@example.com")
        self.assertContains(unknown, "If that email has an account")
        self.assertEqual(mail.outbox, [])

    def test_link_only_works_once(self):
        self.request_reset()
        link = self.reset_link()
        self.choose_password(link)
        self.assertContains(self.client.get(link, follow=True), "This link doesn't work")

    def test_reset_ends_a_lockout(self):
        self.assertEqual(fail_login(self.client, "ana", 3).status_code, 429)
        self.request_reset()
        self.choose_password(self.reset_link())
        response = self.client.post(
            reverse("accounts:login"), {"username": "ana", "password": NEW_PASSWORD}, REMOTE_ADDR="192.0.2.10"
        )
        self.assertEqual(response.status_code, 302)

    def test_member_waiting_for_approval_can_still_reset(self):
        waiting = User.objects.create_user(
            "wait", "wait@example.com", PASSWORD, approval_status=User.Approval.PENDING
        )
        self.client.force_login(waiting)
        self.assertEqual(self.client.get(reverse("accounts:password_reset")).status_code, 200)

    def test_change_password_needs_the_old_one(self):
        self.client.force_login(self.user)
        url = reverse("accounts:password_change")
        self.client.post(url, {"old_password": "nope", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password(NEW_PASSWORD))
        self.client.post(url, {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertEqual(self.client.get(reverse("accounts:dashboard")).status_code, 200)


@override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_TIME=0.25)
class LockoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", PASSWORD)
        cls.bruno = User.objects.create_user("bruno", "bruno@example.com", PASSWORD)

    def test_one_persons_bad_passwords_never_lock_out_someone_else_on_the_same_address(self):
        fail_login(self.client, "ana", 3)
        response = self.client.post(
            reverse("accounts:login"), {"username": "bruno", "password": PASSWORD}, REMOTE_ADDR="192.0.2.10"
        )
        self.assertEqual(response.status_code, 302)

    def test_lockout_page_offers_a_way_back(self):
        response = fail_login(self.client, "ana", 3)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, reverse("accounts:password_reset"), status_code=429)


class ErrorAndLegalPageTests(TestCase):
    def test_missing_page_is_friendly(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "We can't find that page", status_code=404)

    def test_legal_pages_stay_hidden_until_filled_in(self):
        self.assertEqual(self.client.get(reverse("terms")).status_code, 404)
        self.assertEqual(self.client.get(reverse("privacy")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("accounts:register")), reverse("terms"))

    def test_legal_text_shows_escaped_and_is_linked(self):
        settings = SiteSettings.load()
        settings.terms_text = "Be kind.\n\n<script>alert(1)</script>"
        settings.save()
        page = self.client.get(reverse("terms"))
        self.assertContains(page, "Be kind.")
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(self.client.get(reverse("accounts:register")), reverse("terms"))


class DeployCheckTests(TestCase):
    def ids(self):
        return {problem.id for problem in launch_settings(None)}

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        DEFAULT_FROM_EMAIL="Events <noreply@example.com>",
        MEDIA_S3_BUCKET="",
    )
    def test_warns_about_unfinished_launch_settings(self):
        self.assertEqual(self.ids(), {"hub.W001", "hub.W002", "hub.W003"})

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        DEFAULT_FROM_EMAIL="Family Hub <hello@familyhub.test>",
        MEDIA_S3_BUCKET="uploads",
    )
    def test_quiet_when_configured(self):
        self.assertEqual(self.ids(), set())
