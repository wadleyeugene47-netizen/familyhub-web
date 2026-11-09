from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import EmailNotification
from apps.notifications.services import deliver_email


class Command(BaseCommand):
    help = "Retry pending or failed email notifications."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        stale_sending = timezone.now() - timedelta(minutes=15)
        candidates = EmailNotification.objects.filter(
            Q(status__in=[EmailNotification.Status.PENDING, EmailNotification.Status.FAILED])
            | Q(status=EmailNotification.Status.SENDING, updated_at__lt=stale_sending)
        ).order_by("created_at")[: max(0, options["limit"])]
        candidates = list(candidates)
        delivered = 0
        failed = 0
        for notification in candidates:
            if notification.status == EmailNotification.Status.SENDING:
                EmailNotification.objects.filter(
                    pk=notification.pk,
                    status=EmailNotification.Status.SENDING,
                ).update(status=EmailNotification.Status.PENDING, updated_at=timezone.now())
            if deliver_email(notification.pk):
                delivered += 1
            else:
                failed += 1
        self.stdout.write(
            self.style.SUCCESS(f"Processed {delivered + failed} notifications: {delivered} sent, {failed} still failed.")
        )
