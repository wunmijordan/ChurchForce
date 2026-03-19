from django.urls import path
from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("",         views.dashboard_view,  name="dashboard"),
    path("admin/",   views.admin_dashboard, name="admin_dashboard"),
]