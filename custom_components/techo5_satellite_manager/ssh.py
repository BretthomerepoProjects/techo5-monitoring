"""Strict host verification; no passwords, agents, user SSH config or forwarding."""
import asyncio
import asyncssh
from .core import CheckError, MAX_OUTPUT, credential_paths, validate_host


def load_material(config_dir, key_name):
    key_path, hosts_path = credential_paths(config_dir, key_name)
    return (asyncssh.import_private_key(key_path.read_bytes()),
            asyncssh.import_known_hosts(hosts_path.read_text(encoding="utf-8")))


async def probe(hass, settings, command):
    try:
        key, hosts = await hass.async_add_executor_job(
            load_material, hass.config.config_dir, settings["key_name"])
        async with asyncssh.connect(
            validate_host(settings["host"]), port=settings["port"], username="root",
            client_keys=[key], known_hosts=hosts, config=None, agent_path=None,
            password_auth=False, kbdint_auth=False, agent_forwarding=False,
            connect_timeout=8, login_timeout=8, encoding="utf-8",
        ) as connection:
            async with connection.create_process(command, stderr=asyncssh.DEVNULL) as process:
                # read(n) may return a partial chunk; accumulate with an absolute cap.
                chunks = []
                length = 0
                while True:
                    chunk = await process.stdout.read(min(1024, MAX_OUTPUT + 1 - length))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    length += len(chunk.encode())
                    if length > MAX_OUTPUT:
                        raise CheckError("output_limit")
                await process.wait_closed()
                if process.exit_status != 0:
                    raise CheckError("probe_failed")
                return "".join(chunks)
    except asyncio.CancelledError:
        raise
    except CheckError:
        raise
    except asyncssh.HostKeyNotVerifiable as exc:
        raise CheckError("host_key_rejected") from exc
    except asyncssh.PermissionDenied as exc:
        raise CheckError("authentication_failed") from exc
    except (OSError, ValueError, asyncssh.Error) as exc:
        raise CheckError("connection_or_credentials_failed") from exc
