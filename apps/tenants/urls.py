from django.urls import path
from tenants import views
from django.conf import settings

app_name = "tenants"

urlpatterns = [
    # Public self-serve signup
    path("signup/", views.signup, name="signup"),

    # Subscription status walls
    # Linked from TenantMiddleware when a subscription lapses.
    # Both are exempt from the subscription guard so lapsed tenants
    # can always reach the upgrade/billing flow.
    path("trial-expired/",          views.trial_expired,          name="trial_expired"),
    path("subscription-inactive/",  views.subscription_inactive,  name="subscription_inactive"),
    # Church admin settings — tabbed
    path("settings/church/",            views.church_settings,   name="church_settings"),

    # Lookup table AJAX CRUD (VisitChannel / VisitPurpose / ChurchService)
    path("settings/lookup/<str:lookup_type>/add/",        views.lookup_add,    name="lookup_add"),
    path("settings/lookup/<str:lookup_type>/<int:pk>/toggle/", views.lookup_toggle, name="lookup_toggle"),
    path("settings/lookup/<str:lookup_type>/<int:pk>/rename/", views.lookup_rename, name="lookup_rename"),
    path("settings/lookup/<str:lookup_type>/reorder/",        views.lookup_reorder, name="lookup_reorder"),

    # Workforce AJAX CRUD — stages and roles
    path("settings/workforce/<str:item_type>/add/",           views.workforce_item_add,              name="workforce_item_add"),
    path("settings/workforce/<str:item_type>/<int:pk>/rename/", views.workforce_item_rename,         name="workforce_item_rename"),
    path("settings/workforce/<str:item_type>/reorder/",       views.workforce_item_reorder,          name="workforce_item_reorder"),
    path("settings/workforce/<str:item_type>/<int:pk>/delete/", views.workforce_item_delete,         name="workforce_item_delete"),
    path("settings/workforce/role/<int:pk>/toggle-leadership/", views.workforce_role_toggle_leadership, name="workforce_role_toggle_leadership"),

    # Campus stages AJAX
    path("settings/campus-stages/save/", views.campus_stage_save, name="campus_stage_save"),

    # LMS quick create
    path("settings/lms/course/create/",  views.lms_course_create,     name="lms_course_create"),

    # Membership track save (create + update)
    path("settings/track/save/",         views.membership_track_save, name="membership_track_save"),
    path("settings/track/<int:pk>/save/", views.membership_track_save, name="membership_track_update"),
]

if settings.DEBUG:
    from .views_dev import dev_quick_signup

    urlpatterns += [
        path("dev/signup/", dev_quick_signup, name="dev_quick_signup"),
    ]