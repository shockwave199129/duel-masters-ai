"""core/cards.py — compatibility shim.

All code moved to the core/cards/ package. This module re-exports
everything so existing `from core.cards import ...` callers keep working.
"""
from .cards import *  # noqa: F401,F403 — re-export for backwards compatibility
