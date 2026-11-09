from django.contrib import admin

from .models import ChoiceList, ChoiceOption, Feature, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("site_name", "default_fee_percent", "updated_at")

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "enabled", "updated_at")
    list_editable = ("enabled",)
    readonly_fields = ("key",)


class ChoiceOptionInline(admin.TabularInline):
    model = ChoiceOption
    extra = 1


@admin.register(ChoiceList)
class ChoiceListAdmin(admin.ModelAdmin):
    list_display = ("name", "key")
    inlines = (ChoiceOptionInline,)
