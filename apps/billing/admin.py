from django.contrib import admin
from django.contrib import messages
from billing.models import SubscriptionPlan, ChurchSubscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "multi_campus", "white_label", "description")
    list_filter = ("multi_campus", "white_label")


@admin.register(ChurchSubscription)
class ChurchSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "church",
        "plan",
        "is_trial",
        "is_active",
        "trial_expires_at",
        "expires_at",
        "started_at",
    )
    list_filter = ("plan", "is_trial", "is_active")
    search_fields = ("church__name", "church__slug")
    readonly_fields = ("started_at",)
    actions = ["grant_founders_saas", "grant_founders_white_label"]

    # ----------------------------------------------------------------
    # Admin actions — founders plan grants
    # ----------------------------------------------------------------

    @admin.action(description="Grant Founders SaaS Lifetime plan")
    def grant_founders_saas(self, request, queryset):
        from billing.services import grant_founders_plan

        success, errors = 0, []
        for sub in queryset.select_related("church"):
            try:
                grant_founders_plan(sub.church, "saas")
                success += 1
            except Exception as exc:
                errors.append(f"{sub.church.slug}: {exc}")

        if success:
            self.message_user(
                request,
                f"Founders SaaS Lifetime granted to {success} church(es).",
                messages.SUCCESS,
            )
        for err in errors:
            self.message_user(request, err, messages.ERROR)

    @admin.action(description="Grant Founders White Label Lifetime plan")
    def grant_founders_white_label(self, request, queryset):
        from billing.services import grant_founders_plan

        success, errors = 0, []
        for sub in queryset.select_related("church"):
            try:
                grant_founders_plan(sub.church, "white_label")
                success += 1
            except Exception as exc:
                errors.append(f"{sub.church.slug}: {exc}")

        if success:
            self.message_user(
                request,
                f"Founders White Label Lifetime granted to {success} church(es). "
                "Remember to set custom_domain and run provision_white_label.",
                messages.SUCCESS,
            )
        for err in errors:
            self.message_user(request, err, messages.ERROR)