# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import urlparse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

ADDON = xbmcaddon.Addon('plugin.video.jellyfin.lite')
sys.path.insert(0, os.path.join(xbmc.translatePath(ADDON.getAddonInfo('path')),
                                'resources', 'lib'))

from jfclient import Client, AuthError, log, qs, utf8, TICKS  # noqa: E402

BASE = sys.argv[0]
HANDLE = int(sys.argv[1])
ARGS = dict((k, v[0]) for k, v in urlparse.parse_qs(sys.argv[2].lstrip('?')).items())

PLAYABLE = ('Movie', 'Episode', 'Video', 'MusicVideo', 'Trailer')
MUSIC_TYPES = ('MusicArtist', 'MusicAlbum', 'Audio')
FIELDS = 'Overview,ProductionYear,Genres,PremiereDate'
HOME = xbmcgui.Window(10000)


def setting_int(key, default):
    try:
        return int(ADDON.getSetting(key))
    except ValueError:
        return default


def plugin_url(**params):
    return BASE + '?' + qs(params)


# -- list item construction ----------------------------------------------
def music_info(it):
    info = {'title': it.get('Name') or ''}
    artists = it.get('Artists') or [a['Name'] for a in it.get('AlbumArtists') or [] if a.get('Name')]
    if it.get('Type') == 'MusicArtist':
        artists = [it.get('Name') or '']
    info['artist'] = u' / '.join(artists)
    info['album'] = it.get('Album') or (it.get('Type') == 'MusicAlbum' and it.get('Name')) or ''
    if it.get('Genres'):
        info['genre'] = u' / '.join(it['Genres'])
    if it.get('ProductionYear'):
        info['year'] = it['ProductionYear']
    if it.get('RunTimeTicks'):
        info['duration'] = int(it['RunTimeTicks'] // TICKS)
    if it.get('IndexNumber') is not None:
        info['tracknumber'] = it['IndexNumber']
    if it.get('ParentIndexNumber') is not None:
        info['discnumber'] = it['ParentIndexNumber']
    info['playcount'] = (it.get('UserData') or {}).get('PlayCount') or 0
    return info


def make_item(c, it, library=None):
    kind = it.get('Type')
    name = it.get('Name') or ''
    if kind == 'Episode':
        s, e = it.get('ParentIndexNumber'), it.get('IndexNumber')
        if s is not None and e is not None:
            name = u'%dx%02d. %s' % (s, e, name)
    li = xbmcgui.ListItem(name)
    tags = it.get('ImageTags') or {}
    width = setting_int('thumbwidth', 300)
    thumb = None
    if 'Primary' in tags:
        thumb = c.image(it['Id'], 'Primary', tags['Primary'], width)
    elif kind in ('Episode', 'Season') and it.get('SeriesId'):
        thumb = c.image(it['SeriesId'], 'Primary', it.get('SeriesPrimaryImageTag'), width)
    elif kind == 'Audio' and it.get('AlbumId'):
        thumb = c.image(it['AlbumId'], 'Primary', it.get('AlbumPrimaryImageTag'), width)
    if thumb:
        li.setIconImage(thumb)
        li.setThumbnailImage(thumb)
        li.setProperty('poster', thumb)
    if ADDON.getSetting('fanart') == 'true':
        bd = it.get('BackdropImageTags') or []
        if bd:
            li.setProperty('fanart_image', c.image(it['Id'], 'Backdrop', bd[0], 1280))
        elif it.get('ParentBackdropItemId'):
            li.setProperty('fanart_image', c.image(it['ParentBackdropItemId'], 'Backdrop', None, 1280))

    if kind in MUSIC_TYPES:
        li.setInfo('music', music_info(it))
        if kind == 'Audio':
            li.setProperty('IsPlayable', 'true')
            return plugin_url(mode='play', id=it['Id']), li, False
        return route_for(it, library), li, True

    info = {'title': it.get('Name') or ''}
    if it.get('Overview'):
        info['plot'] = it['Overview']
    if it.get('ProductionYear'):
        info['year'] = it['ProductionYear']
    if it.get('Genres'):
        info['genre'] = u' / '.join(it['Genres'])
    if it.get('PremiereDate'):
        info['aired'] = it['PremiereDate'][:10]
    if kind == 'Episode':
        info['tvshowtitle'] = it.get('SeriesName') or ''
        if it.get('IndexNumber') is not None:
            info['episode'] = it['IndexNumber']
        if it.get('ParentIndexNumber') is not None:
            info['season'] = it['ParentIndexNumber']
    runtime = it.get('RunTimeTicks')
    if runtime:
        info['duration'] = int(runtime // TICKS)  # Helix: seconds
    ud = it.get('UserData') or {}
    if kind in PLAYABLE:
        info['playcount'] = ud.get('Played') and 1 or 0
        if ud.get('PlaybackPositionTicks') and runtime:
            li.setProperty('ResumeTime', str(ud['PlaybackPositionTicks'] // TICKS))
            li.setProperty('TotalTime', str(runtime // TICKS))
    elif ud.get('UnplayedItemCount') is not None:
        li.setProperty('UnWatchedEpisodes', str(ud['UnplayedItemCount']))
    li.setInfo('video', info)

    if kind in PLAYABLE:
        li.setProperty('IsPlayable', 'true')
        played = ud.get('Played')
        li.addContextMenuItems([
            (played and 'Mark unwatched' or 'Mark watched',
             'RunPlugin(%s)' % plugin_url(mode='played', id=it['Id'],
                                          value=played and '0' or '1')),
            ('Play from beginning',
             'PlayMedia(%s)' % plugin_url(mode='play', id=it['Id'], resume='0')),
        ])
        return plugin_url(mode='play', id=it['Id']), li, False
    return route_for(it), li, True


def route_for(it, library=None):
    kind = it.get('Type')
    if it.get('CollectionType') == 'music':
        return plugin_url(mode='music', library=it['Id'])
    if kind == 'MusicArtist':
        return plugin_url(mode='music', view='albums', artist=it['Id'], library=library)
    if kind == 'MusicAlbum':
        return plugin_url(mode='music', view='songs', album=it['Id'], library=library)
    if kind == 'Series':
        return plugin_url(mode='seasons', id=it['Id'])
    if kind == 'Season':
        return plugin_url(mode='episodes', id=it['SeriesId'], season=it['Id'])
    return plugin_url(mode='items', parent=it['Id'], ctype=it.get('CollectionType') or '')


def add_items(c, items, content=None, library=None):
    entries = []
    for it in items:
        try:
            entries.append(make_item(c, it, library))
        except Exception as e:
            log('skipping item %s: %r' % (utf8(it.get('Name')), e))
    xbmcplugin.addDirectoryItems(HANDLE, entries, len(entries))
    if content:
        xbmcplugin.setContent(HANDLE, content)


def add_dir(label, url):
    xbmcplugin.addDirectoryItem(HANDLE, url, xbmcgui.ListItem(label), True)


# -- directory modes -----------------------------------------------------
def mode_root(c):
    add_dir('Continue Watching', plugin_url(mode='resume'))
    add_dir('Next Up', plugin_url(mode='nextup'))
    views = c.get('/UserViews', userId=c.user_id)
    for v in views.get('Items', []):
        if v.get('CollectionType') in ('books', 'photos', 'livetv', 'playlists'):
            continue
        url, li, folder = make_item(c, v)
        xbmcplugin.addDirectoryItem(HANDLE, url, li, True)
    add_dir('Search...', plugin_url(mode='search'))
    xbmcplugin.endOfDirectory(HANDLE)


CONTENT_FOR = {'movies': 'movies', 'tvshows': 'tvshows', 'homevideos': 'movies',
               'musicvideos': 'musicvideos', 'boxsets': 'movies'}


def mode_music(c):
    library = ARGS.get('library')
    view = ARGS.get('view')
    if not view:
        for label, section in (('Artists', 'artists'), ('Albums', 'albums'), ('Songs', 'songs')):
            add_dir(label, plugin_url(mode='music', library=library, view=section))
        add_dir('Search music...', plugin_url(mode='musicsearch', library=library))
        xbmcplugin.endOfDirectory(HANDLE)
        return

    kinds = {'artists': 'MusicArtist', 'albums': 'MusicAlbum', 'songs': 'Audio'}
    page = setting_int('pagesize', 50)
    start = int(ARGS.get('start', 0))
    params = {'userId': c.user_id, 'parentId': ARGS.get('album') or library,
              'startIndex': start, 'limit': page, 'fields': FIELDS,
              'recursive': 'true', 'sortBy': 'SortName', 'sortOrder': 'Ascending',
              'enableImageTypes': 'Primary', 'imageTypeLimit': 1,
              'enableTotalRecordCount': 'true'}
    if ARGS.get('q'):
        params['searchTerm'] = ARGS['q']
    if view == 'artists':
        path = '/Artists'
    else:
        path = '/Items'
        params['includeItemTypes'] = kinds[view]
        if ARGS.get('artist'):
            params['artistIds'] = ARGS['artist']
        if view == 'songs' and ARGS.get('album'):
            params['sortBy'] = 'ParentIndexNumber,IndexNumber,SortName'

    res = c.request('GET', path, params)
    if view == 'albums' and ARGS.get('artist') and start == 0:
        # Also expose singles and appearances that have no album entry.
        add_dir('All songs by this artist', plugin_url(mode='music', view='songs',
                                                      artist=ARGS['artist'], library=library))
    add_items(c, res.get('Items', []), view, library)
    total = res.get('TotalRecordCount') or 0
    if start + page < total:
        next_page = dict(ARGS)
        next_page.update(mode='music', start=start + page)
        add_dir('[Next page  %d-%d of %d]' % (start + page + 1,
                                             min(start + 2 * page, total), total),
                plugin_url(**next_page))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_music_search(c):
    term = ARGS.get('q')
    if not term:
        kb = xbmc.Keyboard('', 'Search Jellyfin music')
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText():
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return
        term = kb.getText()
    for label, view in (('Artists', 'artists'), ('Albums', 'albums'), ('Songs', 'songs')):
        add_dir(label, plugin_url(mode='music', view=view, library=ARGS.get('library'), q=term))
    xbmcplugin.endOfDirectory(HANDLE)


def mode_items(c):
    page = setting_int('pagesize', 50)
    start = int(ARGS.get('start', 0))
    ctype = ARGS.get('ctype', '')
    params = {'userId': c.user_id, 'parentId': ARGS['parent'], 'startIndex': start,
              'limit': page, 'fields': FIELDS, 'sortBy': 'SortName',
              'sortOrder': 'Ascending', 'enableImageTypes': 'Primary,Backdrop',
              'imageTypeLimit': 1, 'enableTotalRecordCount': 'true'}
    # Flatten movie libraries so folders-on-disk don't leak into the view.
    if ctype == 'movies':
        params.update({'includeItemTypes': 'Movie', 'recursive': 'true'})
    elif ctype == 'tvshows':
        params.update({'includeItemTypes': 'Series', 'recursive': 'true'})
    res = c.request('GET', '/Items', params)
    items = res.get('Items', [])
    add_items(c, items, CONTENT_FOR.get(ctype))
    total = res.get('TotalRecordCount', 0)
    if start + page < total:
        add_dir('[Next page  %d-%d of %d]' % (start + page + 1,
                                               min(start + 2 * page, total), total),
                plugin_url(mode='items', parent=ARGS['parent'], ctype=ctype,
                           start=start + page))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_seasons(c):
    res = c.get('/Shows/%s/Seasons' % ARGS['id'], userId=c.user_id, fields=FIELDS,
                enableImages='true', imageTypeLimit=1)
    items = res.get('Items', [])
    if len(items) == 1:  # skip the pointless single-season hop
        ARGS['season'] = items[0]['Id']
        return mode_episodes(c)
    add_items(c, items, 'seasons')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_episodes(c):
    res = c.get('/Shows/%s/Episodes' % ARGS['id'], userId=c.user_id,
                seasonId=ARGS.get('season'), fields=FIELDS,
                enableImages='true', imageTypeLimit=1)
    add_items(c, res.get('Items', []), 'episodes')
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_EPISODE)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_resume(c):
    res = c.get('/UserItems/Resume', userId=c.user_id, limit=50, fields=FIELDS,
                mediaTypes='Video', enableImageTypes='Primary,Backdrop', imageTypeLimit=1)
    add_items(c, res.get('Items', []), 'episodes')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_nextup(c):
    res = c.get('/Shows/NextUp', userId=c.user_id, limit=50, fields=FIELDS,
                enableImageTypes='Primary,Backdrop', imageTypeLimit=1)
    add_items(c, res.get('Items', []), 'episodes')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def mode_search(c):
    term = ARGS.get('q')
    if not term:
        kb = xbmc.Keyboard('', 'Search Jellyfin')
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText():
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return
        term = kb.getText()
    res = c.get('/Items', userId=c.user_id, searchTerm=term, recursive='true',
                includeItemTypes='Movie,Series,Episode', limit=100, fields=FIELDS,
                enableImageTypes='Primary', imageTypeLimit=1)
    add_items(c, res.get('Items', []), 'movies')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


# -- playback ------------------------------------------------------------
def audio_device_profile():
    bitrate = setting_int('audiobitrate', 320) * 1000
    return {
        'Name': 'Kodi 14 Music Lite',
        'MaxStreamingBitrate': bitrate,
        'MaxStaticBitrate': bitrate,
        'DirectPlayProfiles': [
            {'Type': 'Audio', 'Container': 'mp3,flac,wav,ogg,m4a,aac',
             'AudioCodec': 'mp3,flac,pcm_s16le,pcm_s24le,vorbis,aac,alac'},
        ],
        'TranscodingProfiles': [
            {'Type': 'Audio', 'Container': 'mp3', 'AudioCodec': 'mp3',
             'Context': 'Streaming', 'Protocol': 'http', 'MaxAudioChannels': '2',
             'EstimateContentLength': True, 'TranscodeSeekInfo': 'Auto'},
        ],
        'ContainerProfiles': [], 'CodecProfiles': [], 'SubtitleProfiles': [],
    }


def device_profile():
    maxh = setting_int('maxheight', 720)
    channels = str(setting_int('channels', 2))
    h264_limits = [
        {'Condition': 'LessThanEqual', 'Property': 'VideoLevel', 'Value': '41', 'IsRequired': False},
        {'Condition': 'LessThanEqual', 'Property': 'VideoBitDepth', 'Value': '8', 'IsRequired': False},
        {'Condition': 'LessThanEqual', 'Property': 'Height', 'Value': str(maxh), 'IsRequired': True},
        {'Condition': 'EqualsAny', 'Property': 'VideoProfile',
         'Value': 'high|main|baseline|constrained baseline', 'IsRequired': False},
        {'Condition': 'Equals', 'Property': 'IsInterlaced', 'Value': 'false', 'IsRequired': False},
    ]
    return {
        'Name': 'Kodi 14 AppleTV3 Lite',
        'MaxStreamingBitrate': setting_int('bitrate', 2500) * 1000,
        'MaxStaticBitrate': setting_int('bitrate', 2500) * 1000,
        'DirectPlayProfiles': [
            {'Type': 'Video', 'Container': 'mkv,mp4,m4v,mov,ts,mpegts',
             'VideoCodec': 'h264', 'AudioCodec': 'aac,mp3,ac3,eac3,mp2'},
        ],
        'TranscodingProfiles': [
            {'Type': 'Video', 'Container': 'ts', 'VideoCodec': 'h264',
             'AudioCodec': 'aac,mp3,ac3', 'Context': 'Streaming',
             'Protocol': 'http', 'MaxAudioChannels': channels,
             'EstimateContentLength': True, 'TranscodeSeekInfo': 'Auto',
             'CopyTimestamps': False},
        ],
        'ContainerProfiles': [],
        'CodecProfiles': [
            {'Type': 'Video', 'Codec': 'h264', 'Conditions': h264_limits},
            {'Type': 'VideoAudio', 'Conditions': [
                {'Condition': 'LessThanEqual', 'Property': 'AudioChannels',
                 'Value': channels, 'IsRequired': False}]},
        ],
        'SubtitleProfiles': [
            {'Format': 'srt', 'Method': 'External'},
            {'Format': 'subrip', 'Method': 'External'},
            {'Format': 'ass', 'Method': 'Encode'},
            {'Format': 'ssa', 'Method': 'Encode'},
            {'Format': 'vtt', 'Method': 'Encode'},
            {'Format': 'pgssub', 'Method': 'Encode'},
            {'Format': 'dvdsub', 'Method': 'Encode'},
            {'Format': 'dvbsub', 'Method': 'Encode'},
        ],
    }


def fmt_time(sec):
    sec = int(sec)
    if sec >= 3600:
        return '%d:%02d:%02d' % (sec // 3600, sec % 3600 // 60, sec % 60)
    return '%d:%02d' % (sec // 60, sec % 60)


def fail():
    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


def mode_play(c):
    item_id = ARGS['id']
    res = c.get('/Items', userId=c.user_id, ids=item_id, fields=FIELDS)
    items = res.get('Items') or []
    if not items:
        return fail()
    it = items[0]
    is_audio = it.get('Type') == 'Audio'

    # Resume choice.
    start = 0
    pos = (it.get('UserData') or {}).get('PlaybackPositionTicks') or 0
    if not is_audio and pos and ARGS.get('resume') != '0' and ADDON.getSetting('askresume') == 'true':
        choice = xbmcgui.Dialog().select(utf8(it.get('Name') or ''),
                                         ['Resume from ' + fmt_time(pos // TICKS),
                                          'Play from beginning'])
        if choice < 0:
            return fail()
        if choice == 0:
            start = pos // TICKS

    body = {
        'UserId': c.user_id,
        'DeviceProfile': audio_device_profile() if is_audio else device_profile(),
        'MaxStreamingBitrate': (setting_int('audiobitrate', 320) if is_audio
                                else setting_int('bitrate', 2500)) * 1000,
        'MaxAudioChannels': 2 if is_audio else setting_int('channels', 2),
        'EnableDirectPlay': False,  # server-local paths are useless to us
        'EnableDirectStream': ADDON.getSetting('directstream') == 'true' and not start,
        'EnableTranscoding': True,
        'AllowVideoStreamCopy': True,
        'AllowAudioStreamCopy': True,
        'AutoOpenLiveStream': True,
        'StartTimeTicks': start * TICKS,
    }
    if not is_audio and ADDON.getSetting('subs') == '1':
        body['SubtitleStreamIndex'] = -1
    info = c.post('/Items/%s/PlaybackInfo' % item_id, body,
                  params={'userId': c.user_id}, timeout=60)
    if info.get('ErrorCode'):
        xbmcgui.Dialog().ok('Jellyfin', 'Playback refused: %s' % utf8(info['ErrorCode']))
        return fail()
    sources = info.get('MediaSources') or []
    if not sources:
        xbmcgui.Dialog().ok('Jellyfin', 'Server offered no media source for this item.')
        return fail()
    src = sources[0]
    session = info.get('PlaySessionId') or ''

    if src.get('SupportsDirectStream') and not src.get('TranscodingUrl') \
            and not start:
        method = 'DirectStream'
        container = (src.get('Container') or ('mp3' if is_audio else 'mkv')).split(',')[0]
        endpoint = 'Audio' if is_audio else 'Videos'
        url = c.url('/%s/%s/stream.%s' % (endpoint, item_id, container),
                    {'static': 'true', 'mediaSourceId': src['Id'],
                     'playSessionId': session, 'deviceId': c.device_id,
                     'tag': src.get('ETag')})
        mime = None
    elif src.get('TranscodingUrl'):
        method = 'Transcode'
        url = c.server + src['TranscodingUrl']
        if 'ApiKey=' not in url and 'api_key=' not in url:
            url += ('&' if '?' in url else '?') + 'ApiKey=' + c.token
        mime = 'audio/mpeg' if is_audio else 'video/mp2t'
    else:
        xbmcgui.Dialog().ok('Jellyfin', 'Server offered no playable stream for this item.')
        return fail()
    log('%s %s -> %s' % (method, item_id, url.replace(c.token, '<token>')))

    li = xbmcgui.ListItem(path=url)
    if is_audio:
        li.setInfo('music', music_info(it))
        tags = it.get('ImageTags') or {}
        image_id = it['Id'] if tags.get('Primary') else it.get('AlbumId')
        if image_id:
            li.setThumbnailImage(c.image(image_id, 'Primary',
                                        tags.get('Primary') or it.get('AlbumPrimaryImageTag'),
                                        setting_int('thumbwidth', 300)))
    if mime and hasattr(li, 'setMimeType'):
        li.setMimeType(mime)
    # External text subtitles the server chose for us.
    subs = []
    want = src.get('DefaultSubtitleStreamIndex')
    for s in ([] if is_audio else src.get('MediaStreams') or []):
        if s.get('Type') == 'Subtitle' and s.get('DeliveryMethod') == 'External' \
                and s.get('DeliveryUrl') and s.get('Index') == want:
            su = s['DeliveryUrl']
            su = su.startswith('http') and su or c.server + su
            if 'ApiKey=' not in su and 'api_key=' not in su:
                su += (('?' in su) and '&' or '?') + 'ApiKey=' + c.token
            subs.append(su)
    if subs and hasattr(li, 'setSubtitles'):
        li.setSubtitles(subs)

    # Progressive streams start at the server-side offset; report absolute progress.
    HOME.setProperty('jfl.pending', json.dumps({
        'ItemId': item_id, 'MediaSourceId': src['Id'], 'PlaySessionId': session,
        'PlayMethod': method, 'Offset': start,
        'Runtime': (it.get('RunTimeTicks') or 0) // TICKS,
        'ts': time.time(),
    }))
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def mode_played(c):
    path = '/UserPlayedItems/%s' % ARGS['id']
    if ARGS.get('value') == '1':
        c.request('POST', path, {'userId': c.user_id})
    else:
        c.request('DELETE', path, {'userId': c.user_id})
    xbmc.executebuiltin('Container.Refresh')


def mode_logout(c):
    c.logout()
    xbmcgui.Dialog().ok('Jellyfin', 'Saved login cleared.')


MODES = {'root': mode_root, 'items': mode_items, 'seasons': mode_seasons,
         'episodes': mode_episodes, 'resume': mode_resume, 'nextup': mode_nextup,
         'search': mode_search, 'play': mode_play, 'played': mode_played,
         'logout': mode_logout, 'music': mode_music, 'musicsearch': mode_music_search}


def main():
    mode = ARGS.get('mode', 'root')
    c = Client(ADDON)
    try:
        MODES[mode](c)
    except AuthError as e:
        xbmcgui.Dialog().ok('Jellyfin', str(e), 'Check the add-on settings.')
        if mode == 'play':
            fail()
        elif mode not in ('played', 'logout'):
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        ADDON.openSettings()
    except Exception as e:
        log('error in mode %s: %r' % (mode, e), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Jellyfin', 'Request failed:', utf8(repr(e))[:200])
        if mode == 'play':
            fail()
        elif mode not in ('played', 'logout'):
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == '__main__':
    main()
