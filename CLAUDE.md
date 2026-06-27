# ChurchForce — Claude Code Instructions

> This file is read automatically on every task. Keep it up to date as the
> project evolves. For the full reference manual, see `docs/skills/SKILL.md`.

---

## What this project is

Multi-tenant Django SaaS for church management. One deployment, many
independent churches (tenants). Every model, query, AI call, and UI element
must respect tenant isolation. When in doubt, ask: "does this accidentally
cross a tenant boundary?"

**Stack:** Django 4.x · PostgreSQL · Redis · Django Channels · Cloudinary ·
WhiteNoise · APScheduler · Tabler UI · vanilla JS · HTMX patterns · PWA

**AI provider:** `AI_PROVIDER=ollama` in dev (local Qwen), `anthropic` in
prod. Never hard-code a model name in a new file — always import `HAIKU` and
`SONNET` from `core.ai_skills`.

---

## Non-negotiable rules

These apply to every task, no exceptions.

### Tenant isolation

- Every new model **must** inherit `ChurchOwnedModel` (or `TimestampedModel`
  for non-tenant tables like billing). No plain `models.Model` for app data.
- Always use `Model.objects` (scoped manager) in views. `raw_objects` is for
  management commands and admin only — never in a view or serialiser.
- Never add `using=` to a queryset in view code. The `TenantDatabaseRouter`
  handles routing from the contextvar automatically.
- Never create a FK from a white-label tenant model to a shared-DB model
  (other than the `church` FK itself which is explicitly allowed).

### AI skills

- Every new skill function in `core/ai_skills.py` must accept `church` as a
  parameter and call `build_support_system_prompt(church)` or read
  `church.settings` to personalise the system prompt.
- All AI calls must be wrapped in `try/except`. Return `None` or a safe
  default on failure — never let an AI error propagate to the user.
- Use `HAIKU` for fast/cheap tasks, `SONNET` for quality/analysis tasks.

### Bible feature

- `bible_verse_for_date` must call YouVersion API first (via
  `YouVersionTranslationMap`), then fall back to the hardcoded verse table.
  Claude AI involvement is last-resort only and must be behind a feature flag.
- The Bible feature must be available identically across all plan tiers — no
  paywall gating. YouVersion non-commercial agreement requires this.

### Subscription model (current: free-first)

- All new churches are provisioned on the **path-slug routing tier** (`trial` plan
  record) with `is_trial=False` and `expires_at=None`.
- **Plans are routing choices only** — Path URL, Subdomain, Custom Domain.
  Churches switch freely via `billing.services.apply_routing_plan()`.
- Subscription routing infrastructure (slug, subdomain, custom domain) stays
  fully intact — it's routing architecture, not payment gating.
- Paystack views remain for future paid tiers; `$0` plans apply routing instantly.
- Do not remove or disable the `SubscriptionPlan` billing models.
- `hide_platform_credit` toggles on white-label (custom domain) routing tier.

### Append-only tables

Never add update or delete logic to `AutomationLog` or `SMSLog`. These are
immutable audit records.

### Circular import guard

`Church` lives in `tenants/models.py`. Never import from `core/models.py`
inside `tenants/models.py`. `TenantBaseModel` exists to break this cycle.

---

## Before writing any code — checklist

Run through this mentally before every implementation:

**New model?**

- [ ] Inherits `ChurchOwnedModel`
- [ ] Uses `ScopedChurchManager` (comes from `ChurchOwnedModel`)
- [ ] Has composite index on `(church, <main_filter_field>)` if frequently queried by both
- [ ] Migration created and tested on `default` DB

**New view?**

- [ ] Reads `request.church` — never re-resolves Church
- [ ] Access control via `request.permissions.can("...")` — not raw role checks
- [ ] All queries use `.objects` (scoped) — not `.raw_objects`
- [ ] AI calls wrapped in `try/except`

**New AI skill?**

- [ ] Accepts `church` parameter
- [ ] Personalises system prompt from `church.settings`
- [ ] Right model tier: `HAIKU` (fast) or `SONNET` (quality)
- [ ] Returns `None` or safe default on failure
- [ ] Works with `AI_PROVIDER=ollama` locally

**New template?**

- [ ] Extends correct base: `base.html` (app), `public_base.html` (public),
      `base_marketing.html` (marketing pages)
- [ ] New or refactored UI → invoke `/frontend-design` first (marketing,
      onboarding, module pages, and existing template refactors)
- [ ] New architecture change → update relevant Excalidraw in `docs/architecture/`

---

## After any implementation — auto-review

Before presenting code, silently run these checks:

1. **Simplify pass** — duplicated logic, functions doing too much, N+1 queries,
   dead imports, naming that doesn't communicate intent. Fix before showing.
2. **Tenant isolation check** — does any new code touch `raw_objects` in a
   view, or query without a `church` filter where one is needed?
3. **AI skill check** — is every AI call wrapped, personalised, and using the
   right model tier?
4. **Migration safety** — does this migration touch a column used by the
   `TenantDatabaseRouter` or the `Church` model? Flag it before running.

---

## Installed skills and when to invoke them

These are installed in `.claude/skills/` (see setup below). Invoke them by
name or let Claude trigger them automatically from context.

