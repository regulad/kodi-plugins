import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from tools.build import stable_zip
from tools.deploy import key_bytes, remote_script


class PublishTests(unittest.TestCase):
    def test_key_accepts_raw_and_encoded_openssh(self):
        # Parsing is delegated to OpenSSH; this helper only normalizes the envelope.
        key = '-----BEGIN OPENSSH PRIVATE KEY-----\nYWJj\n-----END OPENSSH PRIVATE KEY-----\n'
        for value in (key, key.replace('\n', '\\n'), base64.b64encode(key.encode()).decode()):
            self.assertEqual(key_bytes(value), key.encode())
        with self.assertRaises(ValueError):
            key_bytes(base64.b64encode(b'not a private key').decode())

    def test_zip_is_stable_across_timestamp_and_entry_order(self):
        with tempfile.TemporaryDirectory() as folder:
            outputs = []
            for index in range(2):
                path = Path(folder) / ('addon-%d.zip' % index)
                entries = [('addon/addon.xml', b'<addon/>'), ('addon/default.py', b'pass\n')]
                with zipfile.ZipFile(path, 'w') as archive:
                    for name, data in entries[::1 if index == 0 else -1]:
                        info = zipfile.ZipInfo(name, (2000 + index, 1, 1, 0, 0, 0))
                        archive.writestr(info, data)
                stable_zip(path)
                outputs.append(path.read_bytes())
                self.assertEqual(path.with_suffix('.zip.md5').read_text().split()[0],
                                 hashlib.md5(outputs[-1]).hexdigest())
            self.assertEqual(*outputs)

    def test_remote_path_is_quoted_and_root_rejected(self):
        script = remote_script("/srv/kodi files/owner's site", 'helix-test')
        self.assertIn("base='/srv/kodi files/owner'\"'\"'s site'", script)
        for path in ('/', '///', '/srv/../', 'relative', '/srv/\nbad'):
            with self.assertRaises(ValueError):
                remote_script(path, 'helix-test')

    @unittest.skipIf(os.name == 'nt', 'Publication script runs on Linux')
    def test_publish_retains_old_packages_and_rejects_replaced_version(self):
        with tempfile.TemporaryDirectory() as folder:
            base = str(Path(folder) / "web root's files")

            def publish(release, version, contents):
                package = 'plugin.test/plugin.test-%s.zip' % version
                checksum = hashlib.md5(contents).hexdigest()
                catalog = b'<addons/>\n'
                files = {package: contents,
                         package + '.md5': (checksum + ' *' + package.split('/')[1] + '\n').encode(),
                         'addons.xml': catalog,
                         'addons.xml.md5': (hashlib.md5(catalog).hexdigest() + '  addons.xml\n').encode()}
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w') as archive:
                    for name, content in files.items():
                        entry = tarfile.TarInfo(name)
                        entry.size = len(content)
                        archive.addfile(entry, io.BytesIO(content))
                return subprocess.run(['sh', '-c', remote_script(base, release)],
                                      input=data.getvalue(), capture_output=True)

            self.assertEqual(publish('first', '1.0.0', b'first').returncode, 0)
            self.assertEqual(publish('second', '1.0.1', b'second').returncode, 0)
            live = Path(base) / 'helix'
            self.assertEqual((live / 'plugin.test/plugin.test-1.0.0.zip').read_bytes(), b'first')
            self.assertNotEqual(publish('bad', '1.0.1', b'changed').returncode, 0)
            self.assertEqual(os.readlink(live), '.releases/second')
            self.assertEqual((live / 'plugin.test/plugin.test-1.0.1.zip').read_bytes(), b'second')


if __name__ == '__main__':
    unittest.main()
