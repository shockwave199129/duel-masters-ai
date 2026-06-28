"""core/actions.py — compatibility shim.

All code moved to the core/actions/ package. This module re-exports
everything so existing `from core.actions import ...` callers keep working.
"""
from .actions import *  # noqa: F401,F403 — re-export for backwards compatibility
