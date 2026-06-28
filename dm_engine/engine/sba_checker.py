"""engine/sba_checker.py — compatibility shim. Code moved to engine/sba/."""
from .sba import checker as _checker
from .sba.checker import *  # noqa: F401,F403

# Force re-export of underscore-prefixed functions that import * skips
import sys as _sys
_mod = _sys.modules[__name__]
for _n in dir(_checker):
    if not _n.startswith('__') and not hasattr(_mod, _n):
        setattr(_mod, _n, getattr(_checker, _n))
del _sys, _mod, _n
