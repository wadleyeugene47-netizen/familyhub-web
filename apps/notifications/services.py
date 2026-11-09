from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import EmailNotification


def queue_email(*, recipient, kind, dedupe_key, subject, body, registration=None, payment=None):
    notification, created = EmailNotification.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "recipient": recipient,
            "registration": registration,
            "payment": payment,
            "kind": kind,
            "subject": subject,
            "body": body,
        },
    )
    if created:
        transaction.on_commit(lambda notification_id=notification.pk: deliver_email(notification_id))
    return notification


def deliver_email(notification_id):
    with transaction.atomic():
        notification = EmailNotification.objects.select_for_update().get(pk=notification_id)
        if notification.status == EmailNotification.Status.SENT:
            return True
        if notification.status == EmailNotification.Status.SENDING:
            return False
        notification.status = EmailNotification.Status.SENDING
        notification.attempts += 1
        notification.last_error = ""
        notification.save(update_fields=("status", "attempts", "last_error", "updated_at"))
        recipient_email = notification.recipient.email
        subject = notification.subject
        body = notification.body

    try:
        if not recipient_email:
            raise ValueError("Recipient account has no email address.")
        sent_count = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
        if sent_count != 1:
            raise RuntimeError("Email backend did not confirm delivery.")
    except Exception as error:
        EmailNotification.objects.filter(pk=notification_id).update(
            status=EmailNotification.Status.FAILED,
            last_error=str(error)[:500],
            updated_at=timezone.now(),
        )
        return False

    EmailNotification.objects.filter(pk=notification_id).update(
        status=EmailNotification.Status.SENT,
        sent_at=timezone.now(),
        last_error="",
        updated_at=timezone.now(),
    )
    return True


def queue_registration_email(registration):
    event = registration.event
    if registration.status == registration.Status.CONFIRMED:
        kind = EmailNotification.Kind.REGISTRATION_CONFIRMED
        subject = f"Registration confirmed: {event.title}"
        body = (
            f"Your registration for {event.title} is confirmed.\n\n"
            f"Starts: {event.starts_at:%Y-%m-%d %H:%M} {event.timezone}\n"
            f"Location: {event.location.address_line}, {event.location.city}, "
            f"{event.location.country_code}\n"
        )
        suffix = "confirmed"
    else:
        kind = EmailNotification.Kind.PAYMENT_PENDING
        subject = f"Complete payment for {event.title}"
        body = (
            f"Your place for {event.title} is reserved pending payment.\n"
            f"Amount: {event.price} {event.currency}\n"
            "Sign in to your account to continue to secure checkout.\n"
        )
        suffix = "payment-pending"
    return queue_email(
        recipient=registration.participant,
        registration=registration,
        kind=kind,
        dedupe_key=f"registration:{registration.pk}:{suffix}",
        subject=subject,
        body=body,
    )


def queue_payment_email(payment, *, succeeded):
    registration = payment.registration
    event = registration.event
    if succeeded:
        kind = EmailNotification.Kind.PAYMENT_CONFIRMED
        suffix = "confirmed"
        subject = f"Payment confirmed: {event.title}"
        body = (
            f"Your payment for {event.title} is confirmed.\n"
            f"Amount: {payment.amount} {payment.currency}\n"
            f"Organizer: {payment.organizer.display_name}\n"
        )
    else:
        kind = EmailNotification.Kind.PAYMENT_FAILED
        suffix = "failed"
        subject = f"Payment update: {event.title}"
        body = (
            f"Payment for {event.title} did not complete. Your place is not confirmed.\n"
            "Visit the event page to check your registration and retry if available.\n"
        )
    return queue_email(
        recipient=registration.participant,
        registration=registration,
        payment=payment,
        kind=kind,
        dedupe_key=f"payment:{payment.pk}:{suffix}",
        subject=subject,
        body=body,
    )


def queue_refund_email(refund):
    payment = refund.payment
    registration = payment.registration
    status = refund.get_status_display().lower()
    return queue_email(
        recipient=registration.participant,
        registration=registration,
        payment=payment,
        kind=EmailNotification.Kind.REFUND_UPDATED,
        dedupe_key=f"refund:{refund.pk}:{refund.status}",
        subject=f"Refund update: {registration.event.title}",
        body=(
            f"Refund status for {registration.event.title}: {status}.\n"
            f"Amount: {refund.amount} {refund.currency}\n"
        ),
    )