| Skill                  | Invoke when                                                         |
| ---------------------- | ------------------------------------------------------------------- |
| `simplify`             | After every implementation (runs automatically per rule above)      |
| `frontend-design`      | New marketing/onboarding/module UI **and** existing template refactors |
| `browser-use`          | Integration testing tenant routing, subscription wall, guest forms  |
| `code-reviewer`        | Deep review pass on any file >200 lines before a PR                 |
| `security-auditor`     | Before any white-label customer goes live; after middleware changes |
| `systematic-debugging` | Investigating a bug that spans middleware + views + models          |
| `brainstorming`        | Planning a new feature before writing any code                      |
| `create-pr`            | Packaging a completed feature for review                            |
| `lint-and-validate`    | Before every commit                                                 |
| `excalidraw-diagram`   | Updating architecture docs; building marketing page diagrams        |
| `remotion-best-practices` | Marketing page video assets; onboarding ambient loop             |
| `gws-*`                | Building Google Calendar / Gmail integrations (future)              |
| `shannon`              | Autonomous pentest before white-label go-live (staging only)        |
| `valyu-best-practices` | Real-time data for evangelism_research skill                        |

---

## Key file locations (quick reference)

```
apps/tenants/middleware.py          Tenant resolution + subscription wall
apps/tenants/db_router.py           DB routing logic
apps/tenants/models.py              Church, ChurchSetting, Campus, Subscription
apps/tenants/onboarding.py          Church provisioning (called on signup)
apps/tenants/campus_provisioning.py Campus → Church promotion flow
apps/core/models.py                 ChurchOwnedModel, TimestampedModel, base hierarchy
apps/core/managers.py               ScopedChurchManager, RawChurchManager
apps/core/ai_skills.py              All AI skill functions (Claude / Ollama)
apps/core/request_context.py        contextvars — get_current_church()
apps/permissions/services/resolver.py  7-tier permission resolver
apps/permissions/registry.py        Permission constants and tier definitions
apps/bible/models.py                YouVersionTranslationMap, reading plans, badges
apps/guests/models.py               GuestStatus (pipeline), GuestEntry
apps/workforce/models.py            WorkforceStage, WorkforceMember
apps/lms/models.py                  LMSCourse, LMSModule, LMSEnrollment, LMSCertificate
apps/music/models.py                Setlist, Song, ChordChart, RehearsalSession
apps/services/models.py             Event (single model for all church activities)
apps/automation/models.py           AutomationLog (append-only)
apps/messaging/models.py            GuestMessage, SMSLog (append-only)
churchforce/settings.py             Main settings (AI_PROVIDER, DATABASES, CHANNELS)
static/js/custom.js                 Main app JS
static/css/custom.css               App-wide styles (Tabler overrides)
templates/accounts/welcome_modal.html   First-login onboarding screen
apps/marketing/templates/marketing/    Public marketing pages
docs/architecture/                  Excalidraw architecture diagrams (keep updated)
docs/skills/SKILL.md                Full reference manual (read for deep context)
```

---

## Permissions tier (quick reference)

```
0  Superuser        → all, all tenants
1  Church Admin     → all within church
2  Sub-Admin        → global-scope perms
3  Assistant Pastor → global-scope perms
4  Overseer         → unit-scoped only
5  Unit Head        → unit-scoped only
6  Assistant        → unit-scoped only
7  Custom           → JSONField perms, unit-scoped unless scope="global"
```

Global bleed only allowed for tiers ≤ 3 with `scope="global"`.

---

## Skill setup (run once per machine)

```bash
# Verify all project skills (symlinks + installs live in .claude/skills/)
ls .claude/skills/

# Core quality skills
npx skills add anthropics/claude-code --skill frontend-design

# Antigravity bundle skills are symlinked from ~/.agents/skills/ into
# .claude/skills/ for: brainstorming, systematic-debugging, security-auditor,
# create-pr, lint-and-validate. simplify is project-local (see simplify/SKILL.md).

# Browser automation (integration testing)
npx skills add https://github.com/browser-use/browser-use --skill browser-use

# Diagram generation (architecture docs + marketing)
npx skills add https://github.com/coleam00/excalidraw-diagram-skill --skill excalidraw-diagram

# Video production (marketing pages + onboarding)
npx skills add remotion/agent-skills

# Google Workspace (when building calendar/email integrations)
npm install -g @googleworkspace/cli
npx skills add https://github.com/googleworkspace/cli

# Shannon pen testing (before white-label launch)
# Check current install: https://github.com/shannon-ai

# Valyu real-time data (when extending evangelism_research skill)
# Check current install: https://docs.valyu.network
```

Verify skills are installed:

```bash
ls .claude/skills/
```

---

## Module feature flags (ChurchSetting)

These flags control which modules are visible in the UI per church. When
building a new module, add a corresponding `enable_<module>_module` flag to
`ChurchSetting` and check it in the relevant views and templates.

Currently off by default: `enable_children_module`, `enable_youth_module`,
`enable_teenagers_module`. These modules need UI work before they're enabled.

---

## Common mistakes to catch

- `raw_objects` in a view → always wrong, refactor to `objects`
- Missing `church` param on an AI skill function → breaks personalisation
- `bible_verse_for_date` calling Claude without trying YouVersion first → wrong
- New `SubscriptionPlan` check that blocks Bible feature → violates YouVersion terms
- FK from app model to `auth.User` directly instead of `accounts.ChurchMember` → wrong (ChurchMember is the tenant join)
- Adding `expires_at` to a free-plan church → sets it going inactive
- Template reading `request.church` directly → use context processors instead
