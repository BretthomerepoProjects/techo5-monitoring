"""Build reviewed static payloads with Zig 0.13.0; never package local config."""
import ast
import hashlib
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
output=root/'dist';output.mkdir(exist_ok=True)
version=subprocess.check_output([sys.executable,'-m','ziglang','version'],text=True).strip()
if version!='0.13.0':raise SystemExit('Use ziglang==0.13.0 in an isolated build environment')
tree=ast.parse((root/'custom_components/techo5_satellite_manager/streamer.py').read_text())
pins=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PAYLOADS' for t in n.targets))
for arch,target,cpu in [('armv7l','arm-linux-musleabihf',['-mcpu=cortex_a7']),('aarch64','aarch64-linux-musl',[])]:
    name,digest=pins[arch]
    args=[sys.executable,'-m','ziglang','cc','-target',target,*cpu,'-Os','-static','-s','-Wall','-Wextra','-Werror',str(root/'native/streamer.c'),'-o',str(output/name)]
    subprocess.run(args,check=True)
    actual=hashlib.sha256((output/name).read_bytes()).hexdigest()
    if actual!=digest:raise SystemExit('Build does not match reviewed payload: '+name)
    (output/(name+'.sha256')).write_text(actual+'  '+name+'\n')
    print(name,(output/name).stat().st_size,'bytes',actual)
