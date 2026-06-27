---
name: churchforce
description: >
  Full reference manual for ChurchForce. Read this when CLAUDE.md context is
  not enough — deep architectural decisions, skill-specific workflows, security
  surfaces, and the free-plan subscription model. CLAUDE.md auto-loads on every
  task; this file is the manual you reach for when you need the full picture.
version: "1.1"
updated: 2026-06-12
---

# ChurchForce — Full Reference Manual

---

## 1. Architecture deep-dive

### Three-tier tenant model

```
Trial      →  app.com/slug/...         → shared default DB
SaaS       →  slug.app.com             → shared default DB
White-label → client.com (custom)      → dedicated wl_<slug> DB
```

**All three tiers are currently free.** Routing infrastructure is intact for
future paid tier re-introduction. No architectural changes needed to add
paid plans later — just create new `SubscriptionPlan` records and migrate
churches up.

### Free-plan provisioning pattern

When onboarding a new church (`apps/tenants/onboarding.py`):

```python
# Pattern to follow in provision_church()
free_plan, _ = SubscriptionPlan.objects.get_or_create(
    name="free",
    defaults={
        "white_label": False,
        "monthly_price": 0,
        "campus_limit": -1,   # unlimited campuses on free
    }
)
ChurchSubscription.objects.create(
    church=church,
    plan=free_plan,
    is_trial=False,       # not a trial — genuinely free
    expires_at=None,      # never expires
)
```

`TenantMiddleware._subscription_wall()` checks `sub.is_valid()`. A
non-expiring free plan with `is_trial=False` passes this check cleanly.
The subscription wall only fires for `None` subscription or an expired one.

### Database router — full rules

Shared apps (always `default`):

```python
SHARED_APPS = {
    "contenttypes", "auth", "admin", "sessions",
    "tenants", "billing", "bootstrap",
}
```

White-label alias format: `wl_<church.slug>`. Only routed if the alias is
in `settings.DATABASES`. Missing alias → graceful fallback to `default`.

The router reads the church from `core.request_context.get_current_church()`
which is set by `ChurchContextMiddleware` → `set_current_request(request)`.
Never call `get_current_church()` outside of a request context (e.g. in
management commands) — use `using=` explicitly there.

### Campus hierarchy

```
Church (HQ)
  └── campus_churches (reverse FK via parent_church)
       └── Church (campus-church)
            └── campus_record (reverse OneToOne from Campus.campus_church)
                 └── Campus
                      └── CampusMembership → ChurchMember
```

HQ pushes feature overrides to campus-churches via `Church.hq_override`
(JSONField). Campus-churches inherit HQ subscription if they have none of
their own (`get_effective_subscription()` walks the parent chain).

Slug patterns:

- Trial: `{parent-slug}-{location}` e.g. `gatewaynation-abuja`
- SaaS: `{location}.{parent-subdomain}` e.g. `abuja.gatewaynation`
- White-label: `{location}.{parent-custom-domain-prefix}`

---

## 2. Bible feature — YouVersion integration

### Target behaviour for `bible_verse_for_date`

```python
def bible_verse_for_date(today=None):
    """
    Priority order:
    1. YouVersion API  (YOUVERSION_APP_KEY must be set)
    2. Hardcoded verse table (always available)
    3. Claude AI generation (last resort, behind feature flag)
    """
    # Step 1: try YouVersion
    if settings.YOUVERSION_APP_KEY:
        verse = _fetch_youversion_verse_of_day(today)
        if verse:
            return verse

    # Step 2: hardcoded table
    verse = _get_hardcoded_verse(today)
    if verse:
        return verse

    # Step 3: AI fallback (only if feature flag enabled)
    if getattr(settings, "BIBLE_AI_FALLBACK_ENABLED", False):
        return _ai_generate_verse(today)

    return None
```

The `YouVersionTranslationMap` model in `bible/models.py` already maps short
codes (NIV, BSB, etc.) to YouVersion Bible IDs. Use that as the translation
lookup when calling the YouVersion verse-of-the-day endpoint.

### Non-commercial constraint (standing rule)

YouVersion non-commercial agreement covers this integration. Rules:

- Bible feature must be available identically across ALL plan tiers.
- No paywall, no plan-tier gating, no "upgrade to access Bible" messaging.
- Currently safe: the entire platform is free, so no commercialisation occurs.
- If paid tiers are re-introduced: before gating any feature, confirm with
  YouVersion in writing whether the Bible feature can remain accessible to
  paid-tier users as an included feature (not an upsell). Get it in writing.
