from django.urls import path
from . import views

app_name = "guests"

urlpatterns = [
    path(
        "webhooks/calls/<str:provider>/",
        views.calls_webhook,
        name="calls_webhook",
    ),
    # ── Guest list & CRUD ─────────────────────────────────────────────
    path("", views.guest_list_view, name="guest_list"),
    path("create/", views.create_guest, name="create_guest"),
    path("batch-create/", views.batch_create_guests, name="batch_create_guests"),
    path("<uuid:uid>/edit/", views.edit_guest, name="edit_guest"),
    path("<uuid:uid>/detail/", views.guest_detail_api, name="guest_detail_api"),
    # ── Status & assignment ───────────────────────────────────────────
    # Pipeline advances automatically. Only Not-Planted can be set manually.
    path(
        "<uuid:uid>/force-commit/",
        views.force_commit,
        name="force_commit",
    ),
    path(
        "<uuid:uid>/mark-not-planted/",
        views.mark_not_planted,
        name="mark_not_planted",
    ),
    path(
        "<uuid:uid>/log-call/",
        views.log_call_attempt,
        name="log_call_attempt",
    ),
    path("<uuid:uid>/reassign/", views.reassign_guest, name="reassign_guest"),
    # ── Reviews ───────────────────────────────────────────────────────
    path("reviews/<uuid:uid>/<str:role>/", views.submit_review, name="submit_review"),
    path(
        "<uuid:uid>/reviews/read/",
        views.mark_reviews_read,
        name="mark_reviews_read",
    ),
    # ── Follow-up reports ─────────────────────────────────────────────
    path(
        "<uuid:uid>/report/",
        views.followup_report_page,
        name="followup_report_page",
    ),
    path("<uuid:uid>/followup/", views.followup_history_view, name="followup_history"),
    path(
        "<uuid:uid>/followup/pdf/",
        views.export_followup_reports_pdf,
        name="followup_pdf",
    ),
    # ── Attendance ────────────────────────────────────────────────────
    path("attendance/", views.mark_attendance, name="mark_attendance"),
    # ── Bulk operations ───────────────────────────────────────────────
    # path("bulk-delete/",                      views.bulk_delete_guests,  name="bulk_delete_guests"),
    # ── Import / export ───────────────────────────────────────────────
    path("export/csv/", views.export_csv, name="export_csv"),
    path("export/excel/", views.export_guests_excel, name="export_guests_excel"),
    path("import/csv/", views.import_guests_csv, name="import_guests_csv"),
    path("import/excel/", views.import_guests_excel, name="import_excel"),
    path("bulk-upload/", views.bulk_upload_guests, name="bulk_upload_guests"),
    path("template/csv/", views.download_csv_template, name="download_csv_template"),
    # ── AJAX / chart data ─────────────────────────────────────────────
    path(
        "ajax/services/", views.services_attended_chart, name="services_attended_chart"
    ),
    path("ajax/channels/", views.channel_breakdown, name="channel_breakdown"),
    path("ajax/summary/", views.guest_entry_summary, name="guest_entry_summary"),
    path("ajax/top-services/", views.top_services_data, name="top_services_data"),
    # ── Membership pipeline ───────────────────────────────────────────
    path(
        "workforce-interest/<uuid:token>/",
        views.workforce_interest_form,
        name="workforce_interest_form",
    ),
    path("join/<uuid:token>/", views.open_interest_form, name="open_interest_form"),
    path(
        "tracks/steps/reorder/", views.reorder_track_steps, name="reorder_track_steps"
    ),
    # path("<uuid:uid>/apply/",             views.create_application,   name="create_application"),
    # path("applications/<int:app_id>/approve/", views.approve_application, name="approve_application"),
    # path("applications/<int:app_id>/decline/", views.decline_application, name="decline_application"),
    # path("applications/<int:app_id>/induct/",  views.induct_member,       name="induct_member"),
    # path("applications/<int:app_id>/step/<int:step_id>/complete/",
    #                                          views.complete_step,        name="complete_step"),
]
