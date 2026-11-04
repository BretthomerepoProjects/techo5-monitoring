"""Strict host verification and bounded stdin transfer, without SCP/SFTP."""
import asyncio
import asyncssh
from .core import CheckError, MAX_OUTPUT, credential_paths, validate_host

NODE_ERRORS = {
    "root_required", "unsupported_firmware", "architecture_changed", "invalid_action",
    "missing_tools", "unsafe_path", "binary_checksum_failed", "invalid_config",
    "unmanaged_exporter", "external_supervisor", "legacy_iptables_required",
    "configuration_changed_stop_first", "remote_busy", "invalid_payload_size",
    "insufficient_storage", "upload_failed", "incomplete_upload", "not_installed",
    "start_failed", "process_changed", "stop_failed", "unexpected_files",
    "firewall_requires_review",
    "exporter_port_in_use",
    "cannot_check_listener", "firewall_backup_failed",
}


def load_material(config_dir, key_name):
    key_path, hosts_path = credential_paths(config_dir, key_name)
    return (asyncssh.import_private_key(key_path.read_bytes()),
            asyncssh.import_known_hosts(hosts_path.read_text(encoding="utf-8")))


def connection_options(config_dir, settings):
    key, hosts = load_material(config_dir, settings["key_name"])
    # Options preparation may touch default paths: always run off the HA loop.
    return asyncssh.SSHClientConnectionOptions(
        config=None, port=settings["port"], username="root", client_keys=[key],
        client_host_keys=[], known_hosts=hosts, agent_path=None, host_based_auth=False,
        password_auth=False, kbdint_auth=False, agent_forwarding=False,
        connect_timeout=8, login_timeout=8, encoding=None,
    )


async def probe(hass, settings, command, input_data=None):
    if input_data is not None and len(input_data) > 32 * 1024 * 1024:
        raise CheckError("invalid_payload_size")
    phase = "credentials"
    try:
        options = await hass.async_add_executor_job(
            connection_options, hass.config.config_dir, settings)
        phase = "connect"
        async with asyncssh.connect(validate_host(settings["host"]), options=options) as connection:
            phase = "command"
            async with connection.create_process(command, stderr=asyncssh.DEVNULL) as process:
                async def send():
                    if input_data:
                        for offset in range(0, len(input_data), 65536):
                            process.stdin.write(input_data[offset:offset + 65536])
                            await process.stdin.drain()
                    process.stdin.write_eof()
                # Dropbear can finish a short read-only command before a scheduled
                # writer runs. Do not send EOF on commands which need no input.
                writer = asyncio.create_task(send()) if input_data is not None else None
                try:
                    chunks = []
                    length = 0
                    while True:
                        chunk = await process.stdout.read(min(1024, MAX_OUTPUT + 1 - length))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        length += len(chunk)
                        if length > MAX_OUTPUT:
                            raise CheckError("output_limit")
                    await process.wait_closed()
                    output = b"".join(chunks).decode("utf-8")
                    if process.exit_status != 0:
                        code = output.strip().removeprefix("node_error=")
                        raise CheckError(code if code in NODE_ERRORS else "probe_failed")
                    if writer is not None:
                        await writer
                    return output
                finally:
                    if writer is not None:
                        writer.cancel()
                        await asyncio.gather(writer, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except CheckError:
        raise
    except asyncssh.HostKeyNotVerifiable as exc:
        raise CheckError("host_key_rejected") from exc
    except asyncssh.PermissionDenied as exc:
        raise CheckError("authentication_failed") from exc
    except (OSError, ValueError, asyncssh.Error) as exc:
        if phase == "credentials":
            code = "credential_file_unavailable" if isinstance(exc, OSError) else "credential_files_invalid"
        elif isinstance(exc, ConnectionRefusedError):
            code = "ssh_connection_refused"
        elif isinstance(exc, TimeoutError):
            code = "ssh_connect_timeout"
        elif type(exc).__name__ == "KeyExchangeFailed":
            code = "ssh_negotiation_failed"
        elif type(exc).__name__ == "ConnectionLost":
            code = "ssh_connection_lost"
        elif phase == "command":
            code = "ssh_channel_failed"
        else:
            code = "ssh_network_failed"
        raise CheckError(code) from exc
