"""Real streamer manager in a synthetic POSIX tree, never on a satellite."""
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

    def test_external_supervisor_is_not_modified(self):
        f=self.f;p=f.root/'inittab';p.write_text('pilot:respawn:/example/t5-log-stream\n')
        self.assertIn('external_supervisor',f.call('deploy',f.payload,expect=1))
        self.assertEqual(p.read_text(),'pilot:respawn:/example/t5-log-stream\n')
