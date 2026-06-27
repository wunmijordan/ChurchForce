#!/usr/bin/env python3
"""Generate ChurchForce architecture Excalidraw files. Run from repo root."""
import json
from pathlib import Path

OUT = Path(__file__).parent


def box(x, y, w, h, text, fill="#a5d8ff", stroke="#1971c2", bid=None):
    bid = bid or f"rect_{x}_{y}"
    tid = f"text_{x}_{y}"
    return [
        {
            "type": "rectangle",
            "id": bid,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "strokeColor": stroke,
            "backgroundColor": fill,
            "fillStyle": "solid",
            "strokeWidth": 2,
            "strokeStyle": "solid",
            "roughness": 1,
            "opacity": 100,
            "angle": 0,
            "seed": x + y,
            "version": 1,
            "versionNonce": x * y + 1,
            "isDeleted": False,
            "groupIds": [],
            "boundElements": [{"type": "text", "id": tid}],
            "link": None,
            "locked": False,
            "roundness": {"type": 3},
        },
        {
            "type": "text",
            "id": tid,
            "x": x + 8,
            "y": y + h / 2 - 10,
            "width": w - 16,
            "height": 25,
            "text": text,
            "originalText": text,
            "fontSize": 16,
            "fontFamily": 3,
            "textAlign": "center",
            "verticalAlign": "middle",
            "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent",
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "angle": 0,
            "seed": x + y + 2,
            "version": 1,
            "versionNonce": x * y + 3,
            "isDeleted": False,
            "groupIds": [],
            "boundElements": None,
            "link": None,
            "locked": False,
            "containerId": bid,
            "lineHeight": 1.25,
        },
    ]


def arrow(x1, y1, x2, y2, aid=None):
    aid = aid or f"arrow_{x1}_{y1}"
    return {
        "type": "arrow",
        "id": aid,
        "x": x1,
        "y": y1,
        "width": x2 - x1,
        "height": y2 - y1,
        "strokeColor": "#495057",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "angle": 0,
        "seed": x1 + y1,
        "version": 1,
        "versionNonce": x1 * y1,
        "isDeleted": False,
        "groupIds": [],
        "boundElements": None,
        "link": None,
        "locked": False,
        "points": [[0, 0], [x2 - x1, y2 - y1]],
        "lastCommittedPoint": None,
        "startBinding": None,
        "endBinding": None,
        "startArrowhead": None,
        "endArrowhead": "arrow",
    }


def save(name, elements):
    doc = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {
            "viewBackgroundColor": "#ffffff",
            "gridSize": 20,
        },
        "files": {},
    }
    path = OUT / name
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


def tenant_routing():
    els = []
    els += box(40, 40, 200, 50, "HTTP Request")
    els += box(40, 130, 200, 50, "TenantMiddleware")
    els += box(280, 80, 220, 45, "churchforce.io\n(marketing)")
    els += box(280, 140, 220, 45, "client.com\n(custom domain)")
    els += box(280, 200, 220, 45, "slug.workforce.church\n(subdomain)")
    els += box(280, 260, 220, 45, "workforce.church/slug/\n(path slug)")
    els += box(40, 240, 200, 50, "ChurchContextMiddleware")
    els += box(40, 330, 200, 50, "Scoped .objects queries")
    els += [arrow(140, 90, 140, 130), arrow(240, 155, 280, 163),
            arrow(240, 175, 280, 223), arrow(240, 195, 280, 283),
            arrow(140, 180, 140, 240), arrow(140, 290, 140, 330)]
    save("tenant-routing.excalidraw", els)


def db_router():
    els = []
    els += box(40, 60, 240, 55, "ChurchOwnedModel query")
    els += box(40, 160, 240, 55, "TenantDatabaseRouter")
    els += box(320, 100, 260, 55, "default DB\n(trial + saas tenants)")
    els += box(320, 220, 260, 55, "wl_<slug> DB\n(white-label only)")
    els += box(40, 280, 240, 55, "SHARED_APPS always → default")
    els += [arrow(160, 115, 160, 160), arrow(280, 187, 320, 127),
            arrow(280, 210, 320, 247), arrow(160, 215, 160, 280)]
    save("db-router.excalidraw", els)


def campus_hierarchy():
    els = []
    els += box(200, 40, 200, 50, "HQ Church")
    els += box(80, 150, 180, 50, "Campus Church A")
    els += box(340, 150, 180, 50, "Campus Church B")
    els += box(80, 260, 180, 50, "Campus record")
    els += [arrow(250, 90, 170, 150), arrow(350, 90, 430, 150),
            arrow(170, 200, 170, 260)]
    save("campus-hierarchy.excalidraw", els)


def guest_pipeline():
    stages = ["Visitor", "New Guest", "In Contact", "Committed", "Planted"]
    els = []
    x = 40
    for i, s in enumerate(stages):
        els += box(x, 100, 130, 50, s)
        if i < len(stages) - 1:
            els.append(arrow(x + 130, 125, x + 170, 125))
        x += 170
    els += box(40, 200, 130, 50, "Not Planted")
    save("guest-pipeline.excalidraw", els)


def permissions():
    els = []
    tiers = ["Superuser", "Church Admin", "Sub-Admin", "Overseer", "Unit Head"]
    y = 40
    for t in tiers:
        els += box(40, y, 280, 42, t)
        y += 58
    els += box(360, 120, 240, 80, "PermissionResolver\nrequest.permissions.can()")
    save("permissions.excalidraw", els)


def free_routing():
    els = []
    els += box(40, 60, 180, 50, "Signup\n(path slug)")
    els += box(260, 60, 180, 50, "Subdomain tier")
    els += box(480, 60, 180, 50, "Custom domain")
    els += box(200, 180, 300, 60, "All tiers free\napply_routing_plan()")
    els += [arrow(130, 110, 250, 180), arrow(350, 110, 350, 180),
            arrow(570, 110, 450, 180)]
    save("free-routing-tiers.excalidraw", els)


if __name__ == "__main__":
    tenant_routing()
    db_router()
    campus_hierarchy()
    guest_pipeline()
    permissions()
    free_routing()