- Do not add `SubscriptionPlan` checks to any Bible view until confirmed.

---

## 3. AI skills — complete reference

### Provider switching

```python
# core/ai_skills.py
AI_PROVIDER = settings.AI_PROVIDER  # "anthropic" | "ollama"
HAIKU  = "claude-haiku-4-5"   # fast, cheap: SMS, verse labels, short copy
SONNET = "claude-sonnet-4-6"  # quality: analysis, chat, research
```

In dev (`DEBUG=True`), `AI_PROVIDER` defaults to `"ollama"`. Every skill
function routes through `_provider()` → `_chat()` / `_chat_with_history()`.
The Ollama path uses `OLLAMA_BASE_URL` and `OLLAMA_MODEL` settings.

### All current skill functions

| Function                      | Model            | Input                                   | Purpose                                        |
| ----------------------------- | ---------------- | --------------------------------------- | ---------------------------------------------- |
| `bible_verse_for_date`        | None (API first) | `date`                                  | Daily verse widget                             |
| `support_reply`               | Sonnet           | `church, history, user_message, member` | Multi-turn chat bot                            |
| `build_support_system_prompt` | —                | `church, member`                        | Constructs system prompt from ChurchSetting    |
| `structure_lyrics`            | Haiku            | `raw_lyrics, title`                     | Parses Whisper output into verse/chorus/bridge |
| `analyze_song_structure`      | Haiku            | `title, sections`                       | Key, tempo, mood, chord analysis               |
| `evangelism_research`         | Sonnet           | `church, guest_patterns`                | Pattern analysis on guest data                 |
| `dashboard_analysis`          | Sonnet           | `church, metrics`                       | Trends summary for dashboard widget            |
| `birthday_message_workforce`  | Haiku            | `member_name, role, church_name`        | Staff birthday SMS                             |
| `birthday_message_guest`      | Haiku            | `guest_name, guest_status, church_name` | Guest birthday SMS                             |
| `sermon_summary_for_period`   | Haiku            | `events`                                | Summarise event list into notes                |

### Adding a new skill function — template

```python
def my_new_skill(church, input_data: dict) -> Optional[str]:
    """One-line description of what this skill does."""
    try:
        system = build_support_system_prompt(church)  # always personalise
        user = f"Your task: ...\n\nData:\n{json.dumps(input_data)}"
        return _chat(system=system, user=user, model=HAIKU)  # or SONNET
    except Exception as exc:
        logger.error("my_new_skill failed: %s", exc)
        return None  # never propagate
```

### Personalisation fields (from `ChurchSetting`)

`build_support_system_prompt` draws from:

- `church.settings.core_values` — theological emphasis (JSON list)
- `church.settings.vision_statement`
- `church.settings.mission_statement`
- `church.settings.denominational_affiliation`
- Member tier (from WorkforceMember role) for response depth

When writing a new system prompt from scratch, pull these same fields.

---

## 4. Installed skills — detailed usage guide

### simplify (Anthropic official)

**Auto-runs after every implementation.**

```bash
npx skills add anthropics/claude-code --skill simplify
```

Checks for: duplicated logic, single-responsibility violations, N+1 queries,
dead imports, naming issues. Fixes before presenting code.

ChurchForce targets:

- `apps/core/ai_skills.py` — `db_for_read`/`db_for_write` in the router
  share near-identical instance hint resolution. Extract to
  `_resolve_church_from_hints(hints)` helper.
- Any `raw_objects` usage in a view — always a bug.
- LMS peer review / proctor review flows — overlapping submission state logic.

Configure in `CLAUDE.md` (already done):

```
After any implementation: flag raw_objects in views, N+1 queries,
AI calls without try/except, models not inheriting ChurchOwnedModel.
```

---

### frontend-design (Anthropic official)

**Invoke before any new UI page.**

```bash
npx skills add anthropics/claude-code --skill frontend-design
```

ChurchForce uses Tabler UI. New views should feel intentional within Tabler,
not like generic AI card grids. The skill provides a design system and
opinionated aesthetic direction before writing any HTML/CSS.

**When to invoke:**

- Any new template introducing a new page pattern
- Children / youth / teenagers ministry modules (not yet built)
- Founder control panel (`apps/founder/`)
- **All marketing pages** (`apps/marketing/templates/marketing/`)
- Onboarding: `welcome_modal.html`, `credentials.html`, `accept_invitation.html`

Marketing pages are the first thing a prospective church sees. They must not
look AI-generated. Invoke `/frontend-design` before every marketing template.

