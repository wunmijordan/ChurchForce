"""
notifications/middleware.py

get_current_user() is kept as a public import alias so that signals.py
can continue calling it by the same name. It now delegates to the
async-safe ContextVar in core.request_context rather than thread-locals,
which means it works correctly under ASGI / Django Channels.

The CurrentUserMiddleware class is no longer needed — ChurchContextMiddleware
in tenants.middleware already calls set_current_request(request) which
sets the same ContextVar. This module is kept only for the import alias.
"""

from core.request_context import get_current_user  # noqa: F401 — re-exported for signals.py

# CurrentUserMiddleware is intentionally omitted.
# set_current_request() is called by tenants.middleware.ChurchContextMiddleware.