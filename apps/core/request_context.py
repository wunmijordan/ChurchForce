from contextvars import ContextVar


_current_request = ContextVar("current_request", default=None)


def set_current_request(request):
    """
    Store request in async-safe context.
    """
    _current_request.set(request)


def get_current_request():
    """
    Retrieve request from context.
    """
    return _current_request.get()


def get_current_user():
    request = get_current_request()
    if request:
        return getattr(request, "user", None)
    return None


def get_current_church():
    request = get_current_request()
    if request:
        return getattr(request, "church", None)
    return None