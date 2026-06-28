"""core/zones.py — compatibility shim.

All code moved to the core/zones/ package. This module re-exports
everything so existing `from core.zones import ...` callers keep working.
"""
from .zones import *  # noqa: F401,F403 — re-export for backwards compatibility
