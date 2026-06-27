from django.urls import path
from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("admin/", views.admin_dashboard, name="admin_dashboard"),
    path("superuser/", views.superuser_dashboard, name="superuser_dashboard"),
    path("trainee/", views.trainee_dashboard, name="trainee_dashboard"),
    path(
        "ajax/evangelism-research/",
        views.evangelism_research_view,
        name="evangelism_research",
    ),
    path(
        "ajax/announce/create/", views.workforce_announce_create, name="announce_create"
    ),
    path("ajax/task/create/", views.workforce_task_create, name="task_create"),
]
