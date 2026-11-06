"""Opt-in HPS minimum-core policy, with thermal limits left in control."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from .core import CheckError, validate_host
from .node_exporter import script_request


def parse_cpu_status(output):
    values = {}
    allowed = {"cpu_policy_state", "cpu_online", "cpu_minimum", "cpu_thermal_limit"}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in allowed or key in values:
            raise CheckError("invalid_cpu_response")
        values[key] = value
    if set(values) != allowed or values["cpu_policy_state"] not in {
        "automatic", "requested", "all_online", "constrained", "unsupported"
    }:
        raise CheckError("invalid_cpu_response")
    if values["cpu_policy_state"] == "unsupported":
        if any(values[k] != "unknown" for k in allowed - {"cpu_policy_state"}):
            raise CheckError("invalid_cpu_response")
    else:
        if values["cpu_online"] not in {"0", "0-1", "0-2", "0-3"}:
            raise CheckError("invalid_cpu_response")
        for key in ("cpu_minimum", "cpu_thermal_limit"):
            if values[key] not in {"1", "2", "3", "4"}:
                raise CheckError("invalid_cpu_response")
            values[key] = int(values[key])
    values["cpu_checked_at"] = datetime.now(timezone.utc).isoformat()
    return values


class CpuManager:
    def __init__(self, transport, executor):
        self.transport, self.executor = transport, executor

    async def execute(self, settings, action):
        if action not in {"inspect", "enable", "disable"}:
            raise ValueError("Invalid CPU action")
        host = validate_host(settings["host"])
        script = await self.executor(Path(__file__).with_name("cpu_manager.sh").read_text, "utf-8")
        command, payload = script_request(script, action, host, "unused", 0, "unused", "unused")
        try:
            async with asyncio.timeout(25):
                return parse_cpu_status(await self.transport(settings, command, payload))
        except TimeoutError:
            raise CheckError("cpu_timeout_check_before_retry") from None
