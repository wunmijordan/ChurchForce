from django.urls import path
from . import views

app_name = "workforce"

urlpatterns = [
    path("", views.chat_room, name="chat_room"),
    path("chat/load/", views.load_more_messages, name="load_more_messages"),
    path(
        "private/<int:user_id>/messages/",
        views.load_private_messages,
        name="load_private_messages",
    ),
    path(
        "private/unread-counts/",
        views.private_unread_counts,
        name="private_unread_counts",
    ),
    path(
        "message/<int:message_id>/react/",
        views.chat_message_react,
        name="chat_message_react",
    ),
    path("fetch_link_preview/", views.fetch_link_preview, name="fetch_link_preview"),
    path("upload_file/", views.upload_file, name="upload_file"),
    path("attendance/", views.mark_attendance, name="mark_attendance"),
    # alias used by chat_room.js relative fetch: chat/load/ -> load_more_messages
    path("load/", views.load_more_messages, name="load_more_messages"),
    path("chat/load/", views.load_more_messages, name="chat_load_more"),
    path("attendance/summary/", views.attendance_summary, name="attendance_summary"),
    path("api/events/", views.api_events, name="api_events"),
    path("log-user-activity/", views.log_user_activity, name="log_user_activity"),
    path("get_active_events/", views.get_active_events, name="get_active_events"),
    path("settings/me/", views.member_settings, name="member_settings"),
    # Promotion approval
    path("promotions/", views.promotion_queue, name="promotion_queue"),
    path(
        "promotions/<int:task_id>/action/",
        views.approve_promotion,
        name="approve_promotion",
    ),
    path("create_sub_room/", views.create_sub_room, name="create_sub_room"),
    path(
        "sub_room/<int:room_id>/add_member/",
        views.add_sub_room_member,
        name="add_sub_room_member",
    ),
    path(
        "sub_room/<int:room_id>/members/",
        views.get_sub_room_members,
        name="get_sub_room_members",
    ),
    path(
        "sub_room/<int:room_id>/remove_member/",
        views.remove_sub_room_member,
        name="remove_sub_room_member",
    ),
    path("sub_room/<int:room_id>/edit/", views.edit_sub_room, name="edit_sub_room"),
    path(
        "sub_room/<int:room_id>/delete/", views.delete_sub_room, name="delete_sub_room"
    ),
    path("attendance/clock-out/", views.clock_out, name="clock_out"),
    path("ai_chat_assist/", views.ai_chat_assist, name="ai_chat_assist"),
]
