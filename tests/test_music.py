# -*- coding: utf-8 -*-
import json
import os
import unittest
import urlparse
import xml.etree.ElementTree as ET

from test_playback import (ADDON_PATH, Client, contents, entries, playback,
                           resolved, service, window)


def params(url):
    return dict((k, v[0]) for k, v in urlparse.parse_qs(urlparse.urlsplit(url).query).items())


class MusicClient(Client):
    def __init__(self):
        self.item = {'Id': 'track', 'Type': 'Audio', 'Name': u'Song \u266b',
                     'Artists': [u'Artist \u00e9'], 'Album': 'Album', 'AlbumId': 'album',
                     'AlbumPrimaryImageTag': 'cover', 'IndexNumber': 3,
                     'ParentIndexNumber': 2, 'RunTimeTicks': 180 * playback.TICKS,
                     'UserData': {'PlaybackPositionTicks': 30 * playback.TICKS, 'PlayCount': 4}}
        self.source = {'Id': 'source', 'SupportsDirectStream': True, 'Container': 'flac'}
        self.result = {'Items': [], 'TotalRecordCount': 0}
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.result if path == '/UserViews' else {'Items': [self.item]}

    def request(self, method, path, query):
        self.calls.append((path, query))
        return self.result

    def post(self, path, body, **kwargs):
        self.body = body
        return {'PlaySessionId': 'session', 'MediaSources': [self.source] if self.source else []}

    def image(self, item, kind, tag=None, width=None):
        return self.server + '/Items/' + item + '/Images/' + kind

    def url(self, path, query):
        return self.server + path + '?' + playback.qs(query)


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.client = MusicClient()
        playback.ARGS = {}
        entries[:] = []
        contents[:] = []
        resolved[:] = []
        window.props.clear()

    def test_metadata_declares_audio_and_server_version(self):
        manifest = ET.parse(os.path.join(ADDON_PATH, 'addon.xml')).getroot()
        self.assertEqual(set(manifest.find("extension[@point='xbmc.python.pluginsource']/provides").text.split()),
                         set(['video', 'audio']))
        self.assertIn('10.11.x', manifest.find("extension[@point='xbmc.addon.metadata']/description").text)

    def test_root_includes_music_and_video_but_not_unsupported_libraries(self):
        self.client.result = {'Items': [
            {'Id': kind, 'Name': kind, 'CollectionType': kind}
            for kind in ('music', 'movies', 'books', 'photos')]}
        playback.mode_root(self.client)
        labels = [item.label for url, item, folder in entries]
        self.assertIn('music', labels)
        self.assertIn('movies', labels)
        self.assertNotIn('books', labels)
        self.assertNotIn('photos', labels)
        music_url = next(url for url, item, folder in entries if item.label == 'music')
        self.assertEqual(params(music_url), {'mode': 'music', 'library': 'music'})

    def test_music_library_offers_artist_album_song_and_search_views(self):
        playback.ARGS = {'mode': 'music', 'library': 'library'}
        playback.mode_music(self.client)
        self.assertEqual([item.label for url, item, folder in entries],
                         ['Artists', 'Albums', 'Songs', 'Search music...'])
        self.assertTrue(all(params(url)['library'] == 'library' for url, item, folder in entries))

    def test_artist_album_and_track_routes_keep_library_scope(self):
        playback.ARGS = {'mode': 'music', 'view': 'artists', 'library': 'library'}
        self.client.result = {'Items': [{'Id': 'artist', 'Type': 'MusicArtist', 'Name': 'Artist'}]}
        playback.mode_music(self.client)
        self.assertEqual(self.client.calls[-1][0], '/Artists')
        self.assertEqual(self.client.calls[-1][1]['parentId'], 'library')
        self.assertEqual(contents[-1], 'artists')
        playback.ARGS = params(entries[0][0])
        entries[:] = []
        self.client.result = {'Items': [{'Id': 'album', 'Type': 'MusicAlbum', 'Name': 'Album'}]}
        playback.mode_music(self.client)
        query = self.client.calls[-1][1]
        self.assertEqual(query['artistIds'], 'artist')
        self.assertEqual(query['includeItemTypes'], 'MusicAlbum')
        self.assertEqual(query['parentId'], 'library')
        self.assertEqual(contents[-1], 'albums')
        self.assertEqual(params(entries[0][0])['view'], 'songs')
        playback.ARGS = params(entries[1][0])
        entries[:] = []
        self.client.result = {'Items': [self.client.item]}
        playback.mode_music(self.client)
        query = self.client.calls[-1][1]
        self.assertEqual(query['parentId'], 'album')
        self.assertEqual(query['includeItemTypes'], 'Audio')
        self.assertEqual(query['sortBy'], 'ParentIndexNumber,IndexNumber,SortName')
        self.assertNotIn('artistIds', query)  # Include every track on compilation albums.
        self.assertEqual(contents[-1], 'songs')

    def test_tracks_use_music_tags_album_art_and_playable_links(self):
        url, item, folder = playback.make_item(self.client, self.client.item)
        self.assertFalse(folder)
        self.assertEqual(params(url), {'mode': 'play', 'id': 'track'})
        self.assertEqual(item.props['IsPlayable'], 'true')
        self.assertNotIn('video', item.info)
        self.assertEqual(item.info['music']['artist'], u'Artist \u00e9')
        self.assertEqual(item.info['music']['album'], 'Album')
        self.assertEqual(item.info['music']['tracknumber'], 3)
        self.assertEqual(item.info['music']['discnumber'], 2)
        self.assertEqual(item.info['music']['duration'], 180)
        self.assertEqual(item.info['music']['playcount'], 4)
        self.assertIn('/Items/album/Images/Primary', item.thumb)
        self.assertEqual(item.context, [])  # No video watched-state menu on songs.

    def test_search_and_pagination_preserve_all_music_filters(self):
        playback.ARGS = {'mode': 'musicsearch', 'library': 'library', 'q': 'song'}
        playback.mode_music_search(self.client)
        self.assertEqual(len(entries), 3)
        for url, item, folder in entries:
            self.assertEqual(params(url)['q'], 'song')
        playback.ARGS = {'mode': 'music', 'view': 'songs', 'artist': 'artist',
                         'library': 'library', 'q': 'song', 'start': '50'}
        self.client.result = {'Items': [self.client.item], 'TotalRecordCount': 120}
        entries[:] = []
        playback.mode_music(self.client)
        query = self.client.calls[-1][1]
        self.assertEqual(query['searchTerm'], 'song')
        self.assertEqual(query['startIndex'], 50)
        next_page = params(entries[-1][0])
        self.assertEqual(next_page['start'], '100')
        for key in ('view', 'artist', 'library', 'q'):
            self.assertEqual(next_page[key], playback.ARGS[key])

    def test_direct_audio_uses_audio_endpoint_and_starts_at_beginning(self):
        playback.ARGS = {'id': 'track'}
        playback.mode_play(self.client)
        self.assertIn('/Audio/track/stream.flac?', resolved[-1][2].path)
        self.assertEqual(resolved[-1][2].info['music']['title'], u'Song \u266b')
        self.assertEqual(self.client.body['StartTimeTicks'], 0)
        self.assertEqual(self.client.body['MaxStreamingBitrate'], 320000)
        self.assertEqual(self.client.body['DeviceProfile']['TranscodingProfiles'][0]['Type'], 'Audio')
        self.assertEqual(json.loads(window.props['jfl.pending'])['PlayMethod'], 'DirectStream')

    def test_audio_fallback_is_progressive_mp3_without_video_subtitles(self):
        self.client.source = {'Id': 'source', 'TranscodingUrl': '/Audio/track/stream.mp3',
                              'MediaStreams': [{'Type': 'Subtitle', 'DeliveryMethod': 'External',
                                                'DeliveryUrl': '/irrelevant', 'Index': 1}],
                              'DefaultSubtitleStreamIndex': 1}
        playback.ARGS = {'id': 'track'}
        playback.mode_play(self.client)
        item = resolved[-1][2]
        self.assertEqual(item.mime, 'audio/mpeg')
        self.assertIn('/Audio/track/stream.mp3?ApiKey=token', item.path)
        profile = self.client.body['DeviceProfile']['TranscodingProfiles'][0]
        self.assertEqual(profile['Protocol'], 'http')
        self.assertEqual(profile['Container'], 'mp3')
        self.assertNotIn('SubtitleStreamIndex', self.client.body)

    def test_missing_audio_source_fails_cleanly(self):
        self.client.source = None
        playback.ARGS = {'id': 'track'}
        playback.mode_play(self.client)
        self.assertFalse(resolved[-1][1])
        self.assertNotIn('jfl.pending', window.props)

    def test_service_reports_audio_progress_pause_and_stop(self):
        playback.ARGS = {'id': 'track'}
        playback.mode_play(self.client)
        tracker = service.Tracker()
        reports = []
        tracker.send = lambda path, body: reports.append((path, body))
        tracker.isPlayingVideo = lambda: False
        tracker.isPlayingAudio = lambda: True
        tracker.getTime = lambda: 12.0
        tracker.onPlayBackStarted()
        tracker.tick()
        self.assertEqual(reports[-1][0], '/Sessions/Playing')
        self.assertEqual(reports[-1][1]['PositionTicks'], 12 * playback.TICKS)
        tracker.onPlayBackPaused()
        tracker.tick()
        self.assertEqual(reports[-1][0], '/Sessions/Playing/Progress')
        self.assertTrue(reports[-1][1]['IsPaused'])
        tracker.onPlayBackEnded()
        self.assertEqual(reports[-1][0], '/Sessions/Playing/Stopped')
        self.assertEqual(reports[-1][1]['ItemId'], 'track')
        self.assertIsNone(tracker.active)


if __name__ == '__main__':
    unittest.main()
