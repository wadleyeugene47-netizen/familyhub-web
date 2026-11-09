from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404


def is_owner(user):
    return user.is_authenticated and user.is_superuser


def owner_required(view):
    """Only the platform owner (a superuser) may use the view; everyone else gets a 404."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_owner(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper
