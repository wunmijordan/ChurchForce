from django.urls import path
from . import views

app_name = "music"

urlpatterns = [
    # Library
    path("", views.track_library, name="index"),
    path("library/", views.track_library, name="library"),
    path("library/<slug:unit_slug>/", views.track_library, name="unit_library"),
    path("track/add/", views.track_edit, name="track_add"),
    path("track/quick-create/", views.quick_create_track, name="track_quick_create"),
    path("track/<uuid:uid>/", views.track_detail, name="track_detail"),
    path("track/<uuid:uid>/edit/", views.track_edit, name="track_edit"),
    path("track/<uuid:uid>/mixer/", views.mixer_view, name="mixer"),
    path(
        "track/<uuid:uid>/mixer/analyze/",
        views.mixer_ai_analyze,
        name="mixer_ai_analyze",
    ),
    path(
        "track/<uuid:uid>/mixer/save/", views.mixer_save_state, name="mixer_save_state"
    ),
    # Sections (AJAX)
    path(
        "track/<uuid:track_uid>/sections/save/", views.section_save, name="section_save"
    ),
    path(
        "track/<uuid:track_uid>/sections/<int:section_id>/delete/",
        views.section_delete,
        name="section_delete",
    ),
    path(
        "track/<uuid:track_uid>/ai-structure/",
        views.section_ai_structure,
        name="ai_structure",
    ),
    # Chord charts (AJAX)
    path("track/<uuid:track_uid>/chart/save/", views.chart_save, name="chart_save"),
    # Setlists
    path("setlists/", views.setlist_list, name="setlist_list"),
    path("setlists/<slug:unit_slug>/", views.setlist_list, name="unit_setlists"),
    path("setlist/new/", views.setlist_edit, name="setlist_new"),
    path("setlist/<uuid:uid>/", views.setlist_detail, name="setlist_detail"),
    path("setlist/<uuid:uid>/edit/", views.setlist_edit, name="setlist_edit"),
    path("setlist/<uuid:uid>/my/", views.my_setlist_view, name="my_setlist"),
    path(
        "setlist/<uuid:uid>/add-song/", views.setlist_add_song, name="setlist_add_song"
    ),
    path(
        "setlist/<uuid:uid>/remove/<int:song_id>/",
        views.setlist_remove_song,
        name="setlist_remove_song",
    ),
    path("setlist/<uuid:uid>/reorder/", views.setlist_reorder, name="setlist_reorder"),
    path(
        "setlist/<uuid:uid>/finalize/", views.setlist_finalize, name="setlist_finalize"
    ),
    path(
        "setlist/<uuid:uid>/unfinalize/",
        views.setlist_unfinalize,
        name="setlist_unfinalize",
    ),
    # Rehearsals
    path("rehearsals/", views.rehearsal_board, name="rehearsal_board"),
    path("rehearsals/<slug:unit_slug>/", views.rehearsal_board, name="unit_rehearsals"),
    path("rehearsal/<uuid:uid>/", views.rehearsal_detail, name="rehearsal_detail"),
    path(
        "rehearsal/<uuid:uid>/recording/",
        views.rehearsal_upload_recording,
        name="rehearsal_recording_upload",
    ),
    # Stem management
    path("track/<uuid:uid>/stems/", views.stem_upload, name="stem_upload"),
    path(
        "track/<uuid:uid>/stems/<int:stem_id>/delete/",
        views.stem_delete,
        name="stem_delete",
    ),
    path(
        "track/<uuid:uid>/stems/preset/",
        views.stem_save_preset,
        name="stem_save_preset",
    ),
]
