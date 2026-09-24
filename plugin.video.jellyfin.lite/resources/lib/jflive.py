# -*- coding: utf-8 -*-
"""Progressive live TV stream negotiation shared by the plugin and service."""
import time
import urlparse
import copy
import hashlib

from jfclient import log, qs


def stream_hash(url):
    return hashlib.sha256(url.split('|', 1)[0].encode('utf-8')).hexdigest()


def close_live(client, state):
    live_id = state.get('LiveStreamId')
    if live_id:
        try:
            client.post('/LiveStreams/Close', params={'liveStreamId': live_id}, timeout=10)
        except Exception:
            log('could not close live TV stream')


def open_live(client, channel_id, profile):
    profile = copy.deepcopy(profile)
    for transcode in profile['TranscodingProfiles']:
        transcode['EstimateContentLength'] = False  # Live streams have no finite length.
    info = client.post('/Items/%s/PlaybackInfo' % channel_id, {
        'UserId': client.user_id, 'DeviceProfile': profile,
        'MaxStreamingBitrate': profile['MaxStreamingBitrate'],
        'MaxAudioChannels': int(profile['TranscodingProfiles'][0]['MaxAudioChannels']),
        'EnableDirectPlay': False, 'EnableDirectStream': False,
        'EnableTranscoding': True, 'AllowVideoStreamCopy': True,
        'AllowAudioStreamCopy': True, 'AutoOpenLiveStream': True,
        'StartTimeTicks': 0,
    }, params={'userId': client.user_id}, timeout=30)
    sources = info.get('MediaSources') or []
    if info.get('ErrorCode') or not sources:
        raise RuntimeError('Jellyfin offered no live TV stream')
    source = sources[0]
    try:
        path = source.get('TranscodingUrl') or ''
        if not urlparse.urlsplit(path).path.lower().endswith('.ts'):
            raise RuntimeError('Jellyfin did not offer a progressive TS live stream')
        # Jellyfin normally supplies both values. Add them if absent from its URL.
        query = dict((k.lower(), v) for k, v in urlparse.parse_qs(urlparse.urlsplit(path).query).items())
        extra = {}
        if 'apikey' not in query and 'api_key' not in query:
            extra['ApiKey'] = client.token
        if source.get('LiveStreamId') and 'livestreamid' not in query:
            extra['LiveStreamId'] = source['LiveStreamId']
        url = path if path.startswith(('http://', 'https://')) else client.server + path
        if extra:
            url += ('&' if '?' in url else '?') + qs(extra)
        state = {
            'ItemId': channel_id, 'MediaSourceId': source['Id'],
            'PlaySessionId': info.get('PlaySessionId') or '',
            'LiveStreamId': source.get('LiveStreamId'), 'PlayMethod': 'Transcode',
            'Live': True, 'Profile': profile, 'Offset': 0, 'Runtime': 0,
            'RetryCount': 0, 'ts': time.time(), 'StreamHash': stream_hash(url),
        }
        return url, state
    except Exception:
        close_live(client, source)
        raise
