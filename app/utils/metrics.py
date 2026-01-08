"""
Simple metrics collection for observability (A6).

Provides in-memory counters and gauges for monitoring application health.
No external dependencies required.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Any
from datetime import datetime, timezone


@dataclass
class Metrics:
    """Thread-safe metrics collection."""

    # Counters (monotonically increasing)
    websocket_connections_total: int = 0
    websocket_disconnections_total: int = 0
    groups_created_total: int = 0
    groups_deleted_total: int = 0
    image_cycles_total: int = 0
    errors_total: int = 0

    # Gauges (current values)
    active_groups: int = 0
    active_browsers: int = 0

    # Timestamps
    start_time: float = field(default_factory=time.time)
    last_cycle_time: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dictionary for JSON response."""
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": round(uptime, 2),
            "counters": {
                "websocket_connections_total": self.websocket_connections_total,
                "websocket_disconnections_total": self.websocket_disconnections_total,
                "groups_created_total": self.groups_created_total,
                "groups_deleted_total": self.groups_deleted_total,
                "image_cycles_total": self.image_cycles_total,
                "errors_total": self.errors_total,
            },
            "gauges": {
                "active_groups": self.active_groups,
                "active_browsers": self.active_browsers,
            },
            "timestamps": {
                "start_time": datetime.fromtimestamp(
                    self.start_time, tz=timezone.utc
                ).isoformat(),
                "last_cycle_time": (
                    datetime.fromtimestamp(
                        self.last_cycle_time, tz=timezone.utc
                    ).isoformat()
                    if self.last_cycle_time
                    else None
                ),
            },
        }

    def record_connection(self) -> None:
        """Record a new WebSocket connection."""
        self.websocket_connections_total += 1
        self.active_browsers += 1

    def record_disconnection(self) -> None:
        """Record a WebSocket disconnection."""
        self.websocket_disconnections_total += 1
        self.active_browsers = max(0, self.active_browsers - 1)

    def record_group_created(self) -> None:
        """Record a new group creation."""
        self.groups_created_total += 1
        self.active_groups += 1

    def record_group_deleted(self) -> None:
        """Record a group deletion."""
        self.groups_deleted_total += 1
        self.active_groups = max(0, self.active_groups - 1)

    def record_cycle(self) -> None:
        """Record an image cycle completion."""
        self.image_cycles_total += 1
        self.last_cycle_time = time.time()

    def record_error(self) -> None:
        """Record an error occurrence."""
        self.errors_total += 1


# Global metrics instance
metrics = Metrics()
