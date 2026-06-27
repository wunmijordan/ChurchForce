from django.urls import path
from tenants import views

app_name = "tenants"

urlpatterns = [
    # Public self-serve signup
    path("signup/", views.signup, name="signup"),
    path("signup/url-check/", views.signup_url_check, name="signup_url_check"),
    path("signup/credentials/", views.signup_credentials, name="signup_credentials"),
    # Subscription status walls
    # Linked from TenantMiddleware when a subscription lapses.
    # Both are exempt from the subscription guard so lapsed tenants
    # can always reach the upgrade/billing flow.
    path("trial-expired/", views.trial_expired, name="trial_expired"),
    path(
        "subscription-inactive/",
        views.subscription_inactive,
        name="subscription_inactive",
    ),
    # Church admin settings â€” tabbed
    path(
        "settings/church/", views.account_page, name="church_settings"
    ),  # alias → account_page
    path("account/", views.account_page, name="account_page"),
    # Lookup table AJAX CRUD (VisitChannel / VisitPurpose / ChurchService)
    path("settings/lookup/<str:lookup_type>/add/", views.lookup_add, name="lookup_add"),
    path(
        "settings/lookup/<str:lookup_type>/<int:pk>/toggle/",
        views.lookup_toggle,
        name="lookup_toggle",
    ),
    path(
        "settings/lookup/<str:lookup_type>/<int:pk>/rename/",
        views.lookup_rename,
        name="lookup_rename",
    ),
    path(
        "settings/lookup/<str:lookup_type>/reorder/",
        views.lookup_reorder,
        name="lookup_reorder",
    ),
    # Workforce AJAX CRUD â€” stages and roles
    path(
        "settings/workforce/<str:item_type>/add/",
        views.workforce_item_add,
        name="workforce_item_add",
    ),
    path(
        "settings/workforce/<str:item_type>/<int:pk>/rename/",
        views.workforce_item_rename,
        name="workforce_item_rename",
    ),
    path(
        "settings/workforce/<str:item_type>/reorder/",
        views.workforce_item_reorder,
        name="workforce_item_reorder",
    ),
    path(
        "settings/workforce/<str:item_type>/<int:pk>/delete/",
        views.workforce_item_delete,
        name="workforce_item_delete",
    ),
    path(
        "settings/workforce/role/<int:pk>/toggle-leadership/",
        views.workforce_role_toggle_leadership,
        name="workforce_role_toggle_leadership",
    ),
    path(
        "settings/workforce/role/<int:pk>/set-tier/",
        views.workforce_role_set_tier,
        name="workforce_role_set_tier",
    ),
    path(
        "settings/unit-role/<int:pk>/update/",
        views.unit_role_update,
        name="unit_role_update",
    ),
    # Campus stages AJAX
    path(
        "settings/campus-stages/save/",
        views.campus_stage_save,
        name="campus_stage_save",
    ),
    path(
        "settings/campuses/",
        views.campus_settings,
        name="campus_settings",
    ),
    path(
        "settings/campuses/<slug:slug>/workspace/",
        views.campus_workspace,
        name="campus_workspace",
    ),
    path(
        "settings/campus/create/",
        views.campus_create_inline,
        name="campus_create_inline",
    ),
    path(
        "settings/campus/<slug:slug>/update/",
        views.campus_update_inline,
        name="campus_update_inline",
    ),
    path(
        "settings/campus/<slug:slug>/delete/",
        views.campus_delete_inline,
        name="campus_delete_inline",
    ),
    path(
        "settings/campus/<slug:slug>/hq-override/",
        views.campus_hq_override,
        name="campus_hq_override",
    ),
    path(
        "settings/campus/transfer/",
        views.campus_transfer_inline,
        name="campus_transfer_inline",
    ),
    path(
        "settings/campus/assign/",
        views.campus_assign_inline,
        name="campus_assign_inline",
    ),
    path(
        "settings/campus/remove/",
        views.campus_remove_inline,
        name="campus_remove_inline",
    ),
    path(
        "settings/unit/<int:unit_id>/edit/",
        views.unit_inline_edit,
        name="unit_inline_edit",
    ),
    # Unit role AJAX CRUD
    path("settings/unit-role/add/", views.unit_role_add, name="unit_role_add"),
    path(
        "settings/unit-role/<int:pk>/delete/",
        views.unit_role_delete,
        name="unit_role_delete",
    ),
    # LMS quick create
    path(
        "settings/lms/course/create/", views.lms_course_create, name="lms_course_create"
    ),
    # Membership track save (create + update)
    path(
        "settings/track/save/",
        views.membership_track_save,
        name="membership_track_save",
    ),
    path(
        "settings/track/<int:pk>/save/",
        views.membership_track_save,
        name="membership_track_update",
    ),
    # Track step delete (AJAX)
    path(
        "settings/track/step/<int:pk>/delete/",
        views.track_step_delete,
        name="track_step_delete",
    ),
]
