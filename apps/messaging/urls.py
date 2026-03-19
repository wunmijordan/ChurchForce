from django.urls import path
from messaging import views

app_name = "messaging"

urlpatterns = [
    path("send/",                  views.send_bulk_message,  name="send_bulk"),
    path("message/<int:guest_id>/", views.send_guest_message, name="send_to_guest"),
    path("ajax/guests/",           views.get_guests_by_status, name="ajax_guests_by_status"),
    path("log/",                   views.message_log,         name="log"),
]