# ChurchForce — Clean-Slate Migration & Dev Testing Guide

> No legacy data to preserve. This guide takes you from a blank Postgres database
> to a fully seeded, running ChurchForce instance in dev.

---

## 0. Prerequisites

```bash
python --version   # 3.11+
psql --version     # 14+
redis-cli ping     # PONG
pip install -r requirements.txt --break-system-packages
```

---

## 1. Environment

```bash
cp .env.example .env
```

Minimum for dev:

```env
SECRET_KEY=any-random-50-char-string
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgres://churchforce:password@localhost:5432/churchforce_dev
REDIS_URL=redis://localhost:6379/0
CLOUDINARY_CLOUD_NAME=your_cloud
CLOUDINARY_API_KEY=your_key
CLOUDINARY_API_SECRET=your_secret
# VAPID: leave blank for dev — push won't work but app will
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_ADMIN_EMAIL=admin@localhost
```

---

## 2. Create the database

```bash
psql -U postgres -c "CREATE USER churchforce WITH PASSWORD 'password';"
psql -U postgres -c "CREATE DATABASE churchforce_dev OWNER churchforce;"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE churchforce_dev TO churchforce;"
```

---

## 3. Migrations (clean slate)

```bash
# Wipe any previous migration files
find apps -path "*/migrations/0*.py" -delete

# Recreate fresh
python manage.py makemigrations \
    tenants accounts units lms workforce guests \
    notifications messaging billing services \
    music media automation permissions dashboard core

python manage.py migrate
```

If any app raises `CommandError: App 'X' does not have migrations`:
```bash
mkdir -p apps/X/migrations && touch apps/X/migrations/__init__.py
python manage.py makemigrations X
```

---

## 4. Bootstrap superuser + church

```bash
python manage.py createsuperuser
# username=admin  email=admin@localhost  password=<anything>
```

Then seed the church via shell:

```bash
python manage.py shell
```

```python
from tenants.models import Church, ChurchSetting
from billing.models import SubscriptionPlan
from accounts.models import CustomUser, ChurchMember
from bootstrap.services import seed_church      # apps/bootstrap/services.py
from units.models import ChurchUnit, ChatRoom

user = CustomUser.objects.get(username='admin')

plan = SubscriptionPlan.objects.create(
    name='Founders', price_ngn=0, price_usd=0,
    max_members=500, campus_limit=3, is_active=True,
)
church = Church.objects.create(
    name='My Church', subdomain='localhost',
    admin=user, plan=plan,
)
ChurchSetting.objects.get_or_create(church=church)
seed_church(church)   # seeds units, LMS induction course, campus

# ChurchMember for the superuser (required by ChurchMiddleware)
member = ChurchMember.objects.create(
    church=church, user=user, is_active=True, is_admin=True,
)

# Ensure every unit has a default ChatRoom (required by chat views)
for unit in ChurchUnit.objects.filter(church=church):
    ChatRoom.objects.get_or_create(
        church=church, unit=unit,
        defaults={'name': unit.name, 'is_default': True, 'is_active': True},
    )

print('Bootstrap complete:', church, '| Member:', member)
```

---

## 5. Start services

Open four terminals:

```bash
# T1 — Daphne (HTTP + WebSocket — use this port for everything)
daphne -b 0.0.0.0 -p 8001 gforceapp.asgi:application

# T2 — Redis
redis-server

# T3 — APScheduler (background jobs: reminders, birthday msgs, etc.)
python manage.py start_scheduler

# T4 — Optional: plain runserver for fast template iteration (no WS)
python manage.py runserver 0.0.0.0:8000
```

Browse to **http://localhost:8001** — log in with the superuser you created.

---

## 6. Verify each subsystem

