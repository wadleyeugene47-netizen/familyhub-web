from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.hub.decorators import owner_required
from apps.notifications.services import queue_member_decision_email

from .forms import PreApprovedPhoneForm
from .models import PreApprovedPhone, User


@owner_required
def members(request):
    people = User.objects.filter(is_superuser=False).order_by("-date_joined")
    return render(
        request,
        "hub/members.html",
        {
            "pending_members": people.filter(approval_status=User.Approval.PENDING),
            "approved_members": people.filter(approval_status=User.Approval.APPROVED)[:100],
            "rejected_members": people.filter(approval_status=User.Approval.REJECTED),
        },
    )


@owner_required
def member_decide(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    member = get_object_or_404(User, pk=user_id, is_superuser=False)
    decision = request.POST.get("decision")
    if decision == "approve":
        member.approval_status = User.Approval.APPROVED
    elif decision == "reject":
        member.approval_status = User.Approval.REJECTED
    else:
        messages.error(request, "Choose accept or decline.")
        return redirect("hub:members")
    member.approval_decided_at = timezone.now()
    member.save(update_fields=("approval_status", "approval_decided_at"))
    queue_member_decision_email(member, site_url=request.build_absolute_uri(reverse("accounts:login")))
    if member.approval_status == User.Approval.APPROVED:
        messages.success(request, f"{member.display_name} is now a member.")
    else:
        messages.info(request, f"{member.display_name} was declined.")
    return redirect("hub:members")


@owner_required
def phones(request):
    form = PreApprovedPhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.added_by = request.user
        entry.save()
        messages.success(request, f"{entry.phone} can now sign up and join right away.")
        return redirect("hub:phones")
    return render(
        request,
        "hub/phones.html",
        {"form": form, "entries": PreApprovedPhone.objects.select_related("claimed_by")},
    )


@owner_required
def phone_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    entry = get_object_or_404(PreApprovedPhone, pk=pk)
    entry.delete()
    messages.info(request, f"{entry.phone} was removed from your list.")
    return redirect("hub:phones")
