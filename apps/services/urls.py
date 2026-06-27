from django.urls import path
from . import views

app_name = "services"

urlpatterns = [
    path("manage-events/", views.manage_events, name="manage_events"),
    path("edit-event/<uuid:uid>/", views.edit_event, name="edit_event"),
    path("delete-event/<uuid:uid>/", views.delete_event, name="delete_event"),
    # AJAX: fetch modules for a course (used by event form modules_covered field)
    path("course-modules/", views.course_modules_json, name="course_modules_json"),
]
