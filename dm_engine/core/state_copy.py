"""
core/state_copy.py - fast deep copy for game states.

A pickle round trip runs in C and is several times faster than copy.deepcopy.
As with deepcopy, card data (which never changes) is shared, not copied.
"""

from __future__ import annotations

import copyreg
import io
import pickle
import threading
from copy import deepcopy

from .cards import CardDefinition, CardEffect

_local = threading.local()


def _shared_card(index: int):
    return _local.shared[index]


def _share_card(card):
    _local.shared.append(card)
    return _shared_card, (len(_local.shared) - 1,)


_TABLE = copyreg.dispatch_table.copy()
_TABLE[CardDefinition] = _share_card
_TABLE[CardEffect] = _share_card


def copy_state(value):
    """A copy of ``value`` that shares its card data with the original."""
    outer = getattr(_local, "shared", None)
    _local.shared = []
    try:
        buffer = io.BytesIO()
        pickler = pickle.Pickler(buffer, pickle.HIGHEST_PROTOCOL)
        pickler.dispatch_table = _TABLE
        pickler.dump(value)
        return pickle.loads(buffer.getbuffer())
    except (pickle.PicklingError, AttributeError, TypeError):
        # Something in the state cannot be pickled, such as a function defined
        # inside another one. deepcopy is slower but handles it.
        return deepcopy(value)
    finally:
        _local.shared = outer
