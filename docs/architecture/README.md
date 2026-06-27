# ChurchForce Architecture Diagrams

Hand-maintained [Excalidraw](https://excalidraw.com) source files for key system flows.

Open any `.excalidraw` file in Excalidraw, or regenerate with `/excalidraw` (see `.claude/skills/excalidraw-diagram/`).

| File | Regenerate when |
|------|-----------------|
| `tenant-routing.excalidraw` | `apps/tenants/middleware.py` changes |
| `db-router.excalidraw` | `apps/tenants/db_router.py` or `SHARED_APPS` changes |
| `campus-hierarchy.excalidraw` | `Campus` or `Church` parent/campus models change |
| `guest-pipeline.excalidraw` | `GuestStatus` slugs or pipeline logic changes |
| `permissions.excalidraw` | `apps/permissions/registry.py` changes |
| `free-routing-tiers.excalidraw` | Billing routing / `apply_routing_plan()` changes |

## Regenerate baseline JSON

```bash
python docs/architecture/_generate_diagrams.py
```

Optional PNG export (requires skill render deps):

```bash
cd .claude/skills/excalidraw-diagram/references && uv run python render_excalidraw.py ../../../docs/architecture/tenant-routing.excalidraw
```
