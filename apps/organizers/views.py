from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.shortcuts import redirect, render

from apps.hub.features import is_enabled

from .models import OrganizerPaymentAccount, OrganizerProfile
from .services import StripeOnboardingError, create_or_refresh_onboarding_link, sync_account_status


def _get_organizer(request):
    return OrganizerProfile.objects.filter(user=request.user, is_active=True).first()


@login_required
def payment_onboarding(request):
    if not is_enabled("payments"):
        raise Http404
    organizer = _get_organizer(request)
    if organizer is None:
        messages.error(request, "An active organizer profile is required to enable payments.")
        return redirect("accounts:dashboard")
    account = OrganizerPaymentAccount.objects.filter(organizer=organizer).first()
    if request.method == "POST":
        try:
            onboarding_url = create_or_refresh_onboarding_link(organizer)
        except ImproperlyConfigured as error:
            messages.error(request, str(error))
        except StripeOnboardingError as error:
            messages.error(request, str(error))
        else:
            return redirect(onboarding_url)
    return render(
        request,
        "organizers/payment_onboarding.html",
        {"organizer": organizer, "payment_account": account},
    )


@login_required
def payment_onboarding_return(request):
    if not is_enabled("payments"):
        raise Http404
    organizer = _get_organizer(request)
    if organizer is None:
        messages.error(request, "An active organizer profile is required to enable payments.")
        return redirect("accounts:dashboard")
    try:
        account = sync_account_status(organizer)
    except OrganizerPaymentAccount.DoesNotExist:
        messages.info(request, "Stripe onboarding has not been started yet.")
        return redirect("organizers:payment_onboarding")
    except ImproperlyConfigured as error:
        messages.error(request, str(error))
        return redirect("organizers:payment_onboarding")
    except StripeOnboardingError as error:
        messages.error(request, str(error))
        return redirect("organizers:payment_onboarding")
    if account.charges_enabled:
        messages.success(request, "Stripe reports that your account can accept payments.")
    else:
        messages.info(request, "Onboarding is not complete yet. Review the current requirements below.")
    return redirect("organizers:payment_onboarding")
