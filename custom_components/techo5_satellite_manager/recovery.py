"""Bounded desired-state recovery, independent of Home Assistant."""
import time
from datetime import datetime, timedelta, timezone
from .core import CheckError


def recovery_action(observed):
    state = observed.get("node_state")
    if state in {"not_installed", "invalid"}:
        return "deploy"
    if state == "stopped":
        return "start"
    if state == "running":
        if observed.get("node_firewall") == "restricted":
            return None
        if observed.get("node_firewall") == "absent":
            return "start"
        raise CheckError("firewall_requires_review")
    raise CheckError("node_requires_review")


class RecoveryPolicy:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failures = 0
        self.next_attempt = 0
        self.last_action = None
        self.last_error = None

    def reset(self):
        self.failures = 0
        self.next_attempt = 0
        self.last_error = None

    def failed(self, code):
        self.failures = min(self.failures + 1, 8)
        self.next_attempt = self.clock() + min(60 * 2 ** (self.failures - 1), 900)
        self.last_error = code

    async def maintain(self, manager, settings, observed):
        action = recovery_action(observed)
        if action is None:
            self.reset()
            return observed
        if self.clock() < self.next_attempt:
            return observed
        self.last_action = action
        result = await manager.execute(settings, action)
        if result.get("node_state") != "running" or result.get("node_firewall") != "restricted":
            raise CheckError("recovery_not_running")
        self.reset()
        return result

    def attributes(self, enabled):
        delay = max(0, self.next_attempt - self.clock())
        retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat() if delay else None
        return {"node_recovery_enabled": enabled,
                "node_recovery_status": "disabled" if not enabled else "backoff" if delay else "enabled",
                "node_recovery_failures": self.failures,
                "node_recovery_last_action": self.last_action,
                "node_recovery_last_error": self.last_error,
                "node_recovery_next_retry": retry if enabled else None}
