from django.urls import path
from bible import views

app_name = "bible"

urlpatterns = [
    # ── Member-facing ──────────────────────────────────────────────────────
    path("", views.bible_dashboard, name="dashboard"),
    # ── Admin: Reading Plans ───────────────────────────────────────────────
    path("admin/plans/", views.admin_plan_list, name="admin_plan_list"),
    path("admin/plans/create/", views.admin_plan_create, name="admin_plan_create"),
    path(
        "admin/plans/<int:plan_id>/edit/", views.admin_plan_edit, name="admin_plan_edit"
    ),
    path(
        "admin/plans/<int:plan_id>/delete/",
        views.admin_plan_delete,
        name="admin_plan_delete",
    ),
    # ── Admin: Memory Verses ───────────────────────────────────────────────
    path(
        "admin/memory-verses/",
        views.admin_memory_verse_list,
        name="admin_memory_verse_list",
    ),
    path(
        "admin/memory-verses/create/",
        views.admin_memory_verse_create,
        name="admin_memory_verse_create",
    ),
    path(
        "admin/memory-verses/<int:verse_id>/edit/",
        views.admin_memory_verse_edit,
        name="admin_memory_verse_edit",
    ),
    path(
        "admin/memory-verses/<int:verse_id>/delete/",
        views.admin_memory_verse_delete,
        name="admin_memory_verse_delete",
    ),
    # ── Admin: Study References ────────────────────────────────────────────
    path("admin/study-refs/", views.admin_study_ref_list, name="admin_study_ref_list"),
    path(
        "admin/study-refs/create/",
        views.admin_study_ref_create,
        name="admin_study_ref_create",
    ),
    path(
        "admin/study-refs/<int:study_id>/edit/",
        views.admin_study_ref_edit,
        name="admin_study_ref_edit",
    ),
    path(
        "admin/study-refs/<int:study_id>/delete/",
        views.admin_study_ref_delete,
        name="admin_study_ref_delete",
    ),
    # ── AJAX: Member Progress ──────────────────────────────────────────────
    path(
        "ajax/entry/<int:entry_id>/complete/",
        views.mark_entry_complete,
        name="mark_entry_complete",
    ),
    path(
        "ajax/memory/<int:verse_id>/complete/",
        views.mark_memory_complete,
        name="mark_memory_complete",
    ),
    path(
        "ajax/study/<int:study_id>/complete/",
        views.mark_study_complete,
        name="mark_study_complete",
    ),
    # ── AJAX: Share ────────────────────────────────────────────────────────
    path("ajax/share/", views.share_to_feed, name="share_to_feed"),
    # ── AJAX: Search & Fetch ───────────────────────────────────────────────
    path("api/search/", views.bible_search, name="search"),
    path("api/passage/", views.fetch_passage, name="fetch_passage"),
    path("api/books/", views.book_list, name="book_list"),
    path("api/chapter-verses/", views.chapter_verses, name="chapter_verses"),
    path("api/translations/", views.translations_list, name="translations_list"),
    path("api/debug/", views.bible_debug, name="debug"),
    # ── Bible Reader ───────────────────────────────────────────────────────
    path("reader/", views.bible_reader, name="bible_reader"),
    # ── AJAX: Highlights ──────────────────────────────────────────────────
    path("ajax/highlight/save/", views.ajax_save_highlight, name="ajax_save_highlight"),
    path(
        "ajax/highlight/load/", views.ajax_load_highlights, name="ajax_load_highlights"
    ),
    # ── AJAX: Plan Discussion ─────────────────────────────────────────────
    path(
        "ajax/discussion/",
        views.ajax_create_plan_discussion,
        name="ajax_create_plan_discussion",
    ),
    path(
        "ajax/discussion/entry/<int:entry_id>/",
        views.ajax_list_plan_discussions,
        name="ajax_list_plan_discussions",
    ),
    # ── AJAX: Plan Entries by date ────────────────────────────────────────
    path(
        "ajax/plan/<int:plan_id>/entries/",
        views.ajax_plan_entries,
        name="ajax_plan_entries",
    ),
    # ── Admin: Member Bible Progress ──────────────────────────────────────
    path(
        "admin/member/<int:member_id>/progress/",
        views.admin_member_bible_progress,
        name="admin_member_bible_progress",
    ),
    # ── YouVersion OAuth (Phase 2) ─────────────────────────────────────────
    # Step 1 — member clicks "Sign in with YouVersion" → redirect to YV
    path("youversion/login/", views.youversion_oauth_login, name="youversion_login"),
    # Step 2 — YouVersion redirects back here with ?code=&state=
    path(
        "youversion/callback/",
        views.youversion_oauth_callback,
        name="youversion_callback",
    ),
    # Disconnect
    path("youversion/logout/", views.youversion_oauth_logout, name="youversion_logout"),
    # JSON status — called by reader on load
    path("youversion/status/", views.youversion_oauth_status, name="youversion_status"),
    # On-demand highlight re-sync
    path("youversion/sync/", views.youversion_oauth_sync, name="youversion_sync"),
]