| What | URL | Expected |
|---|---|---|
| Django admin | `/admin/` | Church, Units, Members, Plans visible |
| Member dashboard | `/dashboard/` | Clock, calendar, attendance clock-in button |
| Admin dashboard | `/dashboard/admin/` | User selector dropdown, attendance table |
| Guest list | `/guests/` | Empty table, Add Guest button visible |
| Guest form | `/guests/create/` | Form with social media fields |
| Follow-up report | `/guests/<id>/report/` | Contact tracking section with attachment |
| Chat | `/chat/` | WebSocket connects (check DevTools → Network → WS) |
| LMS | `/lms/` | Induction course seeded by bootstrap |
| Notifications settings | `/notifications/settings/` | No JS console errors |
| Billing | `/billing/pricing/` | Founders plan card visible |

**WebSocket check**: DevTools → Network → WS tab → send a message →
it should appear in the room without page reload. The connection URL should
be `wss://localhost:8001/ws/chat/?room_id=<N>`.

---

## 7. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `NoReverseMatch: 'dashboard'` | App not namespaced in urls | `path("", include("dashboard.urls", namespace="dashboard"))` in `gforceapp/urls.py` |
| `request.member is None` on every view | Middleware not registered | Add `"tenants.middleware.ChurchMemberMiddleware"` to `MIDDLEWARE` in settings, after `AuthenticationMiddleware` |
| WebSocket `403 Forbidden` | Missing `CHANNEL_LAYERS` | Add to settings: `CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [env("REDIS_URL")]}}}` |
| `VAPID key: {{ VAPID_PUBLIC_KEY\|escapejs }}` in console | Context processor didn't fire (unauthenticated page or missing `request.member`) | Expected on login page — harmless. Fixed in `base.html` via `<meta>` fallback |
| `SyntaxError: Unexpected token '{'` in console | `vibrationEnabled` rendered empty | Ensure you deployed the latest `base.html` with the safe `{% if notification_settings %}` guard |
| `ChatRoom.DoesNotExist` in chat view | No default room for unit | Run the ChatRoom creation loop from step 4 |
| `ModuleNotFoundError: No module named 'apps.X'` | `sys.path` not set | `sys.path.insert(0, BASE_DIR / "apps")` must be at **module level** in `manage.py`, not inside `main()` |
| `OperationalError: no such table` | Migration not applied | `python manage.py migrate` |
| Edit/Delete buttons don't appear | `data-sender-id` missing on bubble | Confirm `bubble.dataset.senderId = data.sender_id` is in `appendMessage()` in `chat_room.js` |
| `rooms_json` raises `NameError` in template | `rooms_json` not in view context | Confirm the `workforce_views.py` patch was applied (search for `rooms_json` in the file) |

---

## 8. Reset dev state

```bash
psql -U postgres -c "DROP DATABASE churchforce_dev; CREATE DATABASE churchforce_dev OWNER churchforce;"
find apps -path "*/migrations/0*.py" -delete
python manage.py makemigrations tenants accounts units lms workforce guests \
    notifications messaging billing services music media automation \
    permissions dashboard core
python manage.py migrate
python manage.py createsuperuser
# then re-run the shell bootstrap from step 4
```

---

## 9. Minimum tests before staging

```bash
python manage.py test apps.guests.tests
python manage.py test apps.tenants.tests
python manage.py test apps.billing.tests
```

Priority test cases to write:
1. `PermissionResolver` returns correct caps for each church role
2. `FollowUpReport.save()` with `contact_answered=True` advances guest to In-Contact
3. `GuestEntry.custom_id` is church-scoped (no cross-church collision)
4. Chat consumer accepts WS connection and echoes `chat_message`
5. `can_add_campus()` respects plan `campus_limit`
6. LMS `_try_promote_trainee()` promotes on passing induction score
7. Attendance `mark_btn` POST creates `AttendanceRecord` with correct status

---

## 10. Template directories to create

These must exist before `collectstatic` and before Django can find the templates:

```bash
mkdir -p apps/dashboard/templates/dashboard
mkdir -p apps/workforce/templates/workforce/partials
mkdir -p apps/lms/templates/lms
mkdir -p apps/units/templates/units
mkdir -p templates/guests/partials
mkdir -p templates/accounts
mkdir -p templates/billing
mkdir -p templates/tenants
```
