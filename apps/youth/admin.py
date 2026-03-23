from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from youth.models import YouthProfile, CareerGoal, ImpactProject


class CareerGoalInline(ChurchTabularInline):
    model = CareerGoal
    extra = 0


@admin.register(YouthProfile)
class YouthProfileAdmin(ChurchAdmin):
    list_display = ("full_name", "unit", "member", "career_interest", "church", "is_active")
    list_filter = ("church", "unit", "is_active")
    search_fields = ("full_name", "career_interest", "member__user__full_name")
    inlines = [CareerGoalInline]


@admin.register(CareerGoal)
class CareerGoalAdmin(ChurchAdmin):
    list_display = ("title", "youth", "status", "church")
    list_filter = ("church", "status")
    search_fields = ("title", "youth__full_name")


@admin.register(ImpactProject)
class ImpactProjectAdmin(ChurchAdmin):
    list_display = ("title", "unit", "status", "start_date", "church", "is_active")
    list_filter = ("church", "status", "unit", "is_active")
    search_fields = ("title", "focus_area")
