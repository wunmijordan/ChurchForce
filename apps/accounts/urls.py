from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    # Member management
    path("users/",                      views.user_list,        name="user_list"),
    path("users/manage/",               views.manage_user,      name="create_user"),
    path("users/<int:user_id>/manage/", views.manage_user,      name="edit_user"),
    path("users/credentials/",          views.show_credentials, name="show_credentials"),

    # Profile
    path("profile/edit/", views.edit_profile, name="edit_profile"),

    # Invitations
    path("invite/send/",                views.send_invitation,   name="send_invitation"),
    path("invite/<uuid:token>/",        views.accept_invitation, name="accept_invitation"),
    path("invite/<uuid:token>/resend/", views.resend_invitation, name="resend_invitation"),

    # Attendance
    path("attendance/summary/", views.attendance_summary, name="attendance_summary"),
    path("attendance/clock/",   views.clock_action,       name="clock_action"),
    path("attendance/check/",   views.attendance_check,   name="attendance_check"),
]
