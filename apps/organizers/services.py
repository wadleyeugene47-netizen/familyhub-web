from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
import stripe

from .models import OrganizerPaymentAccount, OrganizerProfile


class StripeOnboardingError(Exception):
    """Raised when connected-account onboarding cannot be started."""


def _stripe_client():
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured(
            "Set STRIPE_SECRET_KEY to a Stripe test-mode secret key before starting onboarding."
        )
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


@transaction.atomic
def create_or_refresh_onboarding_link(organizer: OrganizerProfile) -> str:
    client = _stripe_client()
    try:
        account_record = OrganizerPaymentAccount.objects.select_for_update().filter(
            organizer=organizer
        ).first()
        if account_record is None:
            account = client.v1.accounts.create(
                {
                    "type": "express",
                    "capabilities": {
                        "card_payments": {"requested": True},
                        "transfers": {"requested": True},
                    },
                    "metadata": {"organizer_profile_id": str(organizer.pk)},
                },
                options={"idempotency_key": f"organizer-connect-{organizer.pk}"},
            )
            account_record = OrganizerPaymentAccount.objects.create(
                organizer=organizer,
                connected_account_id=account.id,
                onboarding_status=OrganizerPaymentAccount.OnboardingStatus.PENDING,
            )
        link = client.v1.account_links.create(
            {
                "account": account_record.connected_account_id,
                "refresh_url": settings.STRIPE_CONNECT_REFRESH_URL,
                "return_url": settings.STRIPE_CONNECT_RETURN_URL,
                "type": "account_onboarding",
            }
        )
    except stripe.StripeError as error:
        raise StripeOnboardingError("Stripe could not start connected-account onboarding.") from error
    return link.url


def sync_account_status(organizer: OrganizerProfile) -> OrganizerPaymentAccount:
    account_record = OrganizerPaymentAccount.objects.get(organizer=organizer)
    client = _stripe_client()
    try:
        account = client.v1.accounts.retrieve(account_record.connected_account_id)
    except stripe.StripeError as error:
        raise StripeOnboardingError("Stripe account status could not be retrieved.") from error

    requirements = account.requirements
    currently_due = list(getattr(requirements, "currently_due", []) or [])
    past_due = list(getattr(requirements, "past_due", []) or [])
    account_record.charges_enabled = bool(account.charges_enabled)
    account_record.payouts_enabled = bool(account.payouts_enabled)
    account_record.details_submitted = bool(account.details_submitted)
    account_record.requirements_due = currently_due + past_due
    if account_record.charges_enabled and account_record.details_submitted:
        account_record.onboarding_status = OrganizerPaymentAccount.OnboardingStatus.ENABLED
    elif past_due:
        account_record.onboarding_status = OrganizerPaymentAccount.OnboardingStatus.RESTRICTED
    else:
        account_record.onboarding_status = OrganizerPaymentAccount.OnboardingStatus.PENDING
    account_record.save(
        update_fields=(
            "charges_enabled",
            "payouts_enabled",
            "details_submitted",
            "requirements_due",
            "onboarding_status",
            "updated_at",
        )
    )
    return account_record
