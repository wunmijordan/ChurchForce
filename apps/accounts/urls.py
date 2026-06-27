from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("username-check/", views.username_check, name="username_check"),
    # ── Password reset (fully self-service, no admin interaction) ────────────
    path(
        "password-reset/",
        views.TenantPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            post_reset_login=False,
            success_url="/login/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    # ── Member management ─────────────────────────────────────────────────────
    path("users/", views.user_list, name="user_list"),
    path("users/manage/", views.manage_user, name="create_user"),
    path("users/<str:username>/manage/", views.manage_user, name="edit_user"),
    path("users/credentials/", views.show_credentials, name="show_credentials"),
    path(
        "user/<str:username>/avatar/",
        views.upload_user_avatar,
        name="upload_user_avatar",
    ),
    # ── Batch / bulk member upload ────────────────────────────────────────────
    # In-form batch: up to 10 rows submitted in one POST
    path("users/batch-create/", views.batch_create_users, name="batch_create_users"),
    # Full CSV/Excel bulk upload
    path("users/bulk-upload/", views.bulk_upload_users, name="bulk_upload_users"),
    # Downloadable blank template (members or guests)
    path(
        "users/bulk-template/", views.bulk_upload_template, name="bulk_upload_template"
    ),
    # ── AJAX: load roles for a selected unit ─────────────────────────────────
    path("users/unit-roles/", views.unit_roles_json, name="unit_roles_json"),
    # ── Invitations ───────────────────────────────────────────────────────────
    path("invite/send/", views.send_invitation, name="send_invitation"),
    path("invite/<uuid:token>/", views.accept_invitation, name="accept_invitation"),
    path(
        "invite/<uuid:token>/resend/", views.resend_invitation, name="resend_invitation"
    ),
    # ── Attendance ────────────────────────────────────────────────────────────
    path("attendance/summary/", views.attendance_summary, name="attendance_summary"),
    path("attendance/clock/", views.clock_action, name="clock_action"),
    path("attendance/check/", views.attendance_check, name="attendance_check"),
    path("dismiss-hint/", views.dismiss_hint, name="dismiss_hint"),
]
