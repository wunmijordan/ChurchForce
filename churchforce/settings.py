import os
import sys
from pathlib import Path
import environ
import cloudinary
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

environ.Env.read_env(BASE_DIR / ".env")

ENVIRONMENT = env("ENVIRONMENT", default="development")
DEBUG = env.bool("DEBUG", default=(ENVIRONMENT != "production"))
SECRET_KEY = env("SECRET_KEY", default="django-insecure-placeholder-key")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"] if DEBUG else [])

INSTALLED_APPS = [
    # Django default
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "cloudinary",
    "cloudinary_storage",
    "widget_tweaks",
    "channels",
    "django_htmx",
    # Foundation apps (no cross-app dependencies)
    "bootstrap.apps.BootstrapConfig",
    "founder.apps.FounderConfig",  # Operator control panel at founder.workforce.church
    "marketing.apps.MarketingConfig",  # Public marketing site at churchforce.io
    "tenants.apps.TenantsConfig",
    "core.apps.CoreConfig",
    # Domain apps
    "accounts.apps.AccountsConfig",
    "permissions.apps.PermissionsConfig",
    "units.apps.UnitsConfig",
    "services.apps.ServicesConfig",
    # Feature apps
    "workforce.apps.WorkforceConfig",
    "guests.apps.GuestsConfig",
    "notifications.apps.NotificationsConfig",
    "messaging.apps.MessagingConfig",
    "billing.apps.BillingConfig",
    "automation.apps.AutomationConfig",
    "dashboard.apps.DashboardConfig",
    "feeds.apps.FeedsConfig",
    "lms.apps.LMSConfig",
    # Unit-specific feature apps
    "music.apps.MusicConfig",
    "media.apps.MediaConfig",  # NOTE: 'media' conflicts with Django's
    # MEDIA_ROOT concept - ensure app is
    # registered as 'media' not 'django.media'
    "children.apps.ChildrenConfig",
    "youth.apps.YouthConfig",
    "teenagers.apps.TeenagersConfig",
    # Bible feature
    "bible.apps.BibleConfig",
]

# ── Bible Settings ─────────────────────────────────────────────────────────────
BIBLE_DEFAULT_TRANSLATION = "BSB"
BIBLE_API_BASE_URL = "https://bible.helloao.org/api"
BIBLE_CHAPTER_CACHE_DAYS = 30  # Days before a cached chapter is considered stale
YOUVERSION_APP_KEY = env("YOUVERSION_APP_KEY", default="")
YOUVERSION_BASE_URL = "https://api.youversion.com/v1"
YOUVERSION_CLIENT_ID = env("YOUVERSION_CLIENT_ID", default="")
YOUVERSION_CLIENT_SECRET = env("YOUVERSION_CLIENT_SECRET", default="")
SITE_URL = env("SITE_URL", default="http://workforce.localhost:8000")
APIBIBLE_API_KEY = env("APIBIBLE_API_KEY", default="")
APIBIBLE_BASE_URL = "https://rest.api.bible/v1/bibles"

if DEBUG:
    INSTALLED_APPS.append("debug_toolbar")

# =========================
# MIDDLEWARE
# =========================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "tenants.middleware.DynamicCSRFMiddleware",  # dynamic CSRF_TRUSTED_ORIGINS based on church domain
    "tenants.middleware.TenantMiddleware",  # resolves church from host/path
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "tenants.middleware.ChurchContextMiddleware",  # attaches member/permissions
    # NOTE: notifications.middleware.CurrentUserMiddleware REMOVED â€”
    # ChurchContextMiddleware calls set_current_request() instead.
]

if DEBUG:
    MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")

# =========================
# URLS & TEMPLATES
# =========================
ROOT_URLCONF = "churchforce.urls"
WSGI_APPLICATION = "churchforce.wsgi.application"
ASGI_APPLICATION = "churchforce.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Tenant context (church, subscription, member, permissions)
                "tenants.context_processors.church_context",
                "tenants.context_processors.subscription_status",
                # Notifications
                "notifications.context_processors.unread_notifications",
                "notifications.context_processors.user_settings",
                "notifications.context_processors.vapid_keys",
                # Messaging modal
                "messaging.context_processors.bulk_message_form",
                # Guest quick-access for superusers
                "guests.context_processors.superuser_guests",
                "lms.context_processors.lms_nav_counts",
            ],
        },
    },
]

# =========================
# DATABASE
# =========================
# Hybrid multi-tenant strategy:
#   'default'    â†’ shared PostgreSQL for trial + SaaS tenants
#   'wl_<slug>'  â†’ dedicated PostgreSQL per white-label tenant
#
# White-label DBs: add WL_DB_URL_<SLUG_UPPERCASE> to .env
# e.g. WL_DB_URL_GRACE_CHAPEL=postgres://...
# Slug restored: GRACE_CHAPEL â†’ grace-chapel â†’ alias wl_grace-chapel

