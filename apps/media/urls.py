from django.urls import path
from . import views

app_name = "media"

urlpatterns = [
    path("", views.presentation_list, name="index"),
    path("presentations/", views.presentation_list, name="presentation_list"),
    path("presentation/new/", views.presentation_edit, name="presentation_new"),
    path(
        "presentation/<uuid:uid>/",
        views.presentation_detail,
        name="presentation_detail",
    ),
    path(
        "presentation/<uuid:uid>/edit/",
        views.presentation_edit,
        name="presentation_edit",
    ),
    path("presentation/<uuid:uid>/live/", views.presenter_view, name="presenter_view"),
    # Slide CRUD (AJAX)
    path("presentation/<uuid:uid>/slides/add/", views.slide_add, name="slide_add"),
    path(
        "presentation/<uuid:uid>/slides/<int:slide_id>/edit/",
        views.slide_edit,
        name="slide_edit",
    ),
    path(
        "presentation/<uuid:uid>/slides/<int:slide_id>/delete/",
        views.slide_delete,
        name="slide_delete",
    ),
    path(
        "presentation/<uuid:uid>/slides/reorder/",
        views.slide_reorder,
        name="slide_reorder",
    ),
    path(
        "presentation/<uuid:uid>/go/<int:index>/",
        views.presenter_advance,
        name="presenter_advance",
    ),
    # Bible
    path("bible/fetch/", views.bible_fetch, name="bible_fetch"),
    path("bible/versions/", views.bible_versions, name="bible_versions"),
    # Production schedules
    path(
        "schedule/<int:event_id>/",
        views.production_schedule,
        name="production_schedule",
    ),
    path(
        "schedule/<int:event_id>/checklist/",
        views.schedule_checklist,
        name="schedule_checklist",
    ),
    # Templates
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_edit, name="template_new"),
    path("templates/<int:pk>/edit/", views.template_edit, name="template_edit"),
]
