"""Client URL handling without Kodi or network access."""
import unittest

import test_playback  # Install the shared Kodi test doubles before importing.
from jfclient import Client


class Settings(object):
    def __init__(self, server):
        self.server = server

    def getSetting(self, key):
        return {'server': self.server, 'deviceid': 'device'}.get(key, '')


class ClientUrlTests(unittest.TestCase):
    def test_bare_addresses_default_to_http(self):
        for value, expected in (
                ('jellyfin.example:8096', 'http://jellyfin.example:8096'),
                (' 192.0.2.1:8096/ ', 'http://192.0.2.1:8096'),
                ('jellyfin.example/jellyfin/', 'http://jellyfin.example:8096/jellyfin'),
                ('[2001:db8::1]:8096', 'http://[2001:db8::1]:8096'),
                ('//jellyfin.example:8096', 'http://jellyfin.example:8096')):
            client = Client(Settings(value))
            self.assertEqual(client.server, expected)
            self.assertTrue(client.url('/Videos/channel/stream.ts').startswith(expected + '/Videos/'))

    def test_explicit_protocol_is_preserved(self):
        for value in ('http://jellyfin.example:80', 'https://jellyfin.example:443/jellyfin'):
            self.assertEqual(Client(Settings(value + '/')).server, value)

    def test_omitted_port_defaults_to_8096(self):
        for value, expected in (
                ('jellyfin.example', 'http://jellyfin.example:8096'),
                ('http://jellyfin.example', 'http://jellyfin.example:8096'),
                ('https://jellyfin.example/jellyfin', 'https://jellyfin.example:8096/jellyfin'),
                ('[2001:db8::1]', 'http://[2001:db8::1]:8096')):
            self.assertEqual(Client(Settings(value)).server, expected)

    def test_explicit_ports_are_preserved(self):
        for port in (80, 443, 8096, 8920):
            value = 'jellyfin.example:%d/jellyfin' % port
            self.assertEqual(Client(Settings(value)).server, 'http://' + value)

    def test_empty_server_remains_unconfigured(self):
        self.assertEqual(Client(Settings('  ')).server, '')
