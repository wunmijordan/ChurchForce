# ChurchForce

> **The workforce management platform built exclusively for churches.**

ChurchForce is a multi-tenant SaaS application that helps churches run their volunteer workforce with the same discipline and visibility as a corporate team — attendance tracking, guest follow-up pipelines, internal chat, unit management, an LMS for onboarding, and automated communications. All scoped per church, all accessible from any device.

---

## Live

| Environment    | URL                                                                 |
| -------------- | ------------------------------------------------------------------- |
| Production app | `https://workforce.church`                                          |
| Platform home  | `https://churchforce.io`                                            |
| Demo           | `https://demo.workforce.church` — Login: `demo_admin` / `Demo@1234` |

---

## Tech Stack

| Layer              | Technology                                 |
| ------------------ | ------------------------------------------ |
| Framework          | Django 5.2                                 |
| Real-time          | Django Channels 4 + Redis (WebSocket)      |
| ASGI server        | Daphne / Uvicorn (dev)                     |
| Database           | PostgreSQL (psycopg v3)                    |
| Cache              | Redis via django-redis                     |
| Storage            | Cloudinary (prod) / local filesystem (dev) |
| Task scheduling    | APScheduler (background thread)            |
| Payments           | Paystack                                   |
| SMS                | Termii                                     |
| Push notifications | Web Push / VAPID                           |
| Frontend           | Tabler UI + Bootstrap 5 + vanilla JS       |
| PDF export         | WeasyPrint + ReportLab                     |
| Deployment         | Railway (Nixpacks)                         |

---

## Architecture

### Multi-tenancy

ChurchForce uses **row-level shared tenancy** for SaaS/trial churches and **dedicated databases** for white-label deployments.

```
Request → TenantMiddleware
           ├─ custom_domain match  → white-label church
           ├─ subdomain match      → SaaS church (slug.workforce.church)
           ├─ path slug            → trial church (workforce.church/slug/)
           └─ localhost fallback   → dev

ChurchContextMiddleware
  request.church           → Church
  request.member           → ChurchMember (lazy)
  request.workforce_member → WorkforceMember (lazy)
  request.permissions      → PermissionResolver (lazy, cached)
```

### Permission System

`PermissionResolver(user, church)` resolves capability strings (e.g. `"guests.view"`, `"attendance.mark"`) from unit roles. Templates use `{% if permissions|can:"guests.view" %}` — no role names in any template.

### Apps

```
apps/
├── tenants/        Church, ChurchSetting, middleware, DB router
├── accounts/       CustomUser, ChurchMember, MemberInvitation
├── permissions/    UnitRole, PermissionResolver
├── units/          ChurchUnit, UnitMembership, ChatRoom, Campus
├── workforce/      WorkforceMember, ChatMessage, AttendanceRecord, Events
├── guests/         GuestEntry, FollowUpReport, GuestStatus pipeline
├── lms/            LMSCourse, LMSModule, LMSEnrollment, Certificate
├── notifications/  Notification, UserSettings, PushSubscription (VAPID)
├── messaging/      Bulk SMS/email via Termii
├── billing/        SubscriptionPlan, ChurchSubscription, Paystack
├── automation/     AutomationRule, AutomationLog
├── bootstrap/      ChurchTemplate seeder, seed_demo command
├── dashboard/      Member + admin dashboard views
├── core/           ChurchOwnedModel base, APScheduler, managers
├── music/          SetList, Song, ChordChart (Spotify + Flat API)
├── media/          Media library
└── services/       ChurchService lookup tables
```

---

## Project Structure

```
churchforce/
├── churchforce/          # Django project package
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py           # ASGI entry point (HTTP + WebSocket)
│   └── wsgi.py
├── apps/                 # All Django apps
├── templates/            # Top-level templates (base.html, registration/)
├── static/               # CSS, JS, sounds, icons
│   ├── js/chat_room.js
│   ├── css/custom.css
│   └── vendor/tabler/
├── scripts/
│   └── reset_dev_db.sh   # Local DB reset utility
├── manage.py
├── requirements.txt
├── railway.json
├── sw.js                 # Service worker (PWA)
├── env.dev.txt           # Dev env template → copy to .env
└── MIGRATION_PLAN.md     # Full migration and testing guide
```

---

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+

### Setup

```bash
# 1. Clone and install
git clone https://github.com/yourhandle/churchforce.git
cd churchforce
pip install -r requirements.txt

# 2. Create your .env
cp env.dev.txt .env
# Edit .env — fill in DB_NAME, DB_USER, DB_PASSWORD at minimum

# 3. Reset the database (drop → migrate → bootstrap)
bash scripts/reset_dev_db.sh

# 4. Start the server (uvicorn + APScheduler in one command)
python manage.py devserver
```

Browse to `http://localhost:8000`. Django admin at `/admin/`.

### Running services individually

```bash
# ASGI server (HTTP + WebSocket)
uvicorn churchforce.asgi:application --reload --port 8000

# Background scheduler
python manage.py start_scheduler

# Redis (if not running as a service)
redis-server
```

---

## Environment Variables

See `env.dev.txt` for the full development template and `env.production.txt` for the production checklist.

| Variable                          | Required  | Description                                |
| --------------------------------- | --------- | ------------------------------------------ |
| `SECRET_KEY`                      | ✅        | Django secret key                          |
| `DEBUG`                           | ✅        | `True` in dev, `False` in prod             |
| `DJANGO_ENV`                      | ✅        | `development` or `production`              |
| `DATABASE_URL`                    | Prod only | Full postgres URL (prod)                   |
| `DB_NAME/USER/PASSWORD/HOST/PORT` | Dev only  | Split DB vars (dev)                        |
| `REDIS_URL`                       | ✅        | `redis://` (dev) or `rediss://` (prod TLS) |
| `CLOUDINARY_*`                    | ✅        | Cloud media storage                        |
| `VAPID_PUBLIC_KEY`                | ✅        | Web Push (push notifications)              |
| `PAYSTACK_SECRET_KEY`             | Billing   | Payment processing                         |
| `TERMII_API_KEY`                  | SMS       | SMS delivery                               |
| `DEMO_SUBDOMAIN`                  | Optional  | Enables nightly demo reset (`demo`)        |

---

## Deployment (Railway)

```bash
# railway.json handles build and start automatically
# Set all production env vars in Railway dashboard

# After first deploy — seed the database
railway run python manage.py migrate --noinput
railway run python manage.py collectstatic --noinput
railway run python manage.py createsuperuser
railway run python manage.py seed_demo --subdomain demo  # optional
```

The `railway.json` starts two processes: `web` (gunicorn + uvicorn worker) and `scheduler` (APScheduler).

---

## White-label Deployment

1. Create a `Church` record in Django admin with `custom_domain = "app.theirclient.com"`
2. Client points their DNS `CNAME app.theirclient.com → your-app.up.railway.app`
3. Upload their logo to `church.logo`
4. Set `church.primary_color` and optionally `church.hide_platform_credit = True`
5. For dedicated DB: add `WL_DB_URL_<SLUG_UPPERCASE>=postgres://...` to env

---

## Demo Seeding

```bash
# Seed once
python manage.py seed_demo --subdomain demo

# Reset and reseed
python manage.py seed_demo --subdomain demo --reset
```

APScheduler runs the reset automatically at 3 AM when `DEMO_SUBDOMAIN` is set.

---

## Testing

```bash
python manage.py test apps.guests.tests
python manage.py test apps.tenants.tests
python manage.py test apps.billing.tests
python manage.py test   # all tests
```

---

## Built by

[Wùnmí™ Jordan](https://www.linkedin.com/in/wunmijordan/)
