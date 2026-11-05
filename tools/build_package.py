"""Build an archive from an explicit source allowlist, never runtime config."""
import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
component = root / 'custom_components/techo5_satellite_manager'
version = json.loads((component / 'manifest.json').read_text())['version']
files = {'__init__.py', 'button.py', 'config_flow.py', 'core.py', 'sensor.py', 'ssh.py',
         'streamer.py', 'stream_manager.sh', 'streamer-NOTICES.txt', 'bootstrap.py', 'ha_bootstrap.py', 'node_exporter.py', 'node_manager.sh', 'recovery.py', 'switch.py', 'manifest.json', 'services.yaml', 'strings.json', 'translations/en.json', 'brand/icon.png'}
output = root / 'dist' / f'techo5-satellite-manager-{version}.zip'
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(files):
        path = component / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or symlinked source file: ' + name)
        archive.write(path, path.relative_to(root).as_posix())
digest = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix('.zip.sha256').write_text(digest + '  ' + output.name + '\n')
print(output.name, 'SHA256', digest)
