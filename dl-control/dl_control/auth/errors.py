class MustRotatePasswordError(RuntimeError):
    """An admin with must_change_password attempted a mutating JSON API
    call. The exception handler in main.py maps it to HTTP 423."""
