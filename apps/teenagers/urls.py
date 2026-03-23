from django.urls import path
from . import views

app_name = "teenagers"

urlpatterns = [
    path("", views.index, name="index"),
    path("members/add/", views.teen_create, name="teen_create"),
    path("pulse/add/", views.pulse_create, name="pulse_create"),
    path("mentors/add/", views.mentor_create, name="mentor_create"),
]
