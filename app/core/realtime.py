from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
import time
from typing import Any


@dataclass
class RealtimeEventJournal:
    max_events: int = 1000
    _events: deque[dict[str, Any]] = field(init=False, repr=False)
    _seq: int = field(init=False, default=0, repr=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._events = deque(maxlen=self.max_events)
        self._lock = Lock()

    def publish(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "seq": 0,
            "kind": kind,
            "payload": payload or {},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            self._events.append(event)
        return event

    def since(self, seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._events if int(event.get("seq", 0)) > seq]

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)
