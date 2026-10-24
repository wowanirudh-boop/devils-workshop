"""Keeping Windows from sleeping while the app is writing or planning.

Requests can take minutes, and when you use the app from your phone, the PC looks idle to Windows,
so it may go to sleep mid-chapter. While a request, an auto-write run or a planning run is under
way, the app tells Windows the system is in use (SetThreadExecutionState with ES_SYSTEM_REQUIRED);
when the last one ends, it hands that back. The screen can still turn off, and you can still put
the PC to sleep yourself (Start → Sleep, closing the lid); only sleeping because of idleness waits.

Everything that holds it runs on the app's one event-loop thread, which is what Windows needs:
the request is made and withdrawn by the same thread.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

log = logging.getLogger(__name__)
_holders = 0


def _windows(flags: int) -> None:
    if sys.platform == "win32":
        import ctypes
        if not ctypes.windll.kernel32.SetThreadExecutionState(flags):
            log.warning("Windows didn't accept the keep-awake request (flags %#x)", flags)


setter: Callable[[int], None] = _windows  # tests replace it


def _set(flags: int) -> None:
    try:
        setter(flags)
    except Exception:  # noqa: BLE001 - staying awake is a help, never a reason for a request to fail
        log.exception("Couldn't change the keep-awake state")


@contextmanager
def awake() -> Iterator[None]:
    """Keep the PC awake inside this block. Blocks can overlap (several requests at the same time):
    Windows is told once when the first starts, and again when the last one ends."""
    global _holders
    _holders += 1
    if _holders == 1:
        _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _holders -= 1
        if _holders == 0:
            _set(ES_CONTINUOUS)


def holding() -> bool:
    """True while something is keeping the PC awake."""
    return _holders > 0
