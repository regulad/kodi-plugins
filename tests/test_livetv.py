# -*- coding: utf-8 -*-
import json
import os
import unittest
import xml.etree.ElementTree as ET

from test_playback import ADDON_PATH, entries, playback, resolved, service, window, xbmc
from test_music import MusicClient, params
from jflive import open_live, close_live


class LiveClient(MusicClient):
    def __init__(self):
        MusicClient.__init__(self)
        # Jellyfin 10.11 LiveTvChannel.GetClientTypeName() returns TvChannel.
        self.channel = {'Id': 'channel', 'Type': 'TvChannel', 'Name': 'Channel',
                        'ChannelNumber': '7.2', 'ImageTags': {'Primary': 'logo'},
                        'CurrentProgram': {'Name': 'News', 'Overview': 'Current headlines'}}
        self.posts = []
        self.opens = 0

    def get(self, path, **query):
        self.calls.append((path, query))
        if path == '/LiveTv/Channels':
            return {'Items': [self.channel], 'TotalRecordCount': 60}
        if path.startswith('/LiveTv/Channels/'):
            return self.channel
        return self.result

    def post(self, path, body=None, params=None, **kwargs):
        self.posts.append((path, body, params))
        if path.endswith('/PlaybackInfo'):
            self.opens += 1
            return {'PlaySessionId': 'session-%d' % self.opens, 'MediaSources': [{
                'Id': 'source-%d' % self.opens, 'LiveStreamId': 'live-%d' % self.opens,
                'TranscodingUrl': '/Videos/channel/stream.ts?fresh=%d' % self.opens}]}


