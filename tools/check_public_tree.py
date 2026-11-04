"""Publication guard: examine staged/tracked content, not ignored local files."""
import ipaddress
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True)
files = [name for name in result.stdout.decode().split('\0') if name]
if not files:
    raise SystemExit('No staged/tracked files to review')
blocked_parts = {'.storage', 'credentials', 'ssh', 'backups', 'captures', 'data', 'node_modules', '__pycache__'}
blocked_suffixes = {'.key', '.pem', '.ppk', '.pub', '.log', '.jsonl', '.db', '.sqlite', '.csv', '.pcap', '.zip', '.tar', '.gz'}
secret = re.compile(r'(?:-----BEGIN (?:OPENSSH|RSA|EC|DSA|PRIVATE)|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{24,}|(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp\d+)\s+[A-Za-z0-9+/]{30,})')
ipv4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
issues = []
for name in files:
    path = Path(name)
    if any(part in blocked_parts for part in path.parts) or path.suffix in blocked_suffixes or path.name.startswith(('.env', 'id_rsa', 'id_ed25519', 'authorized_keys', 'known_hosts')):
        issues.append(name + ': prohibited file path/type')
        continue
    # Inspect exactly what will be committed, including stale staged copies.
    data = subprocess.run(['git', 'show', ':' + name], cwd=root, check=True, capture_output=True).stdout
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        issues.append(name + ': unexpected binary file')
        continue
    if secret.search(text):
        issues.append(name + ': possible credential (value withheld)')
    if re.search(r'(?:[A-Z]:[\\/]Users[\\/]|/home/[A-Za-z0-9_-]+/)', text):
        issues.append(name + ': user-specific host path')
    for match in ipv4.finditer(text):
        address = ipaddress.ip_address(match.group())
        # Network definitions and loopback tests are generic. Test-only synthetic targets
        # use a dedicated example subnet; real deployment addresses belong in local config.
        is_definition = text[match.end():].startswith(('/8', '/12', '/16')) and str(address).endswith('.0')
        synthetic_test = name.startswith('tests/') and (address in ipaddress.ip_network('10.77.0.0/24') or address.is_link_local)
        policy_definition = name == 'tools/check_public_tree.py' and str(address) == '10.77.0.0'
        if address.is_private and not address.is_loopback and not is_definition and not synthetic_test and not policy_definition:
            issues.append(name + ': embedded private network address (value withheld)')
            break
if issues:
    print('\n'.join(issues))
    sys.exit(1)
print(f'Publication guard passed for {len(files)} staged/tracked text files. Manual diff review still required.')
