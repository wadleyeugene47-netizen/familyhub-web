from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import LoginForm
from .views import (
    ChangePasswordView,
    ForgotPasswordView,
    LoginView,
    RegisterView,
    SetNewPasswordView,
    dashboard,
    pending,
)

app_name = "accounts"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path(
        "login/",
        LoginView.as_view(authentication_form=LoginForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/forgot/", ForgotPasswordView.as_view(), name="password_reset"),
    path("password/reset/<uidb64>/<token>/", SetNewPasswordView.as_view(), name="password_reset_confirm"),
    path("password/change/", ChangePasswordView.as_view(), name="password_change"),
    path("dashboard/", dashboard, name="dashboard"),
    path("pending/", pending, name="pending"),
]
