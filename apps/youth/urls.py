from django.urls import path
from . import views

app_name = "youth"

urlpatterns = [
    path("", views.index, name="index"),
    path("members/add/", views.youth_create, name="youth_create"),
    path("projects/add/", views.project_create, name="project_create"),
    path("goals/add/", views.goal_create, name="goal_create"),
]

