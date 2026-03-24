#!/usr/bin/env python3
"""Django's command-line utility for administrative tasks."""

import os
import sys
import socket
from pathlib import Path


# ── CRITICAL: add apps/ to sys.path BEFORE Django loads ──────────────────────
# Django calls apps.populate() during setup, which imports every AppConfig
# listed in INSTALLED_APPS. All our apps live in apps/, so this path must
# be on sys.path before that happens — not just inside settings.py.
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "apps"))


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchforce.settings")

    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        ip = get_local_ip()
        print(f"\n🚀 Dev server — open on mobile: http://{ip}:8000\n")

    os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Make sure it's installed and your "
            "virtual environment is activated."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()