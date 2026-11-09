from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventCategory, EventLocation
from apps.notifications.models import EmailNotification
from apps.organizers.models import OrganizerProfile

from .models import PreApprovedPhone

User = get_user_model()
PASSWORD = "A-strong-test-password-2048"


def register(client, **overrides):
    data = {
        "username": "newperson",
        "first_name": "New",
        "last_name": "Person",
        "email": "new@example.com",
        "phone": "+1 617 555 0100",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return client.post(reverse("accounts:register"), data)


class MemberApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", PASSWORD)

    def test_signup_waits_for_owner_and_owner_is_emailed(self):
        response = register(self.client)

        self.assertRedirects(response, reverse("accounts:pending"))
        member = User.objects.get(username="newperson")
        self.assertEqual(member.approval_status, User.Approval.PENDING)
        self.assertTrue(
            EmailNotification.objects.filter(
                recipient=self.owner, kind=EmailNotification.Kind.MEMBER_SIGNUP
            ).exists()
        )

    def test_pending_member_is_kept_on_waiting_page(self):
        register(self.client)

        for url in (reverse("accounts:dashboard"), reverse("events:list"), reverse("events:create")):
            self.assertRedirects(self.client.get(url), reverse("accounts:pending"))
        self.assertEqual(self.client.get(reverse("accounts:pending")).status_code, 200)

    def test_owner_accepts_member(self):
        register(self.client)
        member = User.objects.get(username="newperson")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("hub:dashboard")), "member waiting")
        self.assertContains(self.client.get(reverse("hub:members")), "New Person")

        self.client.post(reverse("hub:member_decide", args=[member.pk]), {"decision": "approve"})

        member.refresh_from_db()
        self.assertEqual(member.approval_status, User.Approval.APPROVED)
        self.assertTrue(
            EmailNotification.objects.filter(
                recipient=member, kind=EmailNotification.Kind.MEMBER_APPROVED
            ).exists()
        )
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("accounts:dashboard")).status_code, 200)

    def test_owner_declines_member(self):
        register(self.client)
        member = User.objects.get(username="newperson")
        self.client.force_login(self.owner)

        self.client.post(reverse("hub:member_decide", args=[member.pk]), {"decision": "reject"})

        member.refresh_from_db()
        self.assertEqual(member.approval_status, User.Approval.REJECTED)
        self.client.force_login(member)
        self.assertRedirects(self.client.get(reverse("accounts:dashboard")), reverse("accounts:pending"))

    def test_pre_approved_phone_joins_right_away(self):
        PreApprovedPhone.objects.create(phone="+1 (617) 555-0100", name="Cousin", added_by=self.owner)

        response = register(self.client, phone="16175550100")

        self.assertRedirects(response, reverse("accounts:dashboard"))
        member = User.objects.get(username="newperson")
        self.assertEqual(member.approval_status, User.Approval.APPROVED)
        self.assertEqual(member.pre_approval.name, "Cousin")
        self.assertFalse(EmailNotification.objects.filter(kind=EmailNotification.Kind.MEMBER_SIGNUP).exists())

    def test_pre_approved_phone_works_only_once(self):
        PreApprovedPhone.objects.create(phone="+1 617 555 0100")
        register(self.client)
        self.client.logout()

        register(self.client, username="second", email="second@example.com")

        self.assertEqual(User.objects.get(username="second").approval_status, User.Approval.PENDING)

    def test_owner_adds_and_removes_phone(self):
        self.client.force_login(self.owner)

        self.client.post(reverse("hub:phones"), {"phone": "+351 912 345 678", "name": "Ana"})
        entry = PreApprovedPhone.objects.get()
        self.assertEqual(entry.phone_digits, "351912345678")

        duplicate = self.client.post(reverse("hub:phones"), {"phone": "351 912345678", "name": ""})
        self.assertContains(duplicate, "already on your list")

        self.client.post(reverse("hub:phone_delete", args=[entry.pk]))
        self.assertFalse(PreApprovedPhone.objects.exists())

    def test_owner_pages_are_hidden_from_members(self):
        member = User.objects.create_user("member", password=PASSWORD)
        self.client.force_login(member)

        for name in ("hub:members", "hub:phones", "hub:event_review"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 404)

    def test_existing_style_accounts_are_approved(self):
        self.assertEqual(User.objects.create_user("scripted").approval_status, User.Approval.APPROVED)


class EventApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", PASSWORD)
        cls.member = User.objects.create_user("member", "member@example.com", PASSWORD)
        cls.category = EventCategory.objects.create(name="Picnic", slug="picnic")

    def event_data(self, **overrides):
        starts_at = timezone.localtime(timezone.now() + timedelta(days=10))
        data = {
            "title": "Family Picnic",
            "description": "Bring food.",
            "category": self.category.pk,
            "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (starts_at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "timezone": "UTC",
            "capacity": 20,
            "price": "0",
            "currency": "USD",
            "status": Event.Status.PUBLISHED,
            "venue_name": "",
            "address_line": "1 Park Road",
            "city": "Boston",
            "region": "",
            "postal_code": "",
            "country_code": "US",
            "latitude": "",
            "longitude": "",
        }
        data.update(overrides)
        return data

    def make_event(self, status):
        organizer, _ = OrganizerProfile.objects.get_or_create(user=self.member, defaults={"display_name": "Member"})
        location = EventLocation.objects.create(address_line="1 Park Road", city="Boston", country_code="US")
        starts_at = timezone.now() + timedelta(days=5)
        return Event.objects.create(
            organizer=organizer,
            category=self.category,
            location=location,
            title="Member Party",
            description="Fun.",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            capacity=10,
            price=Decimal("0"),
            status=status,
        )

    def test_member_event_waits_for_owner(self):
        self.client.force_login(self.member)

        self.client.post(reverse("events:create"), self.event_data())

        event = Event.objects.get()
        self.assertEqual(event.status, Event.Status.PENDING)
        self.assertTrue(
            EmailNotification.objects.filter(
                recipient=self.owner, kind=EmailNotification.Kind.EVENT_SUBMITTED
            ).exists()
        )
        self.client.logout()
        self.assertEqual(self.client.get(reverse("events:detail", args=[event.pk])).status_code, 404)

    def test_owner_event_publishes_directly(self):
        self.client.force_login(self.owner)

        self.client.post(reverse("events:create"), self.event_data())

        self.assertEqual(Event.objects.get().status, Event.Status.PUBLISHED)

    def test_owner_approves_event(self):
        event = self.make_event(Event.Status.PENDING)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("hub:event_review")), "Member Party")
        self.assertEqual(self.client.get(reverse("events:detail", args=[event.pk])).status_code, 200)

        self.client.post(reverse("hub:event_decide", args=[event.pk]), {"decision": "approve"})

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PUBLISHED)
        self.assertTrue(
            EmailNotification.objects.filter(
                recipient=self.member, kind=EmailNotification.Kind.EVENT_APPROVED
            ).exists()
        )

    def test_owner_declines_event_with_message_and_member_can_resubmit(self):
        event = self.make_event(Event.Status.PENDING)
        self.client.force_login(self.owner)

        self.client.post(
            reverse("hub:event_decide", args=[event.pk]), {"decision": "reject", "note": "Wrong date."}
        )

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.REJECTED)
        email = EmailNotification.objects.get(recipient=self.member, kind=EmailNotification.Kind.EVENT_REJECTED)
        self.assertIn("Wrong date.", email.body)

        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse("events:organizer_list")), "Wrong date.")
        self.client.post(reverse("events:update", args=[event.pk]), self.event_data(title="Member Party"))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PENDING)

    def test_owner_cancels_published_event(self):
        event = self.make_event(Event.Status.PUBLISHED)
        self.client.force_login(self.owner)

        self.client.post(reverse("hub:event_decide", args=[event.pk]), {"decision": "cancel", "note": "Storm."})

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CANCELLED)
        self.assertEqual(event.review_note, "Storm.")
        self.assertTrue(
            EmailNotification.objects.filter(
                recipient=self.member, kind=EmailNotification.Kind.EVENT_CANCELLED
            ).exists()
        )

    def test_member_cannot_approve_events(self):
        event = self.make_event(Event.Status.PENDING)
        self.client.force_login(self.member)

        response = self.client.post(reverse("hub:event_decide", args=[event.pk]), {"decision": "approve"})

        self.assertEqual(response.status_code, 404)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PENDING)