def queue_event_update_emails(event, *, cancelled=False):
    from apps.registrations.models import Registration

    active_registrations = Registration.objects.filter(
        event=event,
        status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING_PAYMENT],
    ).select_related("participant")
    kind = (
        EmailNotification.Kind.EVENT_CANCELLED
        if cancelled
        else EmailNotification.Kind.EVENT_UPDATED
    )
    for registration in active_registrations:
        subject = f"Event cancelled: {event.title}" if cancelled else f"Event updated: {event.title}"
        body = (
            f"The event {event.title} has been cancelled by its organizer.\n"
            "Any payment remains recorded; this notice does not itself issue a refund. "
            "Review the event cancellation policy or contact the organizer about payment resolution.\n"
            if cancelled
            else (
                f"The event {event.title} has been updated.\n"
                f"Starts: {event.starts_at:%Y-%m-%d %H:%M} {event.timezone}\n"
                f"Location: {event.location.address_line}, {event.location.city}, "
                f"{event.location.country_code}\n"
            )
        )
        queue_email(
            recipient=registration.participant,
            registration=registration,
            kind=kind,
            dedupe_key=f"event:{event.pk}:update:{event.updated_at.isoformat()}:registration:{registration.pk}",
            subject=subject,
            body=body,
        )


def queue_event_reminder(registration):
    event = registration.event
    return queue_email(
        recipient=registration.participant,
        registration=registration,
        kind=EmailNotification.Kind.EVENT_REMINDER,
        dedupe_key=f"event-reminder-24h:{registration.pk}",
        subject=f"Reminder: {event.title} is coming up",
        body=(
            f"Reminder: {event.title} starts in about 24 hours.\n"
            f"Starts: {event.starts_at:%Y-%m-%d %H:%M} {event.timezone}\n"
            f"Location: {event.location.address_line}, {event.location.city}, "
            f"{event.location.country_code}\n"
        ),
    )


def _owners():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email="")


def queue_member_signup_emails(member, *, review_url):
    for owner in _owners():
        queue_email(
            recipient=owner,
            kind=EmailNotification.Kind.MEMBER_SIGNUP,
            dedupe_key=f"member-signup:{member.pk}:owner:{owner.pk}",
            subject=f"New member waiting for approval: {member.display_name}",
            body=(
                f"{member.display_name} just signed up and is waiting for you to accept them.\n\n"
                f"Username: {member.username}\n"
                f"Email: {member.email}\n"
                f"Phone: {member.phone}\n\n"
                f"Accept or decline: {review_url}\n"
            ),
        )


def queue_member_decision_email(member, *, site_url):
    approved = member.approval_status == member.Approval.APPROVED
    return queue_email(
        recipient=member,
        kind=EmailNotification.Kind.MEMBER_APPROVED if approved else EmailNotification.Kind.MEMBER_REJECTED,
        dedupe_key=f"member-decision:{member.pk}:{member.approval_status}:{member.approval_decided_at.isoformat()}",
        subject="You're in! Your account is approved" if approved else "Your membership request",
        body=(
            f"Good news: your account has been approved. Log in to get started: {site_url}\n"
            if approved
            else "Sorry, your request to join was not accepted.\n"
        ),
    )


def queue_event_submitted_emails(event, *, review_url):
    for owner in _owners():
        if owner.pk == event.organizer.user_id:
            continue
        queue_email(
            recipient=owner,
            kind=EmailNotification.Kind.EVENT_SUBMITTED,
            dedupe_key=f"event-submitted:{event.pk}:{event.updated_at.isoformat()}:owner:{owner.pk}",
            subject=f"Event waiting for approval: {event.title}",
            body=(
                f"{event.organizer.display_name} wants to publish {event.title}.\n\n"
                f"Starts: {event.starts_at:%Y-%m-%d %H:%M} {event.timezone}\n"
                f"Location: {event.location.address_line}, {event.location.city}, "
                f"{event.location.country_code}\n\n"
                f"Approve or decline: {review_url}\n"
            ),
        )


def queue_event_decision_email(event, *, cancelled=False):
    """Tell the organizer what the owner decided about their event."""
    if cancelled:
        kind = EmailNotification.Kind.EVENT_CANCELLED
        subject = f"Your event was cancelled: {event.title}"
        body = f"The site owner cancelled your event {event.title}.\n"
    elif event.status == event.Status.PUBLISHED:
        kind = EmailNotification.Kind.EVENT_APPROVED
        subject = f"Your event is live: {event.title}"
        body = f"Your event {event.title} was approved and is now published.\n"
    else:
        kind = EmailNotification.Kind.EVENT_REJECTED
        subject = f"Your event was not approved: {event.title}"
        body = f"Your event {event.title} was not approved.\n"
    if event.review_note:
        body += f"\nMessage from the owner:\n{event.review_note}\n"
    return queue_email(
        recipient=event.organizer.user,
        kind=kind,
        dedupe_key=f"event-decision:{event.pk}:{event.status}:{event.updated_at.isoformat()}",
        subject=subject,
        body=body,
    )
