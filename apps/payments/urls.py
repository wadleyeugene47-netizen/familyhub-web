from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("checkout/<int:registration_id>/", views.checkout, name="checkout"),
    path("return/<int:registration_id>/", views.checkout_return, name="checkout_return"),
    path("refund/<uuid:payment_id>/", views.refund_payment, name="refund"),
    path("webhooks/stripe/", views.stripe_webhook, name="stripe_webhook"),
]