if ENVIRONMENT == "production":
    DATABASES = {
        "default": dj_database_url.config(
            default=env("DATABASE_URL"),
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    try:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": env("DB_NAME"),
                "USER": env("DB_USER"),
                "PASSWORD": env("DB_PASSWORD"),
                "HOST": env("DB_HOST"),
                "PORT": env("DB_PORT"),
            }
        }
    except Exception:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }

# Dynamically register white-label databases
_wl_prefix = "WL_DB_URL_"
for _key, _url in os.environ.items():
    if _key.startswith(_wl_prefix):
        _slug = _key[len(_wl_prefix) :].lower().replace("_", "-")
        _alias = f"wl_{_slug}"
        DATABASES[_alias] = (
            dj_database_url.config(default=_url, conn_max_age=600, ssl_require=True)
            if ENVIRONMENT == "production"
            else dj_database_url.parse(_url)
        )

DATABASE_ROUTERS = ["tenants.db_router.TenantDatabaseRouter"]

# =========================
# REDIS & CHANNELS
# =========================
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
            "capacity": 1000,
            "expiry": 60,
        },
    }
}

# =========================
# CACHING
# =========================
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # rediss:// (TLS) is used in hosted production Redis â€” disable
            # strict cert checks so Railway/Upstash/Redis Cloud all work.
            **(
                {"CONNECTION_POOL_KWARGS": {"ssl_cert_reqs": None}}
                if REDIS_URL.startswith("rediss://")
                else {}
            ),
        },
        "KEY_PREFIX": "churchforce",
    }
}

# =========================
# STATIC & MEDIA
# =========================
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_URL = "/static/"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"  # renamed from 'media' to avoid
# clash with the 'media' app

# =========================
# CLOUDINARY
# =========================
cloudinary.config(
    cloud_name=env("CLOUDINARY_CLOUD_NAME", default=""),
    api_key=env("CLOUDINARY_API_KEY", default=""),
    api_secret=env("CLOUDINARY_API_SECRET", default=""),
)

# ── File storage ──────────────────────────────────────────────────────────────
# Dev:  FileSystemStorage → files written to MEDIA_ROOT (mediafiles/)
#       Served at MEDIA_URL (/media/) via Django's dev server.
#       CloudinaryField model fields store a relative path instead of a
#       Cloudinary public_id; core.storage.smart_url() handles both cases.
#
# Prod: MediaCloudinaryStorage → files uploaded directly to Cloudinary.
#       MEDIA_ROOT is unused; all URLs come from Cloudinary CDN.
if not DEBUG:
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
else:
    DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": env("CLOUDINARY_CLOUD_NAME", default=""),
    "API_KEY": env("CLOUDINARY_API_KEY", default=""),
    "API_SECRET": env("CLOUDINARY_API_SECRET", default=""),
}

# =========================
# AUTHENTICATION & SESSIONS
# =========================
AUTH_USER_MODEL = "accounts.CustomUser"
LOGIN_REDIRECT_URL = "/post-login/"
LOGOUT_REDIRECT_URL = "accounts/login/"

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 28  # 28 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CONN_MAX_AGE = 600

# =========================
# PASSWORD VALIDATORS
# =========================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =========================
# LOCALISATION
# =========================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"  # server default â€” each church has its own timezone
USE_I18N = True
USE_TZ = True

# =========================
# DEFAULT AUTO FIELD
# =========================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =========================
# WEBSOCKET SCHEME
# =========================
WS_SCHEME = "wss://" if ENVIRONMENT == "production" else "ws://"

# =========================
# CSRF & SECURITY
# =========================
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["https://localhost"],
)

CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG

if DEBUG:
    CSRF_TRUSTED_ORIGINS += ["http://127.0.0.1:8000", "http://localhost:8000"]

# =========================
# VAPID (Web Push)
# =========================
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_ADMIN_EMAIL = env("VAPID_ADMIN_EMAIL", default="admin@churchforce.io")

# =========================
# DEMO ENVIRONMENT
# =========================
# Set DEMO_SUBDOMAIN to enable the demo church nightly reset (APScheduler job).
# The seed_demo management command creates the church at this subdomain.
# Leave blank to disable demo seeding entirely.
# ── Demo church ──────────────────────────────────────────────────────────────
# Set DEMO_SUBDOMAIN in .env.prod to enable the nightly auto-reset.
# Leave blank to disable the demo feature entirely.
DEMO_SUBDOMAIN = env("DEMO_SUBDOMAIN", default="")
DEMO_PASSWORD = env("DEMO_PASSWORD", default="Demo@1234")

