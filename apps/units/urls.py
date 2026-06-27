from django.urls import path
from units import views

app_name = "units"

urlpatterns = [
    path("", views.unit_list, name="list"),
    path("create/", views.unit_create, name="create"),
    path("<slug:slug>/", views.unit_detail, name="detail"),
    path("<slug:slug>/edit/", views.unit_edit, name="edit"),
    path(
        "<slug:slug>/announcements/<int:announcement_id>/edit/",
        views.announcement_edit,
        name="announcement_edit",
    ),
    path(
        "<slug:slug>/announcements/<int:announcement_id>/delete/",
        views.announcement_delete,
        name="announcement_delete",
    ),
    path("<slug:slug>/tasks/<int:task_id>/edit/", views.task_edit, name="task_edit"),
    path(
        "<slug:slug>/tasks/<int:task_id>/delete/", views.task_delete, name="task_delete"
    ),
    path(
        "<slug:slug>/reports/<int:report_id>/status/",
        views.update_report_status,
        name="report_status",
    ),
    path("<slug:slug>/guests/", views.unit_guests, name="unit_guests"),
    path("<slug:slug>/guest-cards/", views.unit_guest_cards, name="guest_cards"),
    path("<slug:slug>/music/", views.unit_music, name="unit_music"),
    path("<slug:slug>/media/", views.unit_media, name="unit_media"),
    path("<slug:slug>/children/", views.unit_children, name="unit_children"),
    path("<slug:slug>/youth/", views.unit_youth, name="unit_youth"),
    path("<slug:slug>/teenagers/", views.unit_teenagers, name="unit_teenagers"),
    path("<slug:slug>/delete/", views.unit_delete, name="delete"),
    path("<slug:slug>/members/add/", views.add_member_to_unit, name="add_member"),
    path(
        "<slug:slug>/members/<int:membership_id>/remove/",
        views.remove_member_from_unit,
        name="remove_member",
    ),
    path(
        "<slug:slug>/members/<int:membership_id>/head/",
        views.set_unit_head,
        name="set_unit_head",
    ),
    path("transfer/", views.transfer_member, name="transfer_member"),
    path(
        "membership/<int:membership_id>/probation/on/",
        views.set_probation,
        name="set_probation",
    ),
    path(
        "membership/<int:membership_id>/probation/off/",
        views.clear_probation,
        name="clear_probation",
    ),
    path(
        "membership/<int:membership_id>/review/clear/",
        views.clear_for_review,
        name="clear_for_review",
    ),
    path("<slug:slug>/attendance/", views.mark_unit_attendance, name="mark_attendance"),
    path("campuses/", views.campus_list, name="campus_list"),
    path("campuses/create/", views.campus_create, name="campus_create"),
    path("campuses/transfer/", views.transfer_to_campus, name="transfer_to_campus"),
    path("<slug:slug>/ajax/members/", views.ajax_unit_members, name="ajax_members"),
    path("<slug:slug>/color/", views.update_unit_color, name="update_color"),
    # Task member interactions (AJAX + plain POST)
    path("tasks/<int:assignment_id>/acknowledge/", views.acknowledge_task, name="acknowledge_task"),
    path("tasks/<int:assignment_id>/complete/",    views.complete_task,    name="complete_task"),
    # Unit functional roles
    path("<slug:slug>/roles/assign/",                      views.functional_role_assign, name="role_assign"),
    path("<slug:slug>/roles/<int:assignment_id>/unassign/", views.functional_role_unassign, name="role_unassign"),
    path("<slug:slug>/roles/create/",                      views.functional_role_create, name="role_create"),
    path("<slug:slug>/roles/<int:role_id>/toggle/",        views.functional_role_toggle, name="role_toggle"),
]
