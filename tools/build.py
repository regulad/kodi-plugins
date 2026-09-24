"""Build a Helix catalog with the pinned upstream generator and stable ZIP bytes."""
import hashlib
import html
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def stable_zip(path):
    with zipfile.ZipFile(path) as archive:
        entries = [(i.filename, archive.read(i)) for i in archive.infolist() if not i.is_dir()]
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.md5').write_text(digest + ' *' + path.name + '\n', encoding='ascii')


def build(sources, generator, output):
    sources, generator, output = map(lambda p: Path(p).resolve(), (sources, generator, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Build output must be empty')
    manifests = sorted(sources.glob('*/addon.xml'))
    addons = {}
    for manifest in manifests:
        root = ET.parse(manifest).getroot()
        aid = root.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', aid) or aid != manifest.parent.name or aid in addons:
            raise ValueError('Invalid or duplicate add-on ID: ' + aid)
        addons[aid] = root
    if 'repository.regulad.helix' not in addons:
        raise ValueError('Repository add-on must be included')
    builtin = {'xbmc.python': (2, 19, 0), 'xbmc.addon': (14, 0, 0)}
    for aid, root in addons.items():
        for dep in root.findall('./requires/import'):
            name = dep.get('addon')
            if dep.get('optional') == 'true':
                continue
            version = tuple(int(v) for v in dep.get('version', '0.0.0').split('.'))
            available = builtin.get(name)
            if name in addons:
                available = tuple(int(v) for v in addons[name].get('version').split('.'))
            if available is None or available < version:
                raise ValueError('Unsatisfied dependency for %s: %s' % (aid, name))
    # Archive tracked sources only; never include checkout credentials or caches.
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / 'sources.zip'
        subprocess.run(['git', '-C', str(sources), 'archive', '--format=zip',
                        '-o', str(snapshot), 'HEAD', *addons], check=True)
        with zipfile.ZipFile(snapshot) as archive:
            for name in archive.namelist():
                if '..' in Path(name).parts or name.startswith('/') or '\\' in name:
                    raise ValueError('Unsafe archive entry')
            archive.extractall(Path(temp) / 'sources')
        subprocess.run([sys.executable, str(generator), '--no-parallel', '--datadir', str(output),
                        *[str(Path(temp) / 'sources' / aid) for aid in addons]], check=True)
    for package in sorted(output.glob('*/*.zip')):
        stable_zip(package)
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None:
                raise ValueError('Corrupt ZIP')
            if any(n.split('/')[0] != package.parent.name for n in archive.namelist()):
                raise ValueError('Wrong ZIP root')
    catalog = ET.parse(output / 'addons.xml').getroot()
    if len(catalog) != len(addons):
        raise ValueError('Incomplete catalog')
    links = []
    for package in sorted(output.glob('*/*.zip')):
        rel = package.relative_to(output).as_posix()
        links.append('<li><a href="%s">%s</a></li>' % (html.escape(rel), html.escape(package.name)))
    (output / 'index.html').write_text('<!doctype html><title>Regulad Helix</title>'
                                     '<h1>Regulad Helix</h1><ul>' + ''.join(links) + '</ul>\n', encoding='utf-8')
    print('Built %d add-ons' % len(addons))


if __name__ == '__main__':
    build(*sys.argv[1:])