# =========================
# PAYSTACK
# =========================
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", default="")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", default="")

# =========================
# SMS
# =========================
SMS_PROVIDER = env("SMS_PROVIDER", default="stub")
TERMII_API_KEY = env("TERMII_API_KEY", default="")
TERMII_SENDER_ID = env("TERMII_SENDER_ID", default="ChurchForce")


# =========================
# DOMAINS
# =========================
def _split_hosts(raw_value):
    if isinstance(raw_value, (list, tuple)):
        return [str(v).strip() for v in raw_value if str(v).strip()]
    return [part.strip() for part in str(raw_value).split(",") if part.strip()]


_marketing_default = (
    "churchforce.io" if ENVIRONMENT == "production" else "localhost:8000"
)
_app_default = "workforce.church" if ENVIRONMENT == "production" else "localhost:8000"

MARKETING_DOMAINS = _split_hosts(env("MARKETING_DOMAIN", default=_marketing_default))
APP_DOMAINS = _split_hosts(env("APP_DOMAIN", default=_app_default))

# Backwards-compatible single-host aliases
MARKETING_DOMAIN = MARKETING_DOMAINS[0]
APP_DOMAIN = APP_DOMAINS[0]

FOUNDER_SUBDOMAIN = env(
    "FOUNDER_SUBDOMAIN", default="founder"
)  # Subdomain for the superuser control panel (founder.workforce.church)
# =========================
# EMAIL (Termii for SMS-to-email in Nigeria, plus console backend for local dev)
# =========================
EMAIL_BACKEND = (
    "churchforce.email_backends.TermiiEmailBackend"
    if SMS_PROVIDER == "termii"
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = "ChurchForce <hello@churchforce.io>"
SERVER_EMAIL = "admin@churchforce.io"  # For error reports
AUTHENTICATION_BACKENDS = [
    "accounts.auth_backends.ChurchUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]


if not DEBUG:
    if SMS_PROVIDER == "termii":
        EMAIL_BACKEND = "churchforce.email_backends.TermiiEmailBackend"
    else:
        # Allows you to set a custom production backend in .env (e.g. Anymail)
        EMAIL_BACKEND = env.str(
            "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
        )
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# =========================
# MUSIC API INTEGRATIONS
# =========================
SPOTIFY_CLIENT_ID = env("SPOTIFY_CLIENT_ID", default="")
SPOTIFY_CLIENT_SECRET = env("SPOTIFY_CLIENT_SECRET", default="")

# YouTube Data API v3 — track enrichment (search → videoId)
# https://console.cloud.google.com → enable "YouTube Data API v3"
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY", default="")

# SoundCloud public API — track enrichment (search → stream URL)
# https://developers.soundcloud.com — register a free app for client_id
SOUNDCLOUD_CLIENT_ID = env("SOUNDCLOUD_CLIENT_ID", default="")
FLAT_API_TOKEN = env("FLAT_API_TOKEN", default="")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
AI_PROVIDER = env("AI_PROVIDER", default=("ollama" if DEBUG else "anthropic"))
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://127.0.0.1:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", default="qwen2.5-coder:7b")
# Used by core.ai_skills for:
#   - Daily Bible verse (welcome screen)
#   - AI support bot (ws/support/)
#   - Lyrics auto-structuring (music module)

# =========================
# PWA CONFIGURATION
# =========================
PWA_APP_NAME = env("PWA_APP_NAME", default="ChurchForce")
PWA_APP_SHORT_NAME = env("PWA_SHORT_NAME", default="ChurchForce")
PWA_APP_DESCRIPTION = "Workforce Hub for your church"
PWA_APP_THEME_COLOR = "#2e303e"
PWA_APP_BACKGROUND_COLOR = "#2e303e"
PWA_APP_DISPLAY = "standalone"
PWA_APP_SCOPE = "/"
PWA_APP_START_URL = "/"
PWA_APP_ORIENTATION = "portrait"
PWA_APP_STATUS_BAR_COLOR = "default"
PWA_APP_ICONS = [
    {"src": "/static/images/icons/icon-192x192.png", "sizes": "192x192"},
    {"src": "/static/images/icons/icon-512x512.png", "sizes": "512x512"},
]
PWA_APP_ICONS_APPLE = PWA_APP_ICONS
PWA_APP_DIR = "ltr"
PWA_APP_LANG = "en-US"

# =========================
# LOGGING
# =========================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
        "billing": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "notifications": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "messaging": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "automation": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "music": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
