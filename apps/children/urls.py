from django.urls import path
from . import views

app_name = "children"

urlpatterns = [
    path("", views.index, name="index"),
    path("children/add/", views.child_create, name="child_create"),
    path("sessions/add/", views.session_create, name="session_create"),
    path("sessions/<int:session_id>/", views.session_detail, name="session_detail"),
    path("sessions/<int:session_id>/update/", views.update_checkin, name="update_checkin"),
]
