from django.urls import path
from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("",         views.dashboard_view,  name="dashboard"),
    path("dashboard/admin/",   views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/superuser/", views.superuser_dashboard, name="superuser_dashboard"),
]

