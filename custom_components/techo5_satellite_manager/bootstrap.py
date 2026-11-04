"""Public-key provisioning and explicit SSH host trust; no private-key export."""
import asyncio
import ipaddress
import os
from pathlib import Path
import threading
import asyncssh

_HOSTS_LOCK = threading.Lock()


class BootstrapError(Exception):
    """A safe user-facing code, never raw credential/connection text."""


def key_path(config_dir, key_name):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", key_name):
        raise BootstrapError('invalid_key')
    base = Path(config_dir).resolve() / 'satellite_manager/ssh'
    path = base / key_name
    if base.resolve() != base or path.resolve() != path or not path.is_file() or path.stat().st_size > 65536:
        raise BootstrapError('invalid_key')
    return path


def public_identity(config_dir, key_name, extra_keys=''):
    try:
        private = asyncssh.import_private_key(key_path(config_dir, key_name).read_bytes())
        public = private.export_public_key().decode().strip()
        keys = [public]
        for line in extra_keys.splitlines():
            if line.strip():
                keys.append(asyncssh.import_public_key(line.strip()).export_public_key().decode().strip())
        keys = list(dict.fromkeys(keys))
        if len(keys) > 16 or sum(map(len, keys)) > 32768:
            raise ValueError
        return '\n'.join(keys) + '\n', private.get_fingerprint()
    except (OSError, ValueError, asyncssh.Error) as exc:
        raise BootstrapError('invalid_key') from exc


def host_trust(config_dir, host, port, server_key, *, accept=False):
    """Never replace a saved host key; first-use additions require explicit UI trust."""
    host = str(ipaddress.IPv4Address(host))
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise BootstrapError('invalid_key')
    base = Path(config_dir).resolve() / 'satellite_manager/ssh'
    path = base / 'known_hosts'
    if base.resolve() != base or path.resolve() != path:
        raise BootstrapError('invalid_key')
    with _HOSTS_LOCK:
        if path.exists():
            if path.stat().st_size > 65536:
                raise BootstrapError('invalid_key')
            content = path.read_text(encoding='utf-8')
        else:
            content = ''
        matches = asyncssh.import_known_hosts(content).match(host, host, port)
        trusted, authorities, revoked = matches[:3]
        if server_key in revoked:
            raise BootstrapError('host_key_changed')
        if server_key in trusted:
            return True
        if trusted or authorities or any(matches[3:]):
            raise BootstrapError('host_key_changed')
        if not accept:
            return False
        name = host if port == 22 else f'[{host}]:{port}'
        line = name + ' ' + server_key.export_public_key().decode().strip() + '\n'
        if len(content.encode()) + len(line.encode()) > 65536:
            raise BootstrapError('invalid_key')
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, 'a', encoding='utf-8') as output:
            if content and not content.endswith('\n'):
                output.write('\n')
            output.write(line)
            output.flush()
            os.fsync(output.fileno())
        return True


async def prepare(backend, public_keys, *, confirmed):
    """The operator-confirmed order: validate -> enable -> push -> listen -> host key."""
    if not confirmed:
        raise BootstrapError('confirmation_required')
    if not public_keys.strip() or len(public_keys) > 32768:
        raise BootstrapError('invalid_key')
    async with asyncio.timeout(35):
        await backend.validate()
        await backend.enable_ssh()
        await backend.push_keys(public_keys)
        await backend.wait_listening()
        return await backend.server_key()
