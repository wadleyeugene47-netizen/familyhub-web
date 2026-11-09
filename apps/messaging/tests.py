from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Conversation, Message
from .services import host_unread_count, member_unread_count

User = get_user_model()
PASSWORD = "A-strong-test-password-2048"
JSON = {"HTTP_ACCEPT": "application/json"}


class ChatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_superuser("host", "host@example.com", PASSWORD)
        cls.member = User.objects.create_user("member", "member@example.com", PASSWORD, first_name="Mia")
        cls.other = User.objects.create_user("other", "other@example.com", PASSWORD)

    def test_member_writes_and_host_sees_unread(self):
        self.client.force_login(self.member)
        self.client.post(reverse("messaging:chat"), {"body": "Hello host!"})

        self.assertEqual(Message.objects.get().from_host, False)
        self.assertEqual(host_unread_count(), 1)

        self.client.force_login(self.host)
        self.assertContains(self.client.get(reverse("hub:dashboard")), "Mia")
        response = self.client.get(reverse("hub:message_thread", args=[self.member.pk]))
        self.assertContains(response, "Hello host!")
        self.assertEqual(host_unread_count(), 0)

    def test_host_replies_and_member_gets_it_by_polling(self):
        self.client.force_login(self.host)
        self.client.post(reverse("hub:message_thread", args=[self.member.pk]), {"body": "Welcome!"}, **JSON)
        self.assertEqual(member_unread_count(self.member), 1)

        self.client.force_login(self.member)
        data = self.client.get(reverse("messaging:poll"), {"after": 0}).json()

        self.assertEqual([m["body"] for m in data["messages"]], ["Welcome!"])
        self.assertTrue(data["messages"][0]["from_host"])
        self.assertFalse(data["messages"][0]["mine"])
        self.assertEqual(member_unread_count(self.member), 0)
        last = data["messages"][0]["id"]
        self.assertEqual(self.client.get(reverse("messaging:poll"), {"after": last}).json()["messages"], [])

    def test_members_only_see_their_own_chat(self):
        self.client.force_login(self.host)
        self.client.post(reverse("hub:message_thread", args=[self.member.pk]), {"body": "Private note"})

        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("messaging:chat")), "Private note")
        self.assertEqual(self.client.get(reverse("messaging:poll")).json()["messages"], [])

    def test_members_cannot_open_host_inbox(self):
        self.client.force_login(self.member)

        for url in (reverse("hub:messages"), reverse("hub:message_thread", args=[self.other.pk])):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.post(reverse("hub:message_thread", args=[self.other.pk]), {"body": "sneaky"})
        self.assertFalse(Message.objects.exists())

    def test_empty_message_is_refused(self):
        self.client.force_login(self.member)

        response = self.client.post(reverse("messaging:chat"), {"body": "   "}, **JSON)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Conversation.objects.exists())

    def test_message_text_is_escaped(self):
        self.client.force_login(self.member)
        self.client.post(reverse("messaging:chat"), {"body": "<script>alert(1)</script>"})

        self.client.force_login(self.host)
        response = self.client.get(reverse("hub:message_thread", args=[self.member.pk]))

        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_waiting_member_can_message_host_but_declined_cannot(self):
        waiting = User.objects.create_user("waiting", password=PASSWORD, approval_status=User.Approval.PENDING)
        declined = User.objects.create_user("declined", password=PASSWORD, approval_status=User.Approval.REJECTED)

        self.client.force_login(waiting)
        self.assertEqual(self.client.get(reverse("messaging:chat")).status_code, 200)

        self.client.force_login(declined)
        self.assertRedirects(self.client.get(reverse("messaging:chat")), reverse("accounts:pending"))


class HostPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_superuser("host", "host@example.com", PASSWORD)
        cls.member = User.objects.create_user("member", "member@example.com", PASSWORD)

    def test_host_lands_in_panel_after_login(self):
        response = self.client.post(reverse("accounts:login"), {"username": "host", "password": PASSWORD})

        self.assertRedirects(response, reverse("hub:dashboard"))

    def test_member_lands_on_their_dashboard_after_login(self):
        response = self.client.post(reverse("accounts:login"), {"username": "member", "password": PASSWORD})

        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_panel_pages_use_the_host_layout(self):
        self.client.force_login(self.host)

        for name in ("hub:dashboard", "hub:members", "hub:event_review", "hub:messages", "hub:phones", "hub:settings"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "panel-sidebar")

    def test_member_pages_do_not_use_the_host_layout(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("messaging:chat"))

        self.assertNotContains(response, "panel-sidebar")
        self.assertEqual(self.client.get(reverse("hub:dashboard")).status_code, 404)
