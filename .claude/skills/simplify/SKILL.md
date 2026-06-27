---
name: simplify
description: >
  Review changed code for reuse, quality, and efficiency — then fix what you
  find before presenting to the user. ChurchForce auto-runs this after every
  implementation per CLAUDE.md.
---

# Simplify (ChurchForce)

Run after every implementation. Fix issues before showing code.

## Checklist

1. **Duplicated logic** — extract if used more than twice
2. **Single responsibility** — functions doing too much should split
3. **N+1 queries** — use `select_related` / `prefetch_related`
4. **Dead imports and unused variables**
5. **Naming** — must communicate intent
6. **Tenant isolation** — no `raw_objects` in views; all queries scoped
7. **AI calls** — `church` param, `try/except`, correct `HAIKU`/`SONNET`
8. **Models** — inherit `ChurchOwnedModel` for tenant data

## ChurchForce-specific flags

- `raw_objects` in a view → always refactor to `.objects`
- Missing `church` on new `core/ai_skills.py` functions
- `bible_verse_for_date` skipping YouVersion API
- Plan gating on Bible views (forbidden)
- `expires_at` set on free-plan churches

Fix what you find. Do not only list problems.
