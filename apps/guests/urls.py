from django.urls import path
from . import views

app_name = "guests"

urlpatterns = [
    # ── Guest list & CRUD ─────────────────────────────────────────────
    path("",                                  views.guest_list_view,     name="guest_list"),
    path("create/",                           views.create_guest,        name="create_guest"),
    path("<int:pk>/edit/",                    views.edit_guest,          name="edit_guest"),
    path("<int:guest_id>/detail/",            views.guest_detail_api,    name="guest_detail_api"),

    # ── Status & assignment ───────────────────────────────────────────
    #path("status/<int:pk>/",                  views.update_guest_status, name="update_guest_status"),
    #path("<int:guest_id>/status/<str:status_key>/", views.update_status_view, name="update_status"),
    path("<int:guest_id>/reassign/",          views.reassign_guest,      name="reassign_guest"),

    # ── Reviews ───────────────────────────────────────────────────────
    #path("reviews/<int:guest_id>/<str:role>/", views.submit_review,     name="submit_review"),
    #path("<int:guest_id>/reviews/read/",      views.mark_reviews_read,  name="mark_reviews_read"),

    # ── Follow-up reports ─────────────────────────────────────────────
    path("<int:guest_id>/report/",            views.followup_report_page, name="followup_report_page"),
    path("<int:guest_id>/followup/",          views.followup_history_view, name="followup_history"),
    path("<int:guest_id>/followup/pdf/",      views.export_followup_reports_pdf, name="followup_pdf"),

    # ── Attendance ────────────────────────────────────────────────────
    path("attendance/",                       views.mark_attendance,     name="mark_attendance"),

    # ── Bulk operations ───────────────────────────────────────────────
    #path("bulk-delete/",                      views.bulk_delete_guests,  name="bulk_delete_guests"),

    # ── Import / export ───────────────────────────────────────────────
    path("export/csv/",                       views.export_csv,           name="export_csv"),
    path("export/excel/",                     views.export_guests_excel,  name="export_guests_excel"),
    path("import/csv/",                       views.import_guests_csv,    name="import_guests_csv"),
    path("import/excel/",                     views.import_guests_excel,  name="import_excel"),
    path("template/csv/",                     views.download_csv_template, name="download_csv_template"),

    # ── AJAX / chart data ─────────────────────────────────────────────
    path("ajax/services/",                    views.services_attended_chart, name="services_attended_chart"),
    path("ajax/channels/",                    views.channel_breakdown,    name="channel_breakdown"),
    path("ajax/summary/",                     views.guest_entry_summary,  name="guest_entry_summary"),
    path("ajax/top-services/",                views.top_services_data,    name="top_services_data"),

    # ── Membership pipeline ───────────────────────────────────────────
    #path("<int:guest_id>/apply/",             views.create_application,   name="create_application"),
    #path("applications/<int:app_id>/approve/", views.approve_application, name="approve_application"),
    #path("applications/<int:app_id>/decline/", views.decline_application, name="decline_application"),
    #path("applications/<int:app_id>/induct/",  views.induct_member,       name="induct_member"),
    #path("applications/<int:app_id>/step/<int:step_id>/complete/",
    #                                          views.complete_step,        name="complete_step"),
]