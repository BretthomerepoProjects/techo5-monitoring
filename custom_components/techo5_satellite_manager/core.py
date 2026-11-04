"""Bounded read-only checks, independent of Home Assistant."""
import asyncio
import ipaddress
import re
from datetime import datetime, timezone
from pathlib import Path

DOMAIN = "techo5_satellite_manager"
MAX_OUTPUT = 8192
TIMEOUT = 20
PROBE = r'''set -eu
printf 'schema=1\n'
printf 'arch='; uname -m
printf 'kernel='; uname -r
printf 'techo5='; if [ -r /etc/techo5/boot.sh ]; then echo yes; else echo no; fi
printf 'root_mode='; awk '$2=="/" {print $4; exit}' /proc/mounts
printf 'online='; cat /sys/devices/system/cpu/online
printf 'uptime='; cut -d' ' -f1 /proc/uptime
printf 'load='; cut -d' ' -f1-3 /proc/loadavg
awk '/^MemTotal:/ {print "memory_total_kib=" $2} /^MemAvailable:/ {print "memory_available_kib=" $2}' /proc/meminfo
printf 'avahi='; id -u avahi 2>/dev/null || echo absent
printf 'monitoring='; id -u monitoring 2>/dev/null || echo absent
p=$(pidof node_exporter 2>/dev/null || true)
set -- $p
printf 'exporter_count=%s\n' "$#"
if [ "$#" = 1 ]; then
  awk '/^Uid:/ {print "exporter_uid=" $2; print "exporter_uids=" $2 " " $3 " " $4 " " $5} /^Gid:/ {print "exporter_gid=" $2; print "exporter_gids=" $2 " " $3 " " $4 " " $5} /^CapEff:/ {print "exporter_caps=" $2} /^VmRSS:/ {print "exporter_rss_kib=" $2}' /proc/$1/status
fi
'''


class CheckError(Exception):
    """Public error code only; never expose SSH exception text."""


def validate_host(host):
    address = ipaddress.IPv4Address(host)
    if not any(address in ipaddress.ip_network(net) for net in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("An RFC1918 IPv4 address is required")
    return str(address)


def credential_paths(config_dir, key_name):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", key_name):
        raise ValueError("Invalid key name")
    base = Path(config_dir).resolve() / "satellite_manager" / "ssh"
    # Reject directory and file symlinks escaping the dedicated credential directory.
    if base.resolve() != base:
        raise ValueError("Credential directory must not be a symlink")
    paths = (base / key_name, base / "known_hosts")
    for path in paths:
        if path.resolve() != path or not path.is_file():
            raise ValueError("Credential file missing or symlinked")
        if path.stat().st_size > 65536:
            raise ValueError("Credential file too large")
    return paths


def parse_probe(output):
    if len(output.encode()) > MAX_OUTPUT:
        raise CheckError("output_limit")
    allowed = {"schema", "arch", "kernel", "techo5", "root_mode", "online", "uptime",
               "load", "memory_total_kib", "memory_available_kib", "avahi", "monitoring",
               "exporter_count", "exporter_uid", "exporter_gid", "exporter_uids", "exporter_gids", "exporter_caps", "exporter_rss_kib"}
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in values or len(value) > 256:
            raise CheckError("invalid_response")
        values[key] = value
    required = allowed - {"exporter_uid", "exporter_gid", "exporter_uids", "exporter_gids", "exporter_caps", "exporter_rss_kib", "memory_available_kib"}
    if not required <= values.keys() or values["schema"] != "1":
        raise CheckError("invalid_response")
    if values["techo5"] != "yes" or values["arch"] not in ("armv7l", "aarch64"):
        raise CheckError("unsupported_device")
    try:
        for key in ("memory_total_kib", "memory_available_kib", "exporter_count", "exporter_uid", "exporter_gid", "exporter_rss_kib"):
            if key in values:
                values[key] = int(values[key])
                if values[key] < 0:
                    raise ValueError
        float(values["uptime"])
        if len(values["load"].split()) != 3:
            raise ValueError
        for part in values["load"].split():
            float(part)
        if values["exporter_count"] == 1:
            if not {"exporter_uid", "exporter_gid", "exporter_caps"} <= values.keys():
                raise ValueError
            for key in ("exporter_uids", "exporter_gids"):
                ids = [int(part) for part in values.get(key, "").split()]
                if len(ids) != 4 or any(identity < 0 for identity in ids):
                    raise ValueError
            int(values["exporter_caps"], 16)
    except ValueError as exc:
        raise CheckError("invalid_response") from exc
    values["exporter_identity"] = (
        "absent" if values["exporter_count"] == 0 else
        "multiple" if values["exporter_count"] > 1 else
        "capabilities_observed" if int(values["exporter_caps"], 16) else
        "root_observed" if values["exporter_uid"] == 0 or values["exporter_gid"] == 0 or "0" in (values["exporter_uids"] + " " + values["exporter_gids"]).split() else
        "nonroot_observed"
    )
    # Non-root observed is evidence, not a declaration that the account is approved.
    values["deployment_enabled"] = False
    return values


class Checker:
    def __init__(self, transport, timeout=TIMEOUT):
        self.transport = transport
        self.timeout = timeout
        self.busy = False

    async def check(self, settings):
        if self.busy:
            raise CheckError("busy")
        self.busy = True
        try:
            async with asyncio.timeout(self.timeout):
                result = parse_probe(await self.transport(settings, PROBE))
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            return result
        except TimeoutError as exc:
            raise CheckError("timeout") from exc
        finally:
            self.busy = False