class LiveTvTests(unittest.TestCase):
    def setUp(self):
        self.client = LiveClient()
        self.now = 1000.0
        self.old_time = service.time.time
        self.old_client = service.Client
        self.old_open = service.open_live
        service.time.time = lambda: self.now
        service.Client = lambda addon: self.client
        playback.ARGS = {'content_type': 'video', 'id': 'channel'}
        window.props.clear()
        entries[:] = []
        resolved[:] = []
        xbmc.abortRequested = False
        self.tracker = service.Tracker()
        self.playing = False
        self.played = []
        self.current_url = ''
        self.tracker.isPlaying = lambda: self.playing
        self.tracker.isPlayingVideo = lambda: self.playing
        self.tracker.isPlayingAudio = lambda: False
        self.tracker.getTime = lambda: 10.0
        self.tracker.getPlayingFile = lambda: self.current_url
        def play(url, item):
            self.played.append((url, item))
            self.current_url = url
        self.tracker.play = play

    def tearDown(self):
        service.time.time = self.old_time
        service.Client = self.old_client
        service.open_live = self.old_open
        xbmc.abortRequested = False
        window.props.clear()

    def start_live(self):
        playback.mode_live_play(self.client)
        self.current_url = resolved[-1][2].path
        self.playing = True
        self.tracker.onPlayBackStarted()
        self.tracker.tick()

    def drop_live(self):
        self.playing = False
        self.tracker.onPlayBackEnded()

    def test_channels_are_playable_and_include_number_logo_and_program(self):
        playback.mode_live_tv(self.client)
        query = self.client.calls[-1][1]
        self.assertEqual(query['type'], 'TV')
        self.assertEqual(query['limit'], 50)
        self.assertEqual(query['userId'], 'user')
        url, item, folder = entries[0]
        self.assertFalse(folder)
        self.assertEqual(item.label, '7.2. Channel - News')
        self.assertEqual(item.info['video']['plot'], 'Current headlines')
        self.assertIn('/Items/channel/Images/Primary', item.thumb)
        self.assertEqual(item.props['IsPlayable'], 'true')
        self.assertEqual(params(url)['mode'], 'liveplay')
        self.assertEqual(item.context, [])
        self.assertEqual(params(entries[-1][0])['start'], '50')
        self.assertEqual(params(entries[-1][0])['content_type'], 'video')

    def test_clicking_listed_channel_resolves_stream_instead_of_browsing(self):
        playback.mode_live_tv(self.client)
        url, item, folder = entries[0]
        self.assertFalse(folder)
        playback.ARGS = params(url)
        entries[:] = []
        self.client.calls[:] = []
        playback.MODES[playback.ARGS['mode']](self.client)
        self.assertEqual(entries, [])
        self.assertEqual(self.client.calls[0][0], '/LiveTv/Channels/channel')
        self.assertTrue(resolved[-1][1])
        self.assertIn('/Videos/channel/stream.ts?', resolved[-1][2].path)

    def test_audio_view_never_lists_live_tv(self):
        playback.ARGS = {'content_type': 'audio'}
        playback.mode_root(self.client)
        self.assertNotIn('Live TV', [item.label for url, item, folder in entries])
        entries[:] = []
        self.client.calls[:] = []
        playback.mode_live_tv(self.client)
        self.assertEqual(entries, [])
        self.assertEqual(self.client.calls, [])

    def test_avc_is_disabled_and_video_profile_stays_h264(self):
        settings = ET.parse(os.path.join(ADDON_PATH, 'resources', 'settings.xml')).getroot()
        setting = settings.find("category[@label='Playback']/setting[@label='Codec']")
        self.assertEqual(setting.get('default'), 'AVC')
        self.assertEqual(setting.get('enable'), 'false')
        self.assertEqual(playback.device_profile()['TranscodingProfiles'][0]['VideoCodec'], 'h264')

    def test_tuning_opens_fresh_progressive_stream_without_resume(self):
        playback.mode_live_play(self.client)
        body = self.client.posts[0][1]
        self.assertTrue(body['AutoOpenLiveStream'])
        self.assertFalse(body['EnableDirectPlay'])
        self.assertFalse(body['EnableDirectStream'])
        self.assertEqual(body['StartTimeTicks'], 0)
        profile = body['DeviceProfile']['TranscodingProfiles'][0]
        self.assertEqual((profile['Protocol'], profile['Container']), ('http', 'ts'))
        self.assertFalse(profile['EstimateContentLength'])
        self.assertEqual(resolved[-1][2].mime, 'video/mp2t')
        self.assertIn('LiveStreamId=live-1', resolved[-1][2].path)
        state = json.loads(window.props['jfl.pending'])
        self.assertTrue(state['Live'])
        self.assertEqual(state['LiveStreamId'], 'live-1')

    def test_reconnect_renegotiates_after_delay_and_releases_old_session(self):
        self.start_live()
        self.assertFalse(self.tracker.body()['CanSeek'])
        self.drop_live()
        stop = next(body for path, body, query in self.client.posts if path.endswith('/Stopped'))
        self.assertEqual(stop['LiveStreamId'], 'live-1')
        self.assertTrue(stop['Failed'])
        self.tracker.tick()
        self.assertEqual(self.played, [])
        self.now += 2
        self.tracker.tick()
        self.assertEqual(self.client.opens, 2)
        self.assertIn('fresh=2', self.played[0][0])
        self.assertIn('LiveStreamId=live-2', self.played[0][0])
        self.assertEqual(json.loads(window.props['jfl.pending'])['RetryCount'], 1)

    def test_stop_cancels_scheduled_reconnect(self):
        self.start_live()
        self.drop_live()
        self.tracker.onPlayBackStopped()
        self.now += 20
        self.tracker.tick()
        self.assertEqual(self.played, [])
        self.assertIsNone(self.tracker.reconnect)

    def test_switch_to_another_selection_cancels_reconnect(self):
        self.start_live()
        self.drop_live()
        playback.begin_playback()
        self.now += 20
        self.tracker.tick()
        self.assertEqual(self.played, [])
        self.assertIsNone(self.tracker.reconnect)

    def test_unrelated_playback_cancels_reconnect(self):
        self.start_live()
        self.drop_live()
        self.playing = True
        self.now += 20
        self.tracker.tick()
        self.assertEqual(self.played, [])

    def test_other_media_start_does_not_claim_pending_live_stream(self):
        self.start_live()
        self.drop_live()
        self.now += 2
        self.tracker.tick()
        self.current_url = 'http://example.invalid/other-movie.mp4'
        self.playing = True
        self.tracker.onPlayBackStarted()
        self.assertIsNone(self.tracker.active)
        self.assertIsNone(self.tracker.reconnect)
        self.assertIn(('/LiveStreams/Close', None, {'liveStreamId': 'live-2'}), self.client.posts)

    def test_stopped_initial_open_releases_tuner_without_retry(self):
        playback.mode_live_play(self.client)
        self.tracker.onPlayBackStopped()
        self.assertIsNone(self.tracker.reconnect)
        self.assertNotIn('jfl.pending', window.props)
        self.assertIn(('/LiveStreams/Close', None, {'liveStreamId': 'live-1'}), self.client.posts)

    def test_stop_during_negotiation_closes_new_stream_without_playing(self):
        self.start_live()
        self.drop_live()
        def interrupted_open(*args):
            result = self.old_open(*args)
            self.tracker.onPlayBackStopped()
            return result
        service.open_live = interrupted_open
        self.now += 2
        self.tracker.tick()
        self.assertEqual(self.played, [])
        self.assertIsNone(self.tracker.reconnect)
        self.assertIn(('/LiveStreams/Close', None, {'liveStreamId': 'live-2'}), self.client.posts)

    def test_repeated_drops_are_limited_to_three_reconnects(self):
        self.start_live()
        for attempt in range(1, 4):
            self.drop_live()
            self.assertEqual(self.tracker.reconnect['State']['RetryCount'], attempt)
            self.now += 2 ** attempt
            self.tracker.tick()
            self.playing = True
            self.tracker.onPlayBackStarted()
            self.tracker.tick()
        self.drop_live()
        self.assertIsNone(self.tracker.reconnect)
        self.assertEqual(len(self.played), 3)

    def test_failed_negotiations_use_bounded_backoff(self):
        self.start_live()
        self.drop_live()
        def failed_open(*args):
            raise IOError('server unavailable')
        service.open_live = failed_open
        for delay in (2, 4, 8):
            self.assertEqual(self.tracker.reconnect['At'], self.now + delay)
            self.now += delay
            self.tracker.tick()
        self.assertIsNone(self.tracker.reconnect)
        self.assertEqual(self.played, [])

    def test_stable_playback_resets_retry_budget(self):
        self.start_live()
        self.tracker.active['RetryCount'] = 3
        self.tracker.getTime = lambda: 61.0
        self.tracker.tick()
        self.drop_live()
        self.assertEqual(self.tracker.reconnect['State']['RetryCount'], 1)

    def test_never_started_reconnect_is_closed_and_retried(self):
        self.start_live()
        self.drop_live()
        self.now += 2
        self.tracker.tick()
        self.now += 46
        self.tracker.tick()
        self.assertIn(('/LiveStreams/Close', None, {'liveStreamId': 'live-2'}), self.client.posts)
        self.assertEqual(self.tracker.reconnect['State']['RetryCount'], 2)

    def test_non_live_eof_does_not_reconnect(self):
        self.start_live()
        self.tracker.active['Live'] = False
        self.drop_live()
        self.assertIsNone(self.tracker.reconnect)

    def test_shutdown_cancels_retry(self):
        self.start_live()
        self.drop_live()
        self.tracker.shutdown()
        self.assertIsNone(self.tracker.reconnect)

    def test_unsupported_stream_closes_tuner(self):
        original = self.client.post
        def unsupported(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if result:
                result['MediaSources'][0]['TranscodingUrl'] = '/Videos/channel/master.m3u8'
            return result
        self.client.post = unsupported
        with self.assertRaises(RuntimeError):
            open_live(self.client, 'channel', playback.device_profile())
        self.assertIn(('/LiveStreams/Close', None, {'liveStreamId': 'live-1'}), self.client.posts)


if __name__ == '__main__':
    unittest.main()
