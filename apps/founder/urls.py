from django.urls import path
from . import views

app_name = "founder"

urlpatterns = [
    # ── Core dashboard ─────────────────────────────────────────────────
    path("", views.founder_home, name="home"),
    # ── Church management ──────────────────────────────────────────────
    path("churches/", views.church_list, name="church_list"),
    path("churches/create/", views.church_create, name="church_create"),
    path("churches/<int:pk>/", views.church_detail, name="church_detail"),
    # ── User management ────────────────────────────────────────────────
    path("users/", views.user_list, name="user_list"),
    path("users/<int:pk>/superuser/", views.toggle_superuser, name="toggle_superuser"),
    # ── Impersonation ──────────────────────────────────────────────────
    path("impersonate/<int:pk>/", views.impersonate_user, name="impersonate"),
    path("impersonate/end/", views.impersonate_end, name="impersonate_end"),
    # ── Demo seeding ───────────────────────────────────────────────────
    path("seed-demo/", views.run_seed_demo, name="run_seed_demo"),
    path("seed-music-catalog/", views.run_seed_music_catalog, name="run_seed_music_catalog"),
    # ── Model admin (Django-admin-style CRUD for every registered model)
    path("models/", views.model_registry_index, name="model_registry"),
    path("models/<str:model_key>/", views.model_list, name="model_list"),
    path("models/<str:model_key>/<int:pk>/", views.model_detail, name="model_detail"),
    # ── System health ──────────────────────────────────────────────────
    path("health/", views.system_health, name="health"),
]
