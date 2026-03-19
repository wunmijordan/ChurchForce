from django.contrib import admin
from bootstrap.models import (
    ChurchTemplate,
    TemplateGuestStatus,
    TemplateService,
    TemplateVisitPurpose,
    TemplateVisitChannel,
    TemplateUnit,
    TemplateWorkforceStage,
    TemplateWorkforceRole,
    TemplateUnitRole,
)


class TemplateGuestStatusInline(admin.TabularInline):
    model  = TemplateGuestStatus
    extra  = 1
    fields = ("name", "color", "order", "is_default")


class TemplateServiceInline(admin.TabularInline):
    model  = TemplateService
    extra  = 1
    fields = ("name", "order")


class TemplateVisitPurposeInline(admin.TabularInline):
    model  = TemplateVisitPurpose
    extra  = 1
    fields = ("name", "order")


class TemplateVisitChannelInline(admin.TabularInline):
    model  = TemplateVisitChannel
    extra  = 1
    fields = ("name", "order")


class TemplateUnitInline(admin.TabularInline):
    model  = TemplateUnit
    extra  = 1
    fields = ("name",)


class TemplateWorkforceStageInline(admin.TabularInline):
    model  = TemplateWorkforceStage
    extra  = 1
    fields = ("name", "order")


class TemplateWorkforceRoleInline(admin.TabularInline):
    model  = TemplateWorkforceRole
    extra  = 1
    fields = ("name", "is_leadership", "order")


class TemplateUnitRoleInline(admin.TabularInline):
    model  = TemplateUnitRole
    extra  = 1
    fields = ("unit_name", "name", "is_leadership", "order")


@admin.register(ChurchTemplate)
class ChurchTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "is_system", "is_active", "created_at")
    list_filter  = ("is_default", "is_system", "is_active")
    search_fields = ("name", "description")

    inlines = [
        TemplateGuestStatusInline,
        TemplateServiceInline,
        TemplateVisitPurposeInline,
        TemplateVisitChannelInline,
        TemplateUnitInline,
        TemplateWorkforceStageInline,
        TemplateWorkforceRoleInline,
        TemplateUnitRoleInline,
    ]

    actions = ["apply_to_all_churches"]

    @admin.action(description="Apply this template to all active churches (dry run — check logs)")
    def apply_to_all_churches(self, request, queryset):
        from bootstrap.services import apply_template_to_church
        from tenants.models import Church
        from django.contrib import messages

        churches = Church.raw_objects.filter(is_active=True)
        count = 0
        for template in queryset:
            for church in churches:
                try:
                    apply_template_to_church(template, church)
                    count += 1
                except Exception as exc:
                    self.message_user(
                        request,
                        f"Failed for {church.name} with template {template.name}: {exc}",
                        messages.WARNING,
                    )
        self.message_user(
            request,
            f"Template applied to {count} church(es).",
            messages.SUCCESS,
        )