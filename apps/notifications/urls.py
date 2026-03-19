# notifications/urls.py
from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("mark-read/<int:pk>/", views.mark_notification_read, name="mark_read"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
    path("api/unread/", views.unread_notifications, name="notifications_unread_api"),
    path("update-settings/", views.update_user_settings, name="update_user_settings"),
    path("settings/", views.user_settings, name="user_settings"),
    path("save-subscription/", views.save_subscription, name="save_subscription"),
    path("test/", views.test_push, name="test_push"),
]
