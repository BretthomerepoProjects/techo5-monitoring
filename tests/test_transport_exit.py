"""A remote command may finish before the client schedules its stdin writer."""
from contextlib import asynccontextmanager
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from test_ssh import ssh, core
from test_core import GOOD


class FastExit(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_success_does_not_write_to_closed_stdin(self):
        stdin = types.SimpleNamespace(write_eof=Mock(side_effect=OSError("synthetic closed channel")))
        process = types.SimpleNamespace(stdin=stdin, exit_status=0,
            stdout=types.SimpleNamespace(read=AsyncMock(side_effect=[GOOD.encode(), b""])),
            wait_closed=AsyncMock())
        @asynccontextmanager
        async def context(value):
            yield value
        connection = types.SimpleNamespace(create_process=lambda *args, **kwargs: context(process))
        hass = types.SimpleNamespace(config=types.SimpleNamespace(config_dir="unused"),
            async_add_executor_job=AsyncMock(return_value=object()))
        settings = {"host": "10.77.0.10", "port": 22, "key_name": "test_key"}
        with patch.object(ssh.asyncssh, "connect", return_value=context(connection)):
            self.assertEqual(await ssh.probe(hass, settings, core.PROBE), GOOD)
        stdin.write_eof.assert_not_called()

    async def test_missing_credentials_have_a_specific_sanitized_error(self):
        hass = types.SimpleNamespace(config=types.SimpleNamespace(config_dir="unused"),
            async_add_executor_job=AsyncMock(side_effect=PermissionError("synthetic private detail")))
        with self.assertRaisesRegex(core.CheckError, "^credential_file_unavailable$"):
            await ssh.probe(hass, {"host": "10.77.0.10"}, core.PROBE)

    async def test_refused_connection_is_distinct_from_credential_failure(self):
        hass = types.SimpleNamespace(config=types.SimpleNamespace(config_dir="unused"),
            async_add_executor_job=AsyncMock(return_value=object()))
        with patch.object(ssh.asyncssh, "connect", side_effect=ConnectionRefusedError("synthetic detail")):
            with self.assertRaisesRegex(core.CheckError, "^ssh_connection_refused$"):
                await ssh.probe(hass, {"host": "10.77.0.10"}, core.PROBE)
