#!/usr/bin/env python3
"""Installation on a dedicated systemd Linux instance or Apple Silicon Mac host."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def settings(env):
    c = {k: env.get('AIFACTORY_' + k, '') for k in
         ('URL', 'WORKER', 'TOKEN', 'CA_B64', 'CA_FILE', 'SERVER_IP', 'MAC_IMAGE', 'MAC_BASE', 'MAC_GUEST')}
    u = urllib.parse.urlsplit(c['URL'])
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('AIFACTORY_URL must be an HTTPS origin without credentials, path or query')
    if not NAME.fullmatch(c['WORKER']):
        raise ValueError('AIFACTORY_WORKER must be a worker ID (1..64 letters, digits, underscores or hyphens)')
    if len(c['TOKEN'].strip()) < 32 or any(ch.isspace() for ch in c['TOKEN'].strip()):
        raise ValueError('AIFACTORY_TOKEN must be a dedicated enrolled worker token')
    c['URL'] = c['URL'].rstrip('/')
    if c['CA_B64'] and c['CA_FILE']:
        raise ValueError('Set only one of AIFACTORY_CA_B64 or AIFACTORY_CA_FILE')
    if c['SERVER_IP']:
        ipaddress.ip_address(c['SERVER_IP'])
    for k in ('MAC_BASE', 'MAC_GUEST'):
        if c[k] and not NAME.fullmatch(c[k]):
            raise ValueError('Invalid AIFACTORY_' + k)
    return c


def certificate(c):
    if c['CA_B64']:
        data = base64.b64decode(c['CA_B64'], validate=True)
    elif c['CA_FILE']:
        data = Path(c['CA_FILE']).expanduser().read_bytes()
    else:
        data = ''.join(ssl.DER_cert_to_PEM_cert(x) for x in ssl.create_default_context().get_ca_certs(binary_form=True)).encode()
    try:
        ssl.create_default_context(cadata=data.decode('ascii'))
    except (ssl.SSLError, UnicodeError):
        raise ValueError('Invalid CA certificate data') from None
    return data


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Refusing symlink: ' + str(path))
    tmp = path.with_name(path.name + '.install-pending')
    # Exclusive creation avoids following a stale or malicious temporary symlink.
    with tmp.open('xb') as f:
        os.chmod(tmp, 0o600)
        f.write(data)
    os.replace(tmp, path)


def download(url, destination, sha256=None):
    if not url.startswith('https://'):
        raise ValueError('Downloads require HTTPS')
    with urllib.request.urlopen(url, timeout=120) as r, destination.open('wb') as f:
        if not r.url.startswith('https://'):
            raise ValueError('Insecure download redirect')
        shutil.copyfileobj(r, f)
    if sha256 and hashlib.sha256(destination.read_bytes()).hexdigest() != sha256:
        raise ValueError('Download checksum mismatch')


def json_url(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def go_tool(tmp):
    print('Downloading and verifying the Go build toolchain...', flush=True)
    system = platform.system().lower()
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}[platform.machine()]
    releases = json_url('https://go.dev/dl/?mode=json')
    asset = next(f for release in releases if release['stable'] for f in release['files']
                 if f['os'] == system and f['arch'] == arch and f['kind'] == 'archive')
    archive = tmp / 'go.tgz'
    download('https://go.dev/dl/' + asset['filename'], archive, asset['sha256'])
    # Official Go archive verified against the HTTPS checksum manifest.
    with tarfile.open(archive) as tar:
        tar.extractall(tmp)
    return tmp / 'go/bin/go'


def build(tmp):
    go = go_tool(tmp)
    binaries = tmp / 'bin'
    binaries.mkdir()
    env = dict(os.environ, CGO_ENABLED='0', GOTOOLCHAIN='auto')
    for name in ('aifactory-worker', 'aifactory-computer'):
        run(go, 'build', '-trimpath', '-o', binaries / name, './cmd/' + name, cwd=SOURCE, env=env)
    return binaries


def existing_config(path, c):
    if path.exists():
        old = json.loads(path.read_text())
        if old['worker'] != c['WORKER'] or old['url'].rstrip('/') != c['URL']:
            raise ValueError('Existing installation belongs to another worker or endpoint; explicit migration required')
        if (Path(old['state_dir']) / 'guest-lease').exists():
            raise ValueError('Worker has an active lease; release it before installation')
        return old


def endpoint_hosts(c):
    if not c['SERVER_IP']:
        return
    host = urllib.parse.urlsplit(c['URL']).hostname
    # A host mapping is optional. No routes, firewall rules or global TLS trust are changed.
    path = Path('/etc/hosts')
    line = c['SERVER_IP'] + ' ' + host + ' # aifactory-worker\n'
    data = path.read_text()
    conflicting = [s for s in data.splitlines() if host in s.split('#')[0].split()[1:]]
    if conflicting and any(s.split()[0] != c['SERVER_IP'] for s in conflicting):
        raise ValueError('Existing hosts entry differs from AIFACTORY_SERVER_IP')
    if not conflicting:
        if platform.system() == 'Darwin':
            run('sudo', 'tee', '-a', path, input=line.encode(), stdout=subprocess.DEVNULL)
        else:
            with path.open('a') as f:
                f.write('\n' + line)
    template = Path('/etc/cloud/templates/hosts.debian.tmpl')
    if platform.system() == 'Linux' and template.exists() and line.strip() not in template.read_text():
        with template.open('a') as f:
            f.write('\n' + line)


def verify_connection(binary, c, ca, tmp):
    private_write(tmp / 'check.token', c['TOKEN'].strip().encode())
    private_write(tmp / 'check.crt', ca)
    private_write(tmp / 'check.json', json.dumps(dict(worker=c['WORKER'],url=c['URL'],
        token_file=str(tmp / 'check.token'),ca_file=str(tmp / 'check.crt'),state_dir=str(tmp / 'check-state'))).encode())
    endpoint_hosts(c)
    run(binary, '--config', tmp / 'check.json', '--check')


def linux(c, ca):
    if os.geteuid() != 0:
        raise ValueError('Linux installation requires root')
    if int(output('systemctl', '--version').split()[1]) < 254:
        raise ValueError('systemd >=254 required (Ubuntu 24.04 or Debian 13+)')
    if not shutil.which('apt-get'):
        raise ValueError('Automatic dependency installation currently supports apt-based Linux')
    root = Path('/etc/aifactory-worker')
    old = existing_config(root / 'config.json', c)
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', 'python3', 'python3-pil', 'xvfb', 'xauth', 'x11-utils', 'xdotool',
        'xclip', 'openbox', 'gmrun', 'dbus', 'git', 'gh', 'curl', 'ca-certificates', 'fonts-noto-cjk', 'mousepad')
    with tempfile.TemporaryDirectory(prefix='aifactory-build-') as directory:
        tmp = Path(directory)
        binaries = build(tmp)
        verify_connection(binaries / "aifactory-worker", c, ca, tmp)
        if not shutil.which('claude'):
            script = tmp / 'claude-install.sh'
            download('https://claude.ai/install.sh', script)
            # Install tools before granting the task user access to its workspace.
            env = dict(os.environ, HOME=str(tmp))
            run('bash', script, env=env)
            shutil.copy2(tmp / '.local/bin/claude', '/usr/local/bin/claude', follow_symlinks=True)
        endpoint_hosts(c)
        if old:
            run('systemctl', 'stop', 'aifactory-worker')
            try:
                existing_config(root / 'config.json', c)
            except Exception:
                run('systemctl', 'start', 'aifactory-worker')
                raise
        root.mkdir(exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        private_write(root / 'worker.token', c['TOKEN'].strip().encode())
        private_write(root / 'server.crt', ca)
        config = dict(worker=c['WORKER'], url=c['URL'], token_file=str(root / 'worker.token'),
                      ca_file=str(root / 'server.crt'), state_dir='/var/lib/aifactory-worker/private/state',
                      work_root='/var/lib/aifactory-worker/work', task_user='aifactory-task')
        private_write(tmp / 'config.json', json.dumps(config).encode())
        run('bash', SOURCE / 'templates/install-linux-worker.sh', tmp / 'config.json', binaries, '--headless')
        run('systemctl', 'is-active', 'aifactory-worker', 'aifactory-desktop')
        # A desktop HTTP process alone is insufficient: exercise actual X11 capture.
        for _ in range(30):
            probe = subprocess.run(['runuser', '-u', 'aifactory-task', '--', '/usr/local/lib/aifactory-computer/aifactory-computer'],
                input=b'{"action":"screenshot"}', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25)
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise ValueError('Desktop failed its X11 screenshot check; inspect journalctl -u aifactory-desktop')
    print('Installed Linux worker ' + c['WORKER'] + '. Desktop: Alt+F2 -> application name. Check control list for online status.')


def mac(c, ca):
    if os.geteuid() == 0 or platform.machine() != 'arm64':
        raise ValueError('Run as the logged-in Apple Silicon Mac user, without sudo')
    uid = str(os.getuid())
    run('launchctl', 'print', 'gui/' + uid, stdout=subprocess.DEVNULL)
    brew = Path('/opt/homebrew/bin/brew')
    if not brew.exists():
        raise ValueError('Install Homebrew first; its Apple Command Line Tools setup may require a dialog')
    os.environ['PATH'] = '/opt/homebrew/bin:/usr/local/bin:' + os.environ['PATH']
    root = Path.home() / '.config/aifactory-worker'
    old = existing_config(root / 'config.json', c)
    # Refuse silent parallel installation over the older manual deployment location.
    plist_path = Path.home() / 'Library/LaunchAgents/com.aifactory.worker.plist'
    if plist_path.exists() and not old:
        raise ValueError('Existing manual LaunchAgent detected; migrate its config to ~/.config/aifactory-worker/config.json first')
    for tool in ('tart', 'softnet'):
        if not (Path('/opt/homebrew/bin') / tool).exists():
            run(brew, 'install', 'cirruslabs/cli/' + tool)
    softnet = Path(output(brew, '--prefix', 'softnet')) / 'bin/softnet'
    if softnet.stat().st_uid != 0 or not softnet.stat().st_mode & 0o4000:
        run('sudo', 'chown', 'root:wheel', softnet)
        run('sudo', 'chmod', 'u+s', softnet)
    tart = '/opt/homebrew/bin/tart'
    base = c['MAC_BASE'] or (old or {}).get('base_vm') or 'aifactory-macos-base'
    guest = c['MAC_GUEST'] or (old or {}).get('guest_vm') or 'aifactory-macos-guest'
    if base == guest:
        raise ValueError('Mac base and guest names must differ')
    vms = json.loads(output(tart, 'list', '--format', 'json'))
    existing = next((v for v in vms if v.get('Name') == base or v.get('name') == base), None)
    if not existing:
        run(tart, 'clone', c['MAC_IMAGE'] or 'ghcr.io/cirruslabs/macos-sequoia-base:latest', base)
    elif (existing.get('State') or existing.get('state', '')).lower() != 'stopped':
        raise ValueError('Stop the dedicated base VM before installation')
    with tempfile.TemporaryDirectory(prefix='aifactory-build-') as directory:
        tmp = Path(directory)
        binaries = build(tmp)
        verify_connection(binaries / "aifactory-worker", c, ca, tmp)
        run('xcrun', 'swiftc', '-parse-as-library', '-O', SOURCE / 'computer/macos.swift', '-o', binaries / 'desktop-native')
        if old:
            if subprocess.run(['launchctl', 'print', 'gui/' + uid + '/com.aifactory.worker'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                run('launchctl', 'bootout', 'gui/' + uid + '/com.aifactory.worker')
            try:
                existing_config(root / 'config.json', c)
            except Exception:
                run('launchctl', 'bootstrap', 'gui/' + uid, plist_path)
                raise
        log = (tmp / 'tart.log').open('w')
        vm = subprocess.Popen([tart, 'run', '--no-clipboard', '--net-softnet',
             '--net-softnet-block=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16', base], stdout=log, stderr=log)
        prepared = False
        try:
            for _ in range(120):
                if subprocess.run([tart, 'exec', base, '/usr/bin/true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(2)
            else:
                raise ValueError('Tart guest agent not ready; use a base image with the guest agent')
            run(tart, 'exec', base, '/bin/bash', '-lc', 'sudo networksetup -setdnsservers Ethernet 1.1.1.1 8.8.8.8 && sudo networksetup -setv6off Ethernet')
            # Tool installation contains no worker token; token stays on the host.
            run(tart, 'exec', base, '/bin/bash', '-lc',
                'export PATH=/opt/homebrew/bin:$HOME/.local/bin:$PATH; brew install python gh coreutils; command -v claude >/dev/null || (curl -fsSL https://claude.ai/install.sh | bash)')
            for name in ('aifactory-computer', 'desktop-native'):
                script = "import pathlib,sys; p=pathlib.Path.home()/'.local/lib/aifactory-computer'/sys.argv[1]; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o755)"
                run(tart, 'exec', '-i', base, '/opt/homebrew/bin/python3', '-c', script, name, input=(binaries / name).read_bytes())
            helper = '"$HOME/.local/lib/aifactory-computer/aifactory-computer"'
            for request in ('{"action":"screenshot"}', '{"action":"move","x":1,"y":1}'):
                result = subprocess.run(
                                 [tart, 'exec', '-i', base, '/bin/bash', '-lc', helper], input=request.encode(), stdout=subprocess.PIPE)
                try:
                    ok = result.returncode == 0 and json.loads(result.stdout).get('ok')
                except ValueError:
                    ok = False
                if not ok:
                    raise ValueError('Mac guest needs Screen Recording / Accessibility permission. Open the dedicated base VM, grant permissions to the responsible app (Tart Guest Agent / desktop-native), stop it and rerun. Worker not installed yet.')
            prepared = True
        finally:
            run(tart, 'stop', base)
            vm.wait(timeout=30)
            log.close()
            if old and not prepared:
                run('launchctl', 'bootstrap', 'gui/' + uid, plist_path)
        endpoint_hosts(c)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(root / 'worker.token', c['TOKEN'].strip().encode())
        private_write(root / 'server.crt', ca)
        config = dict(worker=c['WORKER'], url=c['URL'], token_file=str(root / 'worker.token'),
                      ca_file=str(root / 'server.crt'), state_dir=str(Path.home() / '.local/state/aifactory-worker'),
                      tart=tart, base_vm=base, guest_vm=guest)
        if old:
            config['state_dir'] = old['state_dir']
        private_write(root / 'config.json', json.dumps(config).encode())
        dest = Path.home() / '.local/bin/aifactory-worker'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binaries / 'aifactory-worker', dest)
        logs = Path.home() / '.local/state/aifactory-worker/logs'
        logs.mkdir(parents=True, exist_ok=True)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(plistlib.dumps(dict(Label='com.aifactory.worker', ProgramArguments=[str(dest), '--config', str(root / 'config.json')],
            EnvironmentVariables={'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}, RunAtLoad=True, KeepAlive=True,
            ThrottleInterval=10, StandardOutPath=str(logs / 'worker.log'), StandardErrorPath=str(logs / 'worker-error.log'))))
        run('launchctl', 'bootstrap', 'gui/' + uid, plist_path)
    print('Installed Mac worker ' + c['WORKER'] + '. LaunchAgent requires host login. Base VM is stopped and ready.')


def main():
    os.umask(0o077)
    c = settings(os.environ)
    os.environ.pop('AIFACTORY_TOKEN', None)  # Never pass the worker token to build tools or installers.
    ca = certificate(c)
    if os.environ.get('AIFACTORY_CHECK_ONLY') == '1':
        print('Configuration valid; no system changes made')
        return
    if platform.system() == 'Linux':
        linux(c, ca)
    elif platform.system() == 'Darwin':
        mac(c, ca)
    else:
        raise ValueError('Unsupported platform')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Deliberately avoid traceback / environment dumps containing credentials.
        print('Installation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
