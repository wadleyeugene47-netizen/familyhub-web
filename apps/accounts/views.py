from axes.utils import reset as reset_lockout
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import (
    LoginView as BaseLoginView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import FormView

from apps.hub.models import SiteSettings

from apps.allstars.models import AllStarGame, AllStarRegistration
from apps.notifications.services import queue_member_signup_emails
from apps.leagues.models import League, LeagueJoinRequest, TeamMembership
from apps.payments.models import Payment
from apps.registrations.models import Registration

from .forms import RegistrationForm
from .models import PreApprovedPhone, User, normalize_phone


class LoginView(BaseLoginView):
    template_name = "accounts/login.html"

    def get_default_redirect_url(self):
        if self.request.user.is_superuser:
            return reverse("hub:dashboard")
        return super().get_default_redirect_url()


class ForgotPasswordView(PasswordResetView):
    template_name = "accounts/password_form.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:login")
    extra_context = {
        "heading": "Forgot your password?",
        "lead": "Enter your account's email and we'll send you a link to choose a new one.",
        "submit_label": "Send reset link",
    }

    @property
    def extra_email_context(self):
        return {"site_name": SiteSettings.load().site_name}

    def form_valid(self, form):
        response = super().form_valid(form)
        # The same message whether or not the email exists, so this page can't reveal who has an account.
        messages.info(self.request, "If that email has an account, a reset link is on its way.")
        return response


class SetNewPasswordView(PasswordResetConfirmView):
    template_name = "accounts/password_form.html"
    success_url = reverse_lazy("accounts:login")
    extra_context = {
        "heading": "Choose a new password",
        "lead": "Pick something you don't use anywhere else.",
        "submit_label": "Save new password",
    }

    def form_valid(self, form):
        response = super().form_valid(form)
        # Proving ownership of the email ends any lockout, whichever name they were typing at login.
        for identifier in (self.user.username, self.user.email):
            if identifier:
                reset_lockout(username=identifier)
        messages.success(self.request, "Your password was changed. You can log in now.")
        return response


class ChangePasswordView(PasswordChangeView):
    template_name = "accounts/password_form.html"
    success_url = reverse_lazy("accounts:dashboard")
    extra_context = {
        "heading": "Change your password",
        "lead": "Enter your current password, then the new one.",
        "submit_label": "Change password",
    }

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your password was changed.")
        return response


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegistrationForm
    success_url = "/accounts/dashboard/"

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            pre_approval = (
                PreApprovedPhone.objects.select_for_update()
                .filter(phone_digits=normalize_phone(user.phone), claimed_by__isnull=True)
                .first()
            )
            user.approval_status = User.Approval.APPROVED if pre_approval else User.Approval.PENDING
            user.save()
            if pre_approval:
                pre_approval.claimed_by = user
                pre_approval.claimed_at = timezone.now()
                pre_approval.save(update_fields=("claimed_by", "claimed_at"))
            else:
                queue_member_signup_emails(
                    user, review_url=self.request.build_absolute_uri(reverse("hub:members"))
                )
        login(
            self.request,
            user,
            backend="apps.accounts.backends.EmailOrUsernameBackend",
        )
        if not user.is_approved_member:
            return redirect("accounts:pending")
        return redirect(self.get_success_url())


@login_required
def pending(request):
    if request.user.is_approved_member:
        return redirect("accounts:dashboard")
    return render(request, "accounts/pending.html")


def _communities(user):
    """Each live league, with the user's team membership or join request when they have one."""
    leagues = list(League.objects.filter(status=League.Status.ACTIVE))
    memberships = {
        m.team.league_id: m
        for m in TeamMembership.objects.filter(
            user=user, status=TeamMembership.Status.ACTIVE, team__league__in=leagues
        ).select_related("team")
    }
    waiting = set(
        LeagueJoinRequest.objects.filter(
            user=user, status=LeagueJoinRequest.Status.PENDING, league__in=leagues
        ).values_list("league_id", flat=True)
    )
    for league in leagues:
        league.membership = memberships.get(league.pk)
        league.asked_to_play = league.pk in waiting
    return leagues


def _all_star_entries(user):
    return AllStarRegistration.objects.filter(
        user=user,
        status=AllStarRegistration.Status.ACTIVE,
        game__status__in=AllStarGame.PUBLIC_STATUSES,
    ).select_related("game", "team")


@login_required
def dashboard(request):
    registrations = (
        Registration.objects.filter(participant=request.user)
        .select_related("event", "event__category", "event__location", "event__organizer")
        .order_by("-registered_at")
    )
    payments = (
        Payment.objects.filter(registration__participant=request.user)
        .select_related("registration__event", "organizer")
        .prefetch_related("refunds")
        .order_by("-created_at")
    )
    counts = registrations.aggregate(
        confirmed=Count("id", filter=Q(status=Registration.Status.CONFIRMED)),
        pending=Count("id", filter=Q(status=Registration.Status.PENDING_PAYMENT)),
    )
    return render(
        request,
        "accounts/dashboard.html",
        {
            "registrations": registrations,
            "payments": payments,
            "confirmed_count": counts["confirmed"],
            "pending_count": counts["pending"],
            "organizer_profile": getattr(request.user, "organizer_profile", None),
            "communities": _communities(request.user),
            "all_star_entries": _all_star_entries(request.user),
        },
    )
