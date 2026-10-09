"""Terminal output that can never block recording.

A paused terminal (Ctrl-S), a pipe nobody reads, or a stalled SSH session
makes writes to stdout block. If a poll thread printed directly, recording
would stop. So every line goes onto a bounded queue drained by one daemon
thread: only that thread can block, and when the queue is full new lines are
dropped (the JSONL event log is the durable record anyway).
"""

from __future__ import annotations

import queue
import sys
import threading

_QUEUE: queue.Queue[str] = queue.Queue(maxsize=1000)
_started = False
_start_lock = threading.Lock()
dropped = 0


def _drain() -> None:
    while True:
        line = _QUEUE.get()
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001  dead stdout: keep draining so producers never fill up
            pass


def _ensure_started() -> None:
    global _started
    if _started:
        return
    with _start_lock:
        if not _started:
            threading.Thread(target=_drain, name="console", daemon=True).start()
            _started = True


def say(line: str) -> None:
    """Queue a line for the terminal. Never blocks, never raises."""
    global dropped
    try:
        _ensure_started()
        _QUEUE.put_nowait(line)
    except queue.Full:
        dropped += 1
    except Exception:  # noqa: BLE001
        pass
