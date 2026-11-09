from django.contrib import admin

from .models import (
    OrganizerPayout,
    Payment,
    PaymentAttempt,
    PaymentDispute,
    Refund,
    TransferRecord,
    WebhookEvent,
)


class PaymentAttemptInline(admin.TabularInline):
    model = PaymentAttempt
    extra = 0
    readonly_fields = ("idempotency_key", "stripe_checkout_session_id", "status", "created_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "organizer", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency", "created_at")
    search_fields = ("id", "registration__participant__username", "organizer__display_name")
    readonly_fields = (
        "id",
        "stripe_payment_intent_id",
        "amount",
        "platform_fee",
        "organizer_amount",
        "currency",
        "created_at",
        "updated_at",
    )
    inlines = (PaymentAttemptInline,)


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("stripe_refund_id", "payment", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("stripe_refund_id", "payment__id")
    readonly_fields = ("idempotency_key", "stripe_refund_id", "payment", "amount", "currency", "created_at")


@admin.register(PaymentDispute)
class PaymentDisputeAdmin(admin.ModelAdmin):
    list_display = ("stripe_dispute_id", "payment", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("stripe_dispute_id", "payment__id")


@admin.register(TransferRecord)
class TransferRecordAdmin(admin.ModelAdmin):
    list_display = ("stripe_transfer_id", "payment", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("stripe_transfer_id", "payment__id")


@admin.register(OrganizerPayout)
class OrganizerPayoutAdmin(admin.ModelAdmin):
    list_display = ("stripe_payout_id", "organizer", "amount", "currency", "status", "arrival_date")
    list_filter = ("status", "currency")
    search_fields = ("stripe_payout_id", "organizer__display_name")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "event_type", "status", "received_at", "processed_at")
    list_filter = ("status", "event_type")
    search_fields = ("stripe_event_id", "event_type")
    readonly_fields = ("stripe_event_id", "event_type", "status", "received_at", "processed_at")
