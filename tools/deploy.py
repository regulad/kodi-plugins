"""Use OpenSSH's agent/askpass support; never pass a passphrase in argv or logs."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import uuid


def key_bytes(value):
    value = value.replace('\\n', '\n').strip()
    if not value.startswith('-----BEGIN OPENSSH PRIVATE KEY-----'):
        value = base64.b64decode(value, validate=True).decode('ascii').strip()
    if not value.startswith('-----BEGIN OPENSSH PRIVATE KEY-----') or not value.endswith('-----END OPENSSH PRIVATE KEY-----'):
        raise ValueError('SSH_KEY_BLOB must contain an OpenSSH private key')
    return (value + '\n').encode('ascii')


def resolve_host(host):
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('Invalid SSH_KEY_HOST')
    query = host.rstrip('.')
    if '.' not in query:
        status = json.loads(subprocess.check_output(['tailscale', 'status', '--json']))
        suffix = status.get('MagicDNSSuffix', '')
        if suffix:
            query += '.' + suffix
    for record in ('A', 'AAAA'):
        answer = subprocess.check_output(['dig', '@100.100.100.100', '+short', '+time=5', '+tries=2', query, record], text=True)
        for line in answer.splitlines():
            try:
                return str(ipaddress.ip_address(line.strip()))
            except ValueError:
                continue
    raise RuntimeError('Host did not resolve through Tailscale DNS')


def remote_script(base, release):
    base = base.rstrip('/')
    if not base.startswith('/') or '..' in base.split('/') or any(c in base for c in '\r\n\0'):
        raise ValueError('STATIC_FILE_BASE must be an absolute non-root directory')
    # Keep older versioned packages for clients that still have an older catalog.
    # Only this deployment's incoming folder is removed; releases remain available.
    return '''set -eu
umask 022
base=%s
release=%s
mkdir -p "$base/.releases"
if [ -e "$base/helix" ] && [ ! -L "$base/helix" ]; then
  echo 'Refusing to replace an existing non-symlink helix directory' >&2; exit 1
fi
incoming="$base/.releases/$release.incoming"
target="$base/.releases/$release"
mkdir "$incoming" "$target"
trap 'rm -rf -- "$incoming"' EXIT
tar -xf - -C "$incoming"
if [ -d "$base/helix" ]; then
  cp -a "$base/helix/." "$target/"
  for package in "$incoming"/*/*.zip; do
    rel=${package#"$incoming/"}
    if [ -f "$target/$rel" ] && ! cmp -s "$package" "$target/$rel"; then
      echo 'Published package changed without a version bump' >&2; exit 1
    fi
  done
fi
cp -a "$incoming/." "$target/"
cd "$target"
md5sum -c addons.xml.md5
for checksum in */*.zip.md5; do
  (cd "$(dirname "$checksum")"; md5sum -c "$(basename "$checksum")")
done
ln -s ".releases/$release" "$base/.helix-$release"
mv -Tf "$base/.helix-$release" "$base/helix"
echo 'Helix repository published'
''' % (shlex.quote(base), shlex.quote(release))


def deploy(dist):
    names = ('SSH_KEY_HOST', 'SSH_KEY_USER', 'SSH_KEY_BLOB', 'SSH_KEY_PASSPHRASE', 'STATIC_FILE_BASE')
    secrets = {name: os.environ[name] for name in names}
    if any(not secrets[name] for name in names):
        raise ValueError('A required SSH deployment secret is empty')
    host = secrets['SSH_KEY_HOST'].strip()
    address = resolve_host(host)
    user = secrets['SSH_KEY_USER']
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
        raise ValueError('Invalid SSH_KEY_USER')
    script = remote_script(secrets['STATIC_FILE_BASE'], 'helix-' + uuid.uuid4().hex)
    with tempfile.TemporaryDirectory(prefix='kodi-ssh-') as folder:
        folder = Path(folder)
        key = folder / 'identity'
        key.write_bytes(key_bytes(secrets['SSH_KEY_BLOB']))
        key.chmod(0o600)
        askpass = folder / 'askpass'
        askpass.write_text('#!/usr/bin/env python3\nimport os, sys\nsys.stdout.write(os.environ["SSH_KEY_PASSPHRASE"] + "\\n")\n')
        askpass.chmod(0o700)
        env = os.environ.copy()
        agent = subprocess.check_output(['ssh-agent', '-s'], text=True)
        env.update(dict(re.findall(r'(SSH_AUTH_SOCK|SSH_AGENT_PID)=([^;]+);', agent)))
        env.update(SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE='force', DISPLAY=':0')
        try:
            subprocess.run(['ssh-add', str(key)], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=30)
            public = subprocess.check_output(['ssh-add', '-L'], env=env)
            (folder / 'identity.pub').write_bytes(public)
            key.unlink()
            env.pop('SSH_KEY_PASSPHRASE', None)
            env.pop('SSH_KEY_BLOB', None)
            bundle = folder / 'repository.tar'
            with tarfile.open(bundle, 'w') as archive:
                for path in sorted(Path(dist).rglob('*')):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(dist).as_posix())
            command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=30',
                       '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
                       '-o', 'StrictHostKeyChecking=accept-new',
                       '-o', 'UserKnownHostsFile=' + str(folder / 'known_hosts'),
                       '-o', 'HostKeyAlias=' + host, '-o', 'IdentitiesOnly=yes',
                       '-i', str(folder / 'identity.pub'), '-l', user, address,
                       'sh -c ' + shlex.quote(script)]
            with bundle.open('rb') as stream:
                subprocess.run(command, env=env, stdin=stream, check=True, timeout=300)
        finally:
            subprocess.run(['ssh-agent', '-k'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    try:
        deploy(sys.argv[1])
    except subprocess.CalledProcessError as error:
        # Do not print command arguments: the remote command includes a secret path.
        sys.exit('Deployment subprocess failed (exit %s)' % error.returncode)
