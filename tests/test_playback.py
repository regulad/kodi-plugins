"""Run with Python 2.7; exercise playback with Kodi and Jellyfin test doubles."""
import imp
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = os.path.join(ROOT, 'plugin.video.jellyfin.lite')


class Addon(object):
    def getAddonInfo(self, key):
        return ADDON_PATH

    def getSetting(self, key):
        return {'protocol': '0', 'askresume': 'true', 'directstream': 'true'}.get(key, '')


class Window(object):
    def __init__(self):
        self.props = {}

    def setProperty(self, key, value):
        self.props[key] = value

    def getProperty(self, key):
        return self.props.get(key, '')

    def clearProperty(self, key):
        self.props.pop(key, None)


class ListItem(object):
    def __init__(self, label='', **kwargs):
        self.label = label
        self.path = kwargs.get('path')
        self.mime = None
        self.props = {}
        self.info = {}
        self.context = []
        self.thumb = None

    def setMimeType(self, mime):
        self.mime = mime

    def setInfo(self, kind, info):
        self.info[kind] = info

    def setProperty(self, key, value):
        self.props[key] = value

    def setIconImage(self, url):
        self.icon = url

    def setThumbnailImage(self, url):
        self.thumb = url

    def addContextMenuItems(self, items):
        self.context.extend(items)


class Dialog(object):
    def select(self, *args):
        return 0

    def ok(self, *args):
        pass


window = Window()
resolved = []
entries = []
contents = []
xbmc = types.ModuleType('xbmc')
xbmc.translatePath = lambda path: path
xbmc.log = lambda *args: None
xbmc.LOGNOTICE = 1
xbmc.LOGWARNING = 2
xbmc.abortRequested = False
xbmc.sleep = lambda ms: None
xbmc.Player = type('Player', (object,), {})
addon = types.ModuleType('xbmcaddon')
addon.Addon = lambda *args: Addon()
gui = types.ModuleType('xbmcgui')
gui.Window = lambda *args: window
gui.ListItem = ListItem
gui.Dialog = Dialog
plugin = types.ModuleType('xbmcplugin')
plugin.setResolvedUrl = lambda *args: resolved.append(args)
plugin.addDirectoryItems = lambda handle, items, total: entries.extend(items)
plugin.addDirectoryItem = lambda handle, url, item, folder: entries.append((url, item, folder))
plugin.setContent = lambda handle, content: contents.append(content)
plugin.endOfDirectory = lambda *args, **kwargs: None
for name, module in [('xbmc', xbmc), ('xbmcaddon', addon), ('xbmcgui', gui), ('xbmcplugin', plugin)]:
    sys.modules[name] = module
saved_argv = sys.argv
sys.argv = ['plugin://plugin.video.jellyfin.lite', '1', '']
playback = imp.load_source('jfl_playback', os.path.join(ADDON_PATH, 'default.py'))
service = imp.load_source('jfl_service', os.path.join(ADDON_PATH, 'service.py'))
sys.argv = saved_argv


class Client(object):
    user_id, device_id, token = 'user', 'device', 'token'
    server = 'http://example.invalid'

    def __init__(self, resume):
        self.resume = resume

    def get(self, *args, **kwargs):
        return {'Items': [{'Id': 'item', 'Name': 'Test',
                           'UserData': {'PlaybackPositionTicks': self.resume * playback.TICKS}}]}

    def post(self, path, body, **kwargs):
        self.body = body
        return {'PlaySessionId': 'session', 'MediaSources': [
            {'Id': 'source', 'TranscodingUrl': '/Videos/item/stream.ts?test=1'}]}


class PlaybackTests(unittest.TestCase):
    def test_old_transport_setting_cannot_select_segmented_delivery(self):
        profile = playback.device_profile()['TranscodingProfiles']
        self.assertEqual(len(profile), 1)
        self.assertEqual(profile[0]['Protocol'], 'http')
        self.assertEqual(profile[0]['Container'], 'ts')

    def test_resume_uses_server_offset_without_player_seek(self):
        client = Client(90)
        playback.ARGS = {'id': 'item'}
        playback.mode_play(client)
        self.assertEqual(client.body['StartTimeTicks'], 90 * playback.TICKS)
        self.assertFalse(client.body['EnableDirectStream'])
        self.assertEqual(resolved[-1][2].mime, 'video/mp2t')
        pending = json.loads(window.props['jfl.pending'])
        self.assertEqual(pending['Offset'], 90)
        self.assertNotIn('Seek', pending)
        tracker = service.Tracker()
        tracker.active = pending
        tracker.need_start = True
        tracker.isPlayingVideo = lambda: True
        tracker.getTime = lambda: 12.0
        reports = []
        tracker.send = lambda path, body: reports.append(body)
        tracker.tick()
        self.assertEqual(reports[0]['PositionTicks'], 102 * playback.TICKS)

    def test_play_from_beginning_keeps_direct_stream_eligibility(self):
        client = Client(90)
        playback.ARGS = {'id': 'item', 'resume': '0'}
        playback.mode_play(client)
        self.assertEqual(client.body['StartTimeTicks'], 0)
        self.assertTrue(client.body['EnableDirectStream'])


if __name__ == '__main__':
    unittest.main()
