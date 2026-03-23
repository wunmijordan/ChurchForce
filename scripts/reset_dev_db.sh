#!/usr/bin/env bash
# ChurchForce - Local dev database reset
# Usage:  bash scripts/reset_dev_db.sh
#
# Reads ALL values from your .env file.
# Non-interactive mode (skip all prompts):
#   SU_USERNAME=wunmi SU_EMAIL=me@church.io SU_PASS=secret bash scripts/reset_dev_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

# --- Load .env ----------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE"
  echo "       Run:  cp env.dev.txt .env  then fill in your values."
  exit 1
fi

# Only export clean KEY=VALUE lines - skip comments, blanks, and anything malformed.
# This prevents "command not found" errors from unquoted values with spaces.
while IFS= read -r line; do
  # Skip blank lines and comments
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  # Must contain = and key must be a valid identifier
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
  export "${line?}"
done < "$ENV_FILE"

# --- Resolve DB vars from .env -----------------------------------------
DB_NAME="${DB_NAME:?DB_NAME not set in .env}"
DB_USER="${DB_USER:?DB_USER not set in .env}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# DB_PASSWORD is the key name in .env; expose as PGPASSWORD so psql picks it up
export PGPASSWORD="${DB_PASSWORD:-}"

# --- Superuser (optional) ----------------------------------------------
SU_USERNAME="${SU_USERNAME:-}"
SU_EMAIL="${SU_EMAIL:-}"
SU_PASS="${SU_PASS:-}"
CHURCH_NAME="${CHURCH_NAME:-My Church}"

# Resolve Python - project-local venv always wins over $VIRTUAL_ENV
# because $VIRTUAL_ENV may be stale (pointing to a different project).
if [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
  PYTHON="$PROJECT_ROOT/venv/bin/python"
elif [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
  PYTHON="$VIRTUAL_ENV/bin/python"
else
  echo "ERROR: No virtualenv found in $PROJECT_ROOT/venv/ or $PROJECT_ROOT/.venv/"
  echo "       Create one first:  python3 -m venv venv && source venv/bin/activate"
  echo "       Then install:      pip install -r requirements.txt"
  exit 1
fi

MANAGE="$PYTHON manage.py"
echo "Using Python: $PYTHON"

echo ""
echo "=== ChurchForce - Dev DB Reset ==="
echo "DB: $DB_NAME @ $DB_HOST:$DB_PORT (user: $DB_USER)"
echo ""

# --- 1. Drop and recreate -----------------------------------------------
echo ">> Dropping $DB_NAME ..."
psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" \
  -c "DROP DATABASE IF EXISTS \"$DB_NAME\";" 2>/dev/null || true

echo ">> Creating $DB_NAME ..."
psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" \
  -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\";"
echo "   Done."

# --- 2. Wipe migration files --------------------------------------------
echo ""
echo ">> Wiping migration files ..."
find "$PROJECT_ROOT/apps" -path "*/migrations/0*.py" -delete
echo "   Done."

# --- 3. Make migrations -------------------------------------------------
echo ""
echo ">> Running makemigrations ..."
cd "$PROJECT_ROOT"
$MANAGE makemigrations
echo "   Done."

# --- 4. Migrate ---------------------------------------------------------
echo ""
echo ">> Running migrate ..."
$MANAGE migrate --noinput
echo "   Done."

# --- 5. Superuser -------------------------------------------------------
echo ""
if [ -n "$SU_USERNAME" ] && [ -n "$SU_EMAIL" ] && [ -n "$SU_PASS" ]; then
  echo ">> Creating superuser: $SU_USERNAME ..."
  DJANGO_SUPERUSER_PASSWORD="$SU_PASS" \
    $MANAGE createsuperuser \
      --noinput \
      --username "$SU_USERNAME" \
      --email "$SU_EMAIL" 2>/dev/null \
    && echo "   Created: $SU_USERNAME / $SU_PASS" \
    || echo "   Already exists - skipped."
else
  echo ">> Creating superuser (you will be prompted) ..."
  $MANAGE createsuperuser
  printf "\n   Enter the username you just created: "
  read -r SU_USERNAME
fi

# --- 6. Bootstrap -------------------------------------------------------
echo ""
echo ">> Bootstrapping church: $CHURCH_NAME ..."

# Pass values as environment variables so the heredoc can stay fully quoted.
# A quoted heredoc delimiter ('PYEOF') prevents ALL shell expansion inside,
# which avoids the "/dev/fd/63: command not found" bash parsing error.
export CF_SU_USERNAME="$SU_USERNAME"
export CF_CHURCH_NAME="$CHURCH_NAME"

$MANAGE shell << 'PYEOF'
import os

su_username  = os.environ["CF_SU_USERNAME"]
church_name  = os.environ.get("CF_CHURCH_NAME", "My Church")

from tenants.models import Church, ChurchSetting
from billing.services import grant_founders_plan
from accounts.models import CustomUser, ChurchMember
from units.models import ChurchUnit, ChatRoom

try:
    from bootstrap.services import seed_church
    has_bootstrap = True
except ImportError:
    has_bootstrap = False

user = CustomUser.objects.get(username=su_username)

church, created = Church.objects.get_or_create(
    subdomain="localhost",
    defaults=dict(
        name=church_name,
        slug="my-church",
        latitude=6.5244,
        longitude=3.3792,
        timezone="Africa/Lagos",
    ),
)
print("  Church {}: {}".format("created" if created else "already exists", church))

grant_founders_plan(church, "white_label")
print("  Founders Plan (white_label) granted.")

ChurchSetting.objects.get_or_create(church=church)

member, _ = ChurchMember.objects.get_or_create(
    church=church,
    user=user,
    defaults=dict(is_active=True, is_admin=True),
)
print("  Member: {}".format(member))

if has_bootstrap:
    seed_church(church, admin_user=user)
    print("  ✓ ChurchForce Bootstrap OK (Units + ChatRooms created)")
PYEOF

echo ""
echo "==================================================="
echo "  Reset complete!"
echo ""
echo "  Start:    python manage.py devserver"
echo "  Admin:    http://localhost:8000/admin/"
echo "  Login:    $SU_USERNAME"
echo "==================================================="
echo ""
