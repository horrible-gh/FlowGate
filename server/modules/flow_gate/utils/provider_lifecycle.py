"""Single-process provider deletion/admission serialization (0116 C-2-0).

Lock order: guard -> execution registry lock -> database transaction. Keep this
module free of services/DB imports so every path shares the very same lock.
The supported server deployment uses one worker; this is not an interprocess lock.
"""
from functools import wraps
from threading import RLock

guard = RLock()


def synchronized(function):
    @wraps(function)
    def protected(*args, **kwargs):
        with guard:
            return function(*args, **kwargs)
    return protected
