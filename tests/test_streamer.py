import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock

COMP=Path(__file__).resolve().parents[1]/"custom_components/techo5_satellite_manager"
p=types.ModuleType("stream_unit");p.__path__=[str(COMP)];sys.modules[p.__name__]=p
from stream_unit.streamer import parse_stream_status, settings_for_stream, StreamRecoveryPolicy, stream_device_name
from stream_unit.core import CheckError

class Status(unittest.TestCase):
    def test_device_names_are_bounded_safe_syslog_tokens(self):
        self.assertEqual(stream_device_name({'name':'Show8-1'}), 'Show8-1')
        self.assertEqual(stream_device_name({'name':'Office Dot 2'}), 'Office-Dot-2')
        self.assertEqual(stream_device_name({'name':'bad\nforged header'}), 'bad-forged-header')
        self.assertEqual(stream_device_name({'host':'10.77.0.10'}), '10.77.0.10')
        token=stream_device_name({'name':'x'*100})
        self.assertEqual(len(token),63)
        self.assertNotEqual(token,stream_device_name({'name':'x'*99+'y'}))
        self.assertRegex(stream_device_name({'name':'\u2603'}),r'^satellite-[a-f0-9]{8}$')

    def test_scalar_status_never_implies_ingestion(self):
        out=parse_stream_status("stream_state=running\nstream_profile=dot\nstream_version=0.1.1\nstream_written=52\nstream_connected=1\n")
        self.assertEqual(out['stream_written'],52);self.assertFalse(out['log_ingestion_verified'])

    def test_rejects_records_unknown_fields_duplicates_and_invalid_counters(self):
        good="stream_state=running\nstream_profile=screen\nstream_version=0.1.1\n"
        for tail in ['log=private text\n','stream_connected=2\n','stream_written=-1\n','stream_state=stopped\n','stream_written=1.2\n']:
            with self.subTest(tail=tail),self.assertRaises(CheckError):parse_stream_status(good+tail)

    def test_settings_require_private_receiver_and_explicit_consent(self):
        s={'host':'10.77.0.10','log_host':'10.77.0.20','log_port':1514,'log_consent':True}
        self.assertEqual(settings_for_stream(s),('10.77.0.10','10.77.0.20',1514))
        for update in [{'log_consent':False},{'log_host':'8.8.8.8'},{'log_host':s['host']},{'log_port':True},{'log_port':80}]:
            with self.subTest(update=update),self.assertRaises(ValueError):settings_for_stream({**s,**update})

class Recovery(unittest.IsolatedAsyncioTestCase):
    async def test_running_disconnected_never_redeploys_and_missing_is_repaired(self):
        r=StreamRecoveryPolicy();manager=types.SimpleNamespace(execute=AsyncMock(return_value={'stream_state':'running'}))
        await r.maintain(manager,{}, {'stream_state':'running','stream_connected':0});manager.execute.assert_not_called()
        for state,action in [('stopped','start'),('not_installed','deploy'),('invalid','deploy')]:
            await r.maintain(manager,{}, {'stream_state':state});self.assertEqual(manager.execute.await_args.args[1],action)

    async def test_backoff_and_unmanaged_refusal(self):
        r=StreamRecoveryPolicy(clock=lambda:0);r.failed('offline')
        m=types.SimpleNamespace(execute=AsyncMock())
        await r.maintain(m,{}, {'stream_state':'stopped'});m.execute.assert_not_called()
        with self.assertRaises(CheckError):await r.maintain(m,{}, {'stream_state':'unmanaged'})
        self.assertIn('stream_recovery_next_retry',r.attributes(True))

if __name__=='__main__':unittest.main()


class Delivery(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_inspect_without_download_and_use_small_exec(self):
        import stream_unit.streamer as module
        from test_core import GOOD
        from unittest.mock import patch
        settings={"name":"Show8-1","host":"10.77.0.10","log_host":"10.77.0.20","log_consent":True}
        for arch,kernel,profile in [("armv7l","3.18.19-test","dot"),("aarch64","4.9.337-test","screen")]:
            calls=[]
            async def transport(settings,command,payload=None):
                calls.append((command,payload))
                if command==module.PROBE:return GOOD.replace("arch=aarch64","arch="+arch).replace("kernel=4.9.337-test","kernel="+kernel)
                return "stream_state=stopped\nstream_profile="+profile+"\nstream_version=0.1.1\n"
            with patch.object(module,"download_stream",side_effect=AssertionError("unexpected download")):
                r=await module.StreamManager(transport,asyncio.to_thread).execute(settings,"inspect")
            self.assertEqual(r['stream_profile'],profile)
            self.assertLess(len(calls[-1][0].encode()),1024)
            self.assertIn('Show8-1',calls[-1][0])
            self.assertIn(b'if [ "$action" = inspect ]',calls[-1][1])

    async def test_deploy_streams_only_verified_arch_payload_and_unmanaged_refuses(self):
        import stream_unit.streamer as module
        from test_core import GOOD
        from unittest.mock import patch
        settings={"name":"Show8-1","host":"10.77.0.10","log_host":"10.77.0.20","log_consent":True}
        calls=[]
        async def transport(settings,command,payload=None):
            calls.append((command,payload))
            if command==module.PROBE:return GOOD
            return "stream_state="+("not_installed" if len(calls)==2 else "running")+"\nstream_profile=screen\nstream_version=0.1.1\n"
        with patch.object(module,"download_stream",return_value=b"verified payload") as download:
            r=await module.StreamManager(transport,asyncio.to_thread).execute(settings,"deploy")
        download.assert_called_once_with('aarch64')
        self.assertTrue(calls[-1][1].endswith(b'verified payload'))
        self.assertEqual(r['stream_state'],'running')
        async def foreign(settings,command,payload=None):
            return GOOD if command==module.PROBE else 'stream_state=unmanaged\nstream_profile=screen\nstream_version=0.1.1\n'
        with patch.object(module,'download_stream',side_effect=AssertionError),self.assertRaisesRegex(CheckError,'unmanaged_streamer'):
            await module.StreamManager(foreign,asyncio.to_thread).execute(settings,'deploy')
