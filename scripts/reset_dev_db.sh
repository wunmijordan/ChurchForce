#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ChurchForce — Local dev database reset
# Usage:  bash scripts/reset_dev_db.sh
#
# Reads ALL values directly from your .env file — no separate defaults.
# Your .env must exist at the project root before running this.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌  .env not found at $ENV_FILE"
  echo "    Run:  cp env.dev.txt .env  then edit it first."
  exit 1
fi

# Export every non-comment, non-blank line from .env
set -a
# shellcheck disable=SC1090
source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
set +a

# ── Resolve DB vars from .env ─────────────────────────────────────────────────
# Your .env uses split DB_* vars in development (not DATABASE_URL)
DB_NAME="${DB_NAME:?DB_NAME not set in .env}"
DB_USER="${DB_USER:?DB_USER not set in .env}"
DB_PASS="${DB_PASSWORD:?DB_PASSWORD not set in .env}"   # note: .env uses DB_PASSWORD
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# ── Superuser — prompted interactively so you choose your own credentials ─────
# We don't hardcode these. Django's createsuperuser handles it.
# Pass SU_USERNAME / SU_EMAIL / SU_PASS as env vars to skip the prompts:
#   SU_USERNAME=wunmi SU_EMAIL=me@church.io SU_PASS=secret bash scripts/reset_dev_db.sh
SU_USERNAME="${SU_USERNAME:-}"
SU_EMAIL="${SU_EMAIL:-}"
SU_PASS="${SU_PASS:-}"

MANAGE="python manage.py"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║     ChurchForce — Dev DB Reset                      ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  DB:   $DB_NAME @ $DB_HOST:$DB_PORT (user: $DB_USER)"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Drop + recreate the database ──────────────────────────────────────────
# Uses DB_USER from .env (likely 'postgres' locally) — no new role created.
echo "▶  Dropping $DB_NAME …"
psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" \
  -c "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null || true

echo "▶  Creating $DB_NAME …"
psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" \
  -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
echo "✓  Database ready"

# ── 2. Wipe migration files ───────────────────────────────────────────────────
echo "▶  Wiping migration files…"
find "$PROJECT_ROOT/apps" -path "*/migrations/0*.py" -delete
echo "✓  Migration files deleted"

# ── 3. Make migrations ────────────────────────────────────────────────────────
echo "▶  makemigrations…"
cd "$PROJECT_ROOT"
$MANAGE makemigrations \
    tenants accounts units lms workforce guests \
    notifications messaging billing services \
    music media automation permissions dashboard core
echo "✓  Migrations created"

# ── 4. Migrate ────────────────────────────────────────────────────────────────
echo "▶  migrate…"
$MANAGE migrate --noinput
echo "✓  Schema applied"

# ── 5. Superuser ─────────────────────────────────────────────────────────────
echo ""
if [[ -n "$SU_USERNAME" && -n "$SU_EMAIL" && -n "$SU_PASS" ]]; then
  # Non-interactive: all three passed as env vars
  echo "▶  Creating superuser: $SU_USERNAME (non-interactive)"
  DJANGO_SUPERUSER_PASSWORD="$SU_PASS" \
  $MANAGE createsuperuser \
      --noinput \
      --username "$SU_USERNAME" \
      --email "$SU_EMAIL" 2>/dev/null \
  && echo "✓  Superuser created: $SU_USERNAME / $SU_PASS" \
  || echo "   (superuser already exists — skipped)"
else
  # Interactive: Django prompts you
  echo "▶  Creating superuser (you will be prompted)…"
  $MANAGE createsuperuser
fi

# ── 6. Bootstrap ─────────────────────────────────────────────────────────────
echo ""
echo "▶  Bootstrapping church, plan, member, units and chat rooms…"

# Read the superuser username back — either from env or ask
if [[ -z "$SU_USERNAME" ]]; then
  read -rp "   Enter the superuser username you just created: " SU_USERNAME
fi

CHURCH_NAME="${CHURCH_NAME:-My Church}"

$MANAGE shell << PYEOF
from tenants.models import Church, ChurchSetting
from billing.models import SubscriptionPlan
from accounts.models import CustomUser, ChurchMember
from units.models import ChurchUnit, ChatRoom

try:
    from bootstrap.services import seed_church
    has_bootstrap = True
except ImportError:
    has_bootstrap = False

user = CustomUser.objects.get(username='${SU_USERNAME}')

plan, _ = SubscriptionPlan.objects.get_or_create(
    name='Founders',
    defaults=dict(price_ngn=0, price_usd=0, max_members=500,
                  campus_limit=10, is_active=True),
)

church, created = Church.objects.get_or_create(
    subdomain='localhost',
    defaults=dict(
        name='${CHURCH_NAME}',
        slug='my-church',
        admin=user,
        plan=plan,
        latitude=6.5244,
        longitude=3.3792,
        timezone='Africa/Lagos',
    ),
)
print(f"  {'Church created' if created else 'Church already exists'}: {church}")

ChurchSetting.objects.get_or_create(church=church)

if has_bootstrap:
    seed_church(church)
    print('  seed_church() ran OK')

member, _ = ChurchMember.objects.get_or_create(
    church=church, user=user,
    defaults=dict(is_active=True, is_admin=True),
)
print(f'  Member: {member}')

for unit in ChurchUnit.objects.filter(church=church):
    room, c = ChatRoom.objects.get_or_create(
        church=church, unit=unit,
        defaults=dict(name=unit.name, is_default=True, is_active=True),
    )
    if c:
        print(f'  ChatRoom created: {room.name}')
PYEOF

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅  Reset complete!                                 ║"
echo "║                                                      ║"
echo "║  Start server:  python manage.py devserver           ║"
echo "║  Admin:         http://localhost:8000/admin/         ║"
echo "║  Login:         $SU_USERNAME                         "
echo "╚══════════════════════════════════════════════════════╝"
echo ""