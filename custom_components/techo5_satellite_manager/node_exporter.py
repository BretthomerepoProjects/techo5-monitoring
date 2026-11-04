"""Pinned, bounded Node Exporter delivery; no Home Assistant imports."""
import asyncio
import hashlib
import http.client
import io
import shlex
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .core import CheckError, PROBE, parse_probe, validate_host

VERSION = "1.12.1"
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_BINARY = 32 * 1024 * 1024
# Archive digests: https://prometheus.io/download.json (v1.12.1).
# Executable digests are independently verified before upload and on the device.
PAYLOADS = {
    "armv7l": ("armv7", "ae3d72fbf77f9ebed553c24465310e0880383f45d3d1566357ce7a31a997acb2",
               "21353e64a91b08ac4f0c4e01b9897185be1508b815f3d0d9624f98a99117ac1d"),
    "aarch64": ("arm64", "ad35b605f9954b9f1ffddf5ba054bdc5a98d790b9eae5291e1eeb83f1ecbd0e7",
                "737591cd29f1aad5f1f53fe61d19ea54d841c27c101446619f7b026a682b85b8"),
}
ACTIONS = ("inspect", "deploy", "start", "stop", "uninstall")
STATES = {"not_installed", "stopped", "running", "failed", "unmanaged"}


def settings_for_node(settings):
    host = validate_host(settings["host"])
    collector = validate_host(settings.get("collector_host", ""))
    port = settings.get("exporter_port", 9100)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Invalid exporter port")
    if port == settings.get("port", 22) or collector == host:
        raise ValueError("Invalid exporter endpoint")
    return host, collector, port


def unpack_payload(data, arch):
    flavor, archive_hash, binary_hash = PAYLOADS[arch]
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != archive_hash:
        raise CheckError("download_checksum_failed")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember(f"node_exporter-{VERSION}.linux-{flavor}/node_exporter")
            if not member.isfile() or not 0 < member.size <= MAX_BINARY:
                raise CheckError("invalid_payload")
            with archive.extractfile(member) as stream:
                binary = stream.read(MAX_BINARY + 1)
    except (OSError, tarfile.TarError, KeyError) as exc:
        raise CheckError("invalid_payload") from exc
    if len(binary) != member.size or hashlib.sha256(binary).hexdigest() != binary_hash:
        raise CheckError("binary_checksum_failed")
    return binary


def download_payload(arch):
    """Run in an executor. TLS verification stays enabled; never extract paths."""
    flavor = PAYLOADS[arch][0]
    url = ("https://github.com/prometheus/node_exporter/releases/download/"
           f"v{VERSION}/node_exporter-{VERSION}.linux-{flavor}.tar.gz")
    try:
        class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not newurl.startswith("https://"):
                    raise CheckError("download_failed")
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        opener = urllib.request.build_opener(HTTPSRedirect())
        deadline = time.monotonic() + 45
        with opener.open(url, timeout=20) as response:
            chunks = []
            size = 0
            while size <= MAX_ARCHIVE:
                if time.monotonic() > deadline:
                    raise CheckError("download_failed")
                chunk = response.read(min(65536, MAX_ARCHIVE + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
        return unpack_payload(data, arch)
    except CheckError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise CheckError("download_failed") from exc


def parse_status(output):
    values = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in {"node_state", "node_firewall", "node_version"} or key in values:
            raise CheckError("invalid_node_response")
        values[key] = value
    if (set(values) != {"node_state", "node_firewall", "node_version"}
            or values["node_state"] not in STATES
            or values["node_firewall"] not in {"restricted", "absent", "partial"}
            or values["node_version"] != VERSION):
        raise CheckError("invalid_node_response")
    values["node_checked_at"] = datetime.now(timezone.utc).isoformat()
    # Running is a process observation, never proof of a successful scrape.
    values["prometheus_scrape_verified"] = False
    return values


class NodeManager:
    """Caller serializes each satellite's operations, including manual checks."""
    def __init__(self, transport, executor):
        self.transport = transport
        self.executor = executor

    async def execute(self, settings, action):
        if action not in ACTIONS:
            raise ValueError("Invalid action")
        try:
            host, collector, port = settings_for_node(settings)
        except (ValueError, KeyError):
            raise CheckError("configure_node_exporter") from None
        try:
            async with asyncio.timeout(150 if action == "deploy" else 35):
                facts = parse_probe(await self.transport(settings, PROBE))
                arch = facts["arch"]
                # Marker + tested kernel families + remote command preflight are required.
                if not ((arch == "armv7l" and facts["kernel"].startswith("3.18."))
                        or (arch == "aarch64" and facts["kernel"].startswith("4.9."))):
                    raise CheckError("unsupported_firmware")
                script = await self.executor(
                    (Path(__file__).with_name("node_manager.sh")).read_text, "utf-8")
                digest = PAYLOADS[arch][2]
                def command(verb, size=0):
                    return "sh -c " + shlex.quote(script) + " -- " + " ".join(
                        shlex.quote(str(item)) for item in
                        (verb, host, collector, port, arch, digest, size))
                # Inspect first; downloads and mutations are explicit actions only.
                state = parse_status(await self.transport(settings, command("inspect")))
                if action == "inspect":
                    return state
                if state["node_state"] in {"failed", "unmanaged"}:
                    raise CheckError("node_requires_review")
                payload = None
                if action == "deploy" and state["node_state"] == "not_installed":
                    payload = await self.executor(download_payload, arch)
                return parse_status(await self.transport(
                    settings, command(action, len(payload) if payload else 0), payload))
        except TimeoutError:
            raise CheckError("node_timeout_check_before_retry") from None
