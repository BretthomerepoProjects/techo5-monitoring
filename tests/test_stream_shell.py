"""Real streamer manager in a synthetic POSIX tree, never on a satellite."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import unittest
import test_node_shell


@unittest.skipUnless(os.name=='posix' and shutil.which('sh'),'Requires POSIX shell')
class StreamShell(unittest.TestCase):
    def setUp(self):
        self.f=test_node_shell.ShellLifecycle();self.f.setUp()
        f=self.f;f.install=f.base/'streamer';f.env['T5SM_INSTALL']=str(f.install)
        run=f.root/'run';app=f.data/'techo5-linux';app.mkdir();(app/'techo5.log').touch()
        kmsg=f.root/'kmsg';kmsg.touch()
        src=Path(__file__).resolve().parents[1]/'custom_components/techo5_satellite_manager/stream_manager.sh'
        f.script=(src.read_text().replace('/data',str(f.data)).replace('/run/t5sm-streamer',str(run))
                  .replace('/etc/techo5/boot.sh',str(f.boot)).replace('/etc/inittab',str(f.root/'inittab'))
                  .replace('/dev/kmsg',str(kmsg)).replace('[ -c ', '[ -f '))
        f.tool('stat', '''#!/usr/bin/python3
import os,stat,sys
s=os.stat(sys.argv[-1]);m=format(stat.S_IMODE(s.st_mode),'o')
print({'%u':'0','%a':m,'%u:%a':'0:'+m,'%u:%h':'0:'+str(s.st_nlink),'%s':str(s.st_size)}[sys.argv[2]])
''')
        f.tool('pidof', (f.tools/'pidof').read_text().replace('/node_exporter','/t5-log-stream'))

    def tearDown(self):
        f=self.f
        for path in Path('/proc').glob('[0-9]*/exe'):
            try:
                if os.readlink(path)==str(f.install/'t5-log-stream'):os.kill(int(path.parent.name),signal.SIGTERM)
            except OSError:pass
        f.temp.cleanup()

    def test_readonly_then_full_lifecycle_no_firewall_changes(self):
        f=self.f;self.assertIn('stream_state=not_installed',f.call('inspect'));self.assertFalse(f.base.exists())
        self.assertIn('stream_state=running',f.call('deploy',f.payload))
        pid=(f.install/'pid').read_text();f.call('start');self.assertEqual(pid,(f.install/'pid').read_text())
        f.call('stop');self.assertIn('stream_state=stopped',f.call('inspect'))
        f.call('start');f.call('uninstall');self.assertFalse(f.install.exists())
        self.assertEqual(json.loads(f.rules.read_text()),[['unrelated-existing-rule']])

    def test_checksum_unmanaged_symlink_and_lock_refuse(self):
        f=self.f
        self.assertIn('binary_checksum_failed',f.call('deploy',b'bad',expect=1))
        self.assertFalse((f.install/'t5-log-stream').exists())
        f.env['T5SM_FOREIGN']='yes'
        self.assertIn('unmanaged_streamer',f.call('start',expect=1));del f.env['T5SM_FOREIGN']
        (f.base/'operation.lock').mkdir()
        self.assertIn('remote_busy',f.call('start',expect=1));(f.base/'operation.lock').rmdir()
        (f.install/'config').symlink_to(f.root/'external')
        self.assertIn('unsafe_path',f.call('inspect',expect=1))

    def test_endpoint_change_requires_stop_and_deploy(self):
        f=self.f;f.call('deploy',f.payload)
        self.assertIn('configuration_changed_stop_first',f.call('start',collector='10.77.0.21',expect=1))
        f.call('stop');f.call('deploy',collector='10.77.0.21');f.call('uninstall')

    def test_configured_name_is_saved_and_rename_requires_stop(self):
        f=self.f
        f.script=f.script.replace('action=$1 host=$2','action=$1 host=Show8-1')
        f.call('deploy',f.payload)
        self.assertEqual((f.install/'config').read_text().split()[-1],'Show8-1')
        f.script=f.script.replace('host=Show8-1','host=Show8-2')
        self.assertIn('configuration_changed_stop_first',f.call('start',expect=1))
        f.call('stop');f.call('deploy')
        self.assertEqual((f.install/'config').read_text().split()[-1],'Show8-2')
        f.call('uninstall')

    def test_known_predecessor_upgrade_validates_before_stopping(self):
        f=self.f;f.call('deploy',f.payload)
        old_pid=(f.install/'pid').read_text()
        # Simulate the released four-field manifest and reviewed predecessor pin.
        config=f.install/'config'
        config.write_text(' '.join(config.read_text().split()[:4])+'\n')
        old_digest=f.digest
        f.script=f.script.replace('7e5790102e7952039cfece8bc660e392f0f5f3ae6c0a516a66bb991be2086559',old_digest)
        old_payload=f.payload;f.payload+=b'\0';f.digest=hashlib.sha256(f.payload).hexdigest()
        self.assertIn('stream_state=invalid',f.call('inspect'))
        self.assertIn('binary_checksum_failed',f.call('deploy',b'bad',expect=1))
        self.assertEqual((f.install/'pid').read_text(),old_pid)
        self.assertTrue(Path('/proc/'+old_pid.split()[0]).exists())
        self.assertIn('stream_state=running',f.call('deploy',f.payload))
        self.assertNotEqual((f.install/'pid').read_text(),old_pid)
        self.assertEqual((f.install/'previous').read_bytes(),old_payload)
        self.assertEqual(config.read_text().split()[-1],'10.77.0.10')
        f.call('uninstall')

    def test_predecessor_stop_and_unknown_manifest_refusal(self):
        f=self.f;f.call('deploy',f.payload)
        config=f.install/'config';old_digest=f.digest
        f.script=f.script.replace('7e5790102e7952039cfece8bc660e392f0f5f3ae6c0a516a66bb991be2086559',old_digest)
        f.digest='0'*64
        self.assertIn('stream_state=invalid',f.call('stop'))
        saved=config.read_text();config.write_text(saved.replace(old_digest,'1'*64))
        self.assertIn('invalid_config',f.call('deploy',f.payload,expect=1))
        config.write_text(saved);f.call('uninstall')

    def test_external_supervisor_is_not_modified(self):
        f=self.f;p=f.root/'inittab';p.write_text('pilot:respawn:/example/t5-log-stream\n')
        self.assertIn('external_supervisor',f.call('deploy',f.payload,expect=1))
        self.assertEqual(p.read_text(),'pilot:respawn:/example/t5-log-stream\n')
