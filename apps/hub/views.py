from django.contrib import messages
from django.db.models import Count
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import User
from apps.events.models import Event
from apps.messaging.services import conversations_for_host

from .decorators import owner_required
from .forms import ChoiceOptionFormSet, SiteSettingsForm
from .models import ChoiceList, Feature, SiteSettings


def _legal_page(request, title, text):
    if not text.strip():
        raise Http404
    return render(request, "hub/legal.html", {"title": title, "text": text})


def terms(request):
    return _legal_page(request, "Terms of service", SiteSettings.load().terms_text)


def privacy(request):
    return _legal_page(request, "Privacy policy", SiteSettings.load().privacy_text)


@owner_required
def dashboard(request):
    members = User.objects.filter(is_superuser=False)
    return render(
        request,
        "hub/owner_dashboard.html",
        {
            "features": Feature.objects.all(),
            "choice_lists": ChoiceList.objects.annotate(option_count=Count("options")),
            "member_count": members.filter(approval_status=User.Approval.APPROVED).count(),
            "upcoming_count": Event.objects.filter(
                status=Event.Status.PUBLISHED, starts_at__gte=timezone.now()
            ).count(),
            "pending_members": members.filter(approval_status=User.Approval.PENDING).order_by("-date_joined")[:5],
            "recent_conversations": conversations_for_host().filter(last_message_at__isnull=False)[:5],
        },
    )


@owner_required
def toggle_feature(request, key):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    feature = get_object_or_404(Feature, key=key)
    feature.enabled = request.POST.get("enabled") == "on"
    feature.save(update_fields=("enabled", "updated_at"))
    messages.success(request, f"{feature.name} is now switched {'on' if feature.enabled else 'off'}.")
    return redirect("hub:dashboard")


@owner_required
def site_settings(request):
    form = SiteSettingsForm(request.POST or None, instance=SiteSettings.load())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("hub:dashboard")
    return render(request, "hub/site_settings.html", {"form": form})


@owner_required
def choice_list(request, key):
    choice_list = get_object_or_404(ChoiceList, key=key)
    formset = ChoiceOptionFormSet(request.POST or None, instance=choice_list)
    if request.method == "POST" and formset.is_valid():
        formset.save()
        messages.success(request, f"{choice_list.name} saved.")
        return redirect("hub:choice_list", key=choice_list.key)
    return render(request, "hub/choice_list.html", {"choice_list": choice_list, "formset": formset})
