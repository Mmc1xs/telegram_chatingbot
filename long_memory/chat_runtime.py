import time
from contextlib import contextmanager
from threading import Lock


class ChatRuntimeState:
    """Tracks front-stage chat activity so background scoring can yield."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._inflight_count = 0
        self._last_user_activity_ts = 0.0

    def on_user_message_start(self) -> None:
        now = time.time()
        with self._lock:
            self._inflight_count += 1
            self._last_user_activity_ts = now

    def on_user_message_end(self) -> None:
        with self._lock:
            self._inflight_count = max(0, self._inflight_count - 1)
            self._last_user_activity_ts = time.time()

    def has_inflight_chat(self) -> bool:
        with self._lock:
            return self._inflight_count > 0

    def seconds_since_last_activity(self) -> float:
        with self._lock:
            if self._last_user_activity_ts == 0.0:
                return 999999.0
            return max(0.0, time.time() - self._last_user_activity_ts)

    def is_idle(self, idle_seconds: float) -> bool:
        return (not self.has_inflight_chat()) and (
            self.seconds_since_last_activity() >= idle_seconds
        )

    @contextmanager
    def processing_chat(self):
        self.on_user_message_start()
        try:
            yield
        finally:
            self.on_user_message_end()
