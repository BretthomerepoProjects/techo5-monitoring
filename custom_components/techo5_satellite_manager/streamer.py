"""Pinned native log streamer delivery and scalar-only status parsing."""
import asyncio
import hashlib
import http.client
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from .core import CheckError, PROBE, parse_probe, validate_host
from .node_exporter import script_request, ACTIONS
from .recovery import RecoveryPolicy

VERSION = "0.1.1"
RELEASE = "0.4.1"
PAYLOADS = {'armv7l': ('t5-log-stream-linux-armv7', 'debe4964e74a811381f3ae4962a506a2ea2661a91e22998dd31eae7f42dbb42c'), 'aarch64': ('t5-log-stream-linux-arm64', '73a38f6c1bf54eba11c2932d38f9f2eb2d9fb6de83ff0d12785e2ff848176e39')}
COUNTERS = {"connected", "written", "dropped", "truncated", "kernel_lost",
            "kernel_records", "read_errors", "files", "kernel_open", "queue", "uptime", "cpu_us", "file_gaps"}


def stream_device_name(settings):
    """A bounded ASCII syslog hostname from the integration's configured name."""
    name = settings.get("name") or settings["host"]
    if not isinstance(name, str):
        raise ValueError("Invalid device name")
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    if not token:
        token = "satellite-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    if len(token) > 63:
        token = token[:54] + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    return token


def settings_for_stream(settings):
    host = validate_host(settings["host"])
    collector = validate_host(settings.get("log_host", ""))
    port = settings.get("log_port", 1514)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Invalid log destination port")
    if host == collector or not settings.get("log_consent", False):
        raise ValueError("Review log forwarding first")
    return host, collector, port


def download_stream(arch):
    name, digest = PAYLOADS[arch]
    url = f"https://github.com/BretthomerepoProjects/techo5-monitoring/releases/download/v{RELEASE}/{name}"
    class VerifiedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://"):
                raise CheckError("download_failed")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        deadline=time.monotonic()+40
        with urllib.request.build_opener(VerifiedRedirect()).open(url, timeout=15) as response:
            chunks=[];size=0
            while size<=2097152:
                if time.monotonic()>deadline: raise CheckError("download_failed")
                chunk=response.read(min(65536,2097153-size))
                if not chunk: break
                chunks.append(chunk);size+=len(chunk)
        payload=b"".join(chunks)
    except (OSError, ValueError, http.client.HTTPException):
        raise CheckError("download_failed") from None
    if not payload or len(payload)>2097152 or hashlib.sha256(payload).hexdigest()!=digest:
        raise CheckError("binary_checksum_failed")
    return payload


def parse_stream_status(output):
    values={}
    for line in output.splitlines():
        key,sep,value=line.partition("=")
        if not sep or key in values: raise CheckError("invalid_stream_response")
        if key in {"stream_state","stream_profile","stream_version"}: values[key]=value
        elif key.removeprefix("stream_") in COUNTERS and key.startswith("stream_"):
            if not value.isascii() or not value.isdecimal() or len(value)>20:
                raise CheckError("invalid_stream_response")
            values[key]=int(value)
        else: raise CheckError("invalid_stream_response")
    if (values.get("stream_state") not in {"not_installed","stopped","running","invalid","unmanaged"}
        or values.get("stream_profile") not in {"dot","screen"}
        or values.get("stream_version")!=VERSION): raise CheckError("invalid_stream_response")
    for name in ("connected","kernel_open"):
        if values.get("stream_"+name,0) not in (0,1): raise CheckError("invalid_stream_response")
    values["stream_checked_at"]=datetime.now(timezone.utc).isoformat()
    values["log_ingestion_verified"]=False
    return values


class StreamManager:
    def __init__(self,transport,executor): self.transport=transport;self.executor=executor

    async def execute(self,settings,action):
        if action not in ACTIONS: raise ValueError("Invalid action")
        try:
            host,collector,port=settings_for_stream(settings)
            device_name=stream_device_name(settings)
        except (ValueError,KeyError): raise CheckError("configure_log_streamer") from None
        try:
            async with asyncio.timeout(100 if action=="deploy" else 35):
                facts=parse_probe(await self.transport(settings,PROBE));arch=facts["arch"]
                if not ((arch=="armv7l" and facts["kernel"].startswith("3.18.")) or
                        (arch=="aarch64" and facts["kernel"].startswith("4.9."))):
                    raise CheckError("unsupported_firmware")
                if arch not in PAYLOADS: raise CheckError("stream_build_unavailable")
                script=await self.executor(Path(__file__).with_name("stream_manager.sh").read_text,"utf-8")
                async def run(verb,payload=None):
                    cmd,data=script_request(script,verb,device_name,collector,port,arch,PAYLOADS[arch][1],payload)
                    return parse_stream_status(await self.transport(settings,cmd,data))
                state=await run("inspect")
                if action=="inspect": return state
                if state["stream_state"]=="unmanaged": raise CheckError("unmanaged_streamer")
                payload=None
                if action=="deploy" and state["stream_state"] in {"not_installed","invalid"}:
                    payload=await self.executor(download_stream,arch)
                return await run(action,payload)
        except TimeoutError: raise CheckError("stream_timeout_check_before_retry") from None


class StreamRecoveryPolicy(RecoveryPolicy):
    async def maintain(self,manager,settings,observed):
        state=observed.get("stream_state")
        if state=="running": self.reset();return observed
        action={"stopped":"start","not_installed":"deploy","invalid":"deploy"}.get(state)
        if action is None: raise CheckError("stream_requires_review")
        if self.clock()<self.next_attempt:return observed
        self.last_action=action
        result=await manager.execute(settings,action)
        if result.get("stream_state")!="running":raise CheckError("recovery_not_running")
        self.reset();return result

    def attributes(self,enabled):
        return {key.replace("node_","stream_",1):value for key,value in super().attributes(enabled).items()}
