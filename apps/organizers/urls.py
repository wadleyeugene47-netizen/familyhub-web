from django.urls import path

from . import views
from .dashboard_views import organizer_dashboard

app_name = "organizers"

urlpatterns = [
    path("dashboard/", organizer_dashboard, name="dashboard"),
    path("payments/onboarding/", views.payment_onboarding, name="payment_onboarding"),
    path("payments/return/", views.payment_onboarding_return, name="payment_return"),
]