---

### browser-use

**Invoke for integration testing across all three tenant tiers.**

```bash
npx skills add https://github.com/browser-use/browser-use --skill browser-use
```

**Test targets for ChurchForce:**

```
"Verify slug.app.com resolves to the correct church and redirects
 to the subscription wall after trial expiry."

"Test that a white-label domain rejects a CSRF request from an
 unregistered origin."

"Walk through /interest/<token>/ guest form, confirm expiry redirect."

"Sign in as church admin, enrol a member in LMS, confirm strict_sequence
 gate blocks module 2 submission before module 1 is reviewed."

"Test that session key ACTIVE_CHURCH_SESSION_KEY does not persist
 across two different church tenants in the same browser session."
```

Run after every change to `middleware.py` or `db_router.py`.

---

### code-reviewer + security-auditor + systematic-debugging + brainstorming + lint-and-validate + create-pr (Antigravity bundle)

```bash
npx antigravity-awesome-skills --claude \
  --category development,security,testing
```

This installs the core Antigravity bundle covering:

- `brainstorming` — plan features and specs before writing code
- `lint-and-validate` — fast quality checks before commit
- `create-pr` — clean PR packaging
- `systematic-debugging` — repeatable bug investigation process
- `security-auditor` — API, auth, and sensitive flow review
- `code-reviewer` — deep review on large files

**security-auditor ChurchForce targets:**

1. Tenant routing — can a crafted path slug resolve the wrong church?
2. `ACTIVE_CHURCH_SESSION_KEY` — session poisoning risk across tenants.
3. `DynamicCSRFMiddleware` — auto-approves origins matching any church's
   `custom_domain`. Can an attacker register a matching domain before the
   church claims it?
4. `wl_<slug>` alias injection — can a crafted `church.slug` reference an
   unintended database alias?
5. Subscription wall — are paths in `SUBSCRIPTION_EXEMPT_PREFIXES` exposing
   tenant data without auth?

Run `security-auditor` before any white-label customer goes live.

---

### excalidraw

**Two use cases: internal docs + marketing pages.**

```bash
npx skills add https://github.com/excalidraw/excalidraw-agent-skill
```

**A. Internal architecture documentation (`docs/architecture/`)**

Maintain these diagrams. Regenerate after relevant model changes:

| File                             | Regenerate when                               |
| -------------------------------- | --------------------------------------------- |
| `tenant-routing.excalidraw`      | `middleware.py` changes                       |
| `db-router.excalidraw`           | `db_router.py` or `SHARED_APPS` changes       |
| `campus-hierarchy.excalidraw`    | `Campus` or `Church` model changes            |
| `guest-pipeline.excalidraw`      | `GuestStatus` slugs or pipeline logic changes |
| `permissions.excalidraw`         | `permissions/registry.py` changes             |
| `lms-assessment-flow.excalidraw` | `LMSEnrollment` or `LMSSubmission` changes    |

Add to `CLAUDE.md` (already included):

```
After modifying tenants/models.py or permissions/registry.py,
regenerate the relevant Excalidraw diagram in docs/architecture/.
```

**B. Marketing pages (`apps/marketing/templates/marketing/`)**

The Excalidraw hand-drawn style is appropriate for:

- Guest pipeline flow diagram on the home page (visitor → planted)
- Campus hierarchy explainer (HQ → Satellite → Daughter)
- "How it works" 3-step onboarding sequence
- Pricing page feature comparison (trial vs free vs future paid)

Workflow:

```bash
# Generate diagram
/excalidraw Show the ChurchForce guest pipeline: visitor arrives,
logged as New Guest, follow-ups move to In-Contact, regular attendance
marks Committed, then Planted or Not Planted. Church-branded, approachable.

# Export SVG → optimise → place in static
svgo static/images/diagrams/guest-pipeline.svg
# Reference in template:
# <img src="{% static 'images/diagrams/guest-pipeline.svg' %}" alt="...">
# Only inline SVG if under ~3KB
```

**C. Onboarding (`welcome_modal.html`)**

Small "you are here" orientation diagram: Sign up → Configure church →
Invite team → Go live. Export as SVG, inline it (will be small).

---

### remotion

**Marketing video assets and onboarding ambient loop. Not for in-app UI.**

```bash
npx skills add remotion/agent-skills
```

**A. Marketing page videos**

Run in a separate Remotion project (not inside the Django repo):

```bash
npx create-video@latest churchforce-videos
cd churchforce-videos
npx skills add remotion/agent-skills
```

Target assets:

- 15–30s guest pipeline walkthrough (New Guest → Committed → Planted)
- Campus hierarchy explainer (HQ → Satellite → Daughter, animated connections)
- Testimonial/social proof clips for pricing page

Render and upload to Cloudinary:

```bash
npx remotion render src/index.ts GuestPipeline \
  --output out/guest-pipeline.mp4
# Upload to Cloudinary, reference URL in marketing templates
```

**B. Onboarding ambient loop (`welcome_modal.html`)**

10–15 second ambient loop that plays during first-login church setup.
Render to MP4 → upload Cloudinary → embed:

```html
<video autoplay loop muted playsinline src="{{ cloudinary_url }}"></video>
```

**Not for:** in-app data visualisations (use Chart.js), real-time features
(use Django Channels), anything that needs to update dynamically.

---

### Shannon (pen testing)

**Run before any white-label customer goes live.**

```bash
# Verify current install: https://github.com/shannon-ai
```

Target the five surfaces identified in `CLAUDE.md` security section. Run
against staging with all three tenant tiers active. Document findings in
`docs/security/pentest-<date>.md`.

---

### Google Workspace (gws)

**Install when building calendar/email integrations.**

```bash
npm install -g @googleworkspace/cli
gws mcp -s drive,gmail,calendar,sheets
npx skills add https://github.com/googleworkspace/cli
```

Natural extension points:

- `services.Event` → Google Calendar sync
- `messaging.GuestMessage` → Gmail draft generation
- Guest pipeline reports → Google Sheets export

Add OAuth state per-church to `ChurchSetting` (or a new `integrations_config`
JSONField) so each church controls its own Google connection.

---

### Valyu (real-time data)

**Install when extending `evangelism_research` skill.**

```bash
# Check current install: https://docs.valyu.network
```

Inside `evangelism_research()`, after formatting internal `guest_patterns`,
use Valyu to fetch:

- Denominational research relevant to `church.settings.denominational_affiliation`
- Local demographic data keyed on `church.latitude`, `church.longitude`
- YouVersion reading trend data if available

Append Valyu results to the user message, not the system prompt.

---

## 5. Free-plan subscription model — implementation reference

### How the subscription wall handles free plans

```python
# tenants/middleware.py — _subscription_wall()
def _subscription_wall(self, request, church):
    sub = request.subscription
    if sub is None:
        return redirect("tenants:trial_expired")   # no subscription at all
    if not sub.is_valid():
        if sub.is_trial:
            return redirect("tenants:trial_expired")
        return redirect("tenants:subscription_inactive")
    return None  # ← free plan with no expiry passes here cleanly
```

A `ChurchSubscription` with `is_trial=False` and `expires_at=None` calls
`is_valid()` and returns `True` (implement this in the model if not already:
a non-trial subscription with no expiry date is always valid).

### Retaining routing tiers on a free plan

All three routing tiers continue to work identically:

