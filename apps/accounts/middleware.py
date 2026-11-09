from django.shortcuts import redirect
from django.views.static import serve

# The only pages a signed-in member can open before the owner approves them.
ALLOWED_VIEWS = {
    "accounts:pending",
    "accounts:logout",
    "accounts:login",
    "accounts:register",
    "accounts:password_reset",
    "accounts:password_reset_confirm",
    "terms",
    "privacy",
}
# Someone still waiting may message the host, for example to say who they are. Declined accounts may not.
WAITING_VIEWS = {"messaging:chat", "messaging:poll"}


class MemberApprovalMiddleware:
    """Keep members who are waiting for (or were refused) approval on the waiting page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = request.user
        if not user.is_authenticated or user.is_approved_member:
            return None
        view_name = request.resolver_match.view_name
        if view_name in ALLOWED_VIEWS or view_func is serve:
            return None
        if view_name in WAITING_VIEWS and user.approval_status == user.Approval.PENDING:
            return None
        return redirect("accounts:pending")
