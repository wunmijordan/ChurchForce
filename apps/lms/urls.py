from django.urls import path
from lms import views

app_name = "lms"

urlpatterns = [
    # ── Member-facing ────────────────────────────────────────────────────────
    path("", views.course_list, name="course_list"),
    path("course/<slug:course_slug>/", views.course_detail, name="course_detail"),
    path(
        "enroll/<int:enrollment_id>/submit/<int:module_id>/",
        views.submit_module,
        name="submit_module",
    ),
    path(
        "enroll/<int:enrollment_id>/complete/<int:module_id>/",
        views.module_complete,
        name="module_complete",
    ),
    path("certificates/", views.my_certificates, name="certificates"),
    path("dashboard/", views.trainee_redirect, name="trainee_dashboard"),

    # ── Discussion ───────────────────────────────────────────────────────────
    path("module/<int:module_id>/discuss/", views.discussion_post, name="discussion_post"),

    # ── Peer review ──────────────────────────────────────────────────────────
    path(
        "peer-review/<int:assignment_id>/submit/",
        views.peer_review_submit,
        name="peer_review_submit",
    ),

    # ── Announcements ────────────────────────────────────────────────────────
    path(
        "course/<slug:course_slug>/announce/",
        views.announcement_create,
        name="announcement_create",
    ),

    # ── Admin: courses ───────────────────────────────────────────────────────
    path("admin/", views.admin_courses, name="admin_courses"),
    path("admin/course/create/", views.course_create, name="course_create"),
    path("admin/course/<slug:course_slug>/edit/", views.course_edit, name="course_edit"),
    path("admin/course/<slug:course_slug>/module/add/", views.module_add, name="module_add"),
    path("admin/module/<int:module_id>/edit/", views.module_edit, name="module_edit"),

    # ── Admin: rubric builder ────────────────────────────────────────────────
    path("admin/module/<int:module_id>/rubric/", views.rubric_edit, name="rubric_edit"),

    # ── Review queue ─────────────────────────────────────────────────────────
    path("review/<int:submission_id>/", views.review_submission, name="review_submission"),
    path("proctor/", views.proctor_queue, name="proctor_queue"),

    # ── Sessions ─────────────────────────────────────────────────────────────
    path(
        "admin/course/<slug:course_slug>/sessions/",
        views.session_list,
        name="session_list",
    ),
]