- Trial-style path slug routing: free churches get `slug` set at onboarding
- SaaS-style subdomain: free churches get `subdomain` set at onboarding
- White-label custom domain: free churches can still get `custom_domain` set
  (they just won't have a dedicated DB unless provisioned manually)

The subscription tier in `SubscriptionPlan` controls whether a church gets
a dedicated DB and `hide_platform_credit`. Both can remain gated to a future
paid plan without affecting any other routing or feature access.

### Feature differentiation on the free plan

Currently the only meaningful differentiator is:

- `hide_platform_credit = False` → free churches show "Powered by ChurchForce"
- `hide_platform_credit = True` → white-label / future paid feature

Everything else (modules, campus creation, LMS, music, Bible) is available
to all free churches. Feature flags in `ChurchSetting` remain as per-church
admin toggles, not plan gates.

### Migration path to paid tiers (future)

When you're ready:

1. Create new `SubscriptionPlan` records (`SaaS`, `WhiteLabel`, etc.)
2. Update `onboarding.py` to assign the appropriate plan on signup
3. Add plan checks to the features you want to gate (use `church.plan` property)
4. Migrate existing churches to appropriate plans via management command
5. No changes needed to routing, middleware, or DB router

---

## 6. Security surfaces — full inventory

These are the surfaces `security-auditor` and Shannon should probe:

| Surface                         | Risk                                                                            | Location                                          |
| ------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------- |
| Path slug resolution            | Wrong tenant resolved via crafted URL                                           | `middleware.py` lines resolving `path_parts[0]`   |
| `ACTIVE_CHURCH_SESSION_KEY`     | Session poisoning — tenant context leaks across churches                        | `middleware.py` session read/write                |
| `DynamicCSRFMiddleware`         | Origin spoofing — attacker registers domain matching a church's `custom_domain` | `middleware.py` `DynamicCSRFMiddleware._accept()` |
| `wl_<slug>` alias               | Slug crafting to reference unintended DB alias                                  | `db_router.py` `_db_alias_for_church()`           |
| `SUBSCRIPTION_EXEMPT_PREFIXES`  | Data exposure on exempt paths without auth                                      | `middleware.py` `_is_exempt()`                    |
| White-label domain registration | Unvalidated domain written to `Church.custom_domain`                            | Wherever custom domain is set via form            |
| `provision_white_label`         | Bad migration on live tenant DB                                                 | `management/commands/provision_white_label.py`    |

---

## 7. Module feature flags reference

Set per-church in `ChurchSetting`. Check in views before rendering module UI.

| Flag                      | Default | Notes                              |
| ------------------------- | ------- | ---------------------------------- |
| `enable_guest_module`     | `True`  | Guest pipeline + follow-up         |
| `enable_music_module`     | `True`  | Setlists, chord charts, rehearsals |
| `enable_media_module`     | `True`  | Media/production team              |
| `enable_children_module`  | `False` | Not yet fully built out            |
| `enable_youth_module`     | `False` | Not yet fully built out            |
| `enable_teenagers_module` | `False` | Not yet fully built out            |
| `enable_sms_birthday`     | `True`  | Automated birthday SMS             |
| `enable_sms_bulk`         | `True`  | Manual bulk SMS to guests          |
| `enable_welcome_screen`   | `True`  | Welcome/quote after login          |

When building a new module, add a corresponding flag here and gate the
view with `request.church.settings.enable_<module>_module`.

---

## 8. Guest pipeline — status slug constants

The pipeline logic matches on `GuestStatus.slug`, not display name. These
constants are defined in `apps/guests/models.py`:

```python
GuestStatus.SLUG_NEW_GUEST    = "new-guest"
GuestStatus.SLUG_IN_CONTACT   = "in-contact"
GuestStatus.SLUG_COMMITTED    = "committed"
GuestStatus.SLUG_PLANTED      = "planted"
GuestStatus.SLUG_NOT_PLANTED  = "not-planted"
```

Auto-progression to `committed` is triggered by the scheduler when a guest
hits `ChurchSetting.committed_attendance_threshold` attendances within
`committed_attendance_window_weeks`. `is_terminal = True` statuses stop
auto-progression.

---

## 9. LMS assessment flow

```
LMSCourse
  └── LMSModule (ordered, strict_sequence optional)
        └── LMSSubmission (per enrollee per module)
              ├── LMSPeerReviewAssignment → LMSPeerReview
              └── LMSReview (proctor)
                    └── LMSCertificate (on pass) OR LMSEnrollment cancelled
```

`LMSEnrollment.current_module` tracks position. With `strict_sequence=True`,
submission for module N+1 is blocked until module N review is passed.

Induction courses gate `WorkforceTraineeProfile` → `WorkforceMember`
promotion. Never bypass this check via admin views.

---

## 10. Antigravity library — how to use for ChurchForce

The Antigravity library (`sickn33/antigravity-awesome-skills`) contains 1,525+
skills. Before writing a new SKILL.md from scratch, search the catalog:

```bash
# Search catalog for relevant skills
npx antigravity-awesome-skills --list --category backend
npx antigravity-awesome-skills --list --category security
npx antigravity-awesome-skills --list --category testing
```

ChurchForce-relevant skills already in the library:

- `django-patterns` (if available) — Django-specific patterns
- `systematic-debugging` — spans middleware + views + models (our exact use case)
- `security-auditor` — auth flows, API security, session handling
- `container-security-hardening` — Docker hardening if containerising
- `github-actions-advanced` — CI/CD for the deployment pipeline
- `lint-and-validate` — pre-commit quality gates

When a library skill covers a pattern we need, install and invoke it rather
than writing a custom one. Update this file with what was installed and why.

**Installed from Antigravity (track additions here):**

```
# Add entries as you install them:
# systematic-debugging  — middleware + DB router bug investigation
# security-auditor      — pre-white-label security review
# brainstorming         — feature planning sessions
# lint-and-validate     — pre-commit (pair with ruff + djlint)
# create-pr             — PR packaging
# code-reviewer         — deep review on ai_skills.py and large view files
```
