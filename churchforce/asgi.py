"""
churchforce/asgi.py

ASGI application for ChurchForce.

WebSocket routing:
    notifications   — per-user notification channel (user_{id})
    workforce       — chat room + attendance channels
    units           — per-unit real-time updates (unit_{id})
"""

import os

# Must come before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchforce.settings")

from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import notifications.routing
import workforce.routing
import units.routing

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            notifications.routing.websocket_urlpatterns
            + workforce.routing.websocket_urlpatterns
            + units.routing.websocket_urlpatterns
        )
    ),
})