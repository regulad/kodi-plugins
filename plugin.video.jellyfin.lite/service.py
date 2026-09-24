# -*- coding: utf-8 -*-
# Background service: reports playback to Jellyfin (so resume points and
# watched state sync, and the server kills finished transcodes).
import os
import sys
import json
import time

import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon('plugin.video.jellyfin.lite')
sys.path.insert(0, os.path.join(xbmc.translatePath(ADDON.getAddonInfo('path')),
                                'resources', 'lib'))

from jfclient import Client, log, TICKS  # noqa: E402
from jflive import open_live, close_live, stream_hash  # noqa: E402

HOME = xbmcgui.Window(10000)
PROGRESS_EVERY = 10  # seconds
LIVE_RETRIES = 3
LIVE_START_TIMEOUT = 45


def pending_playback():
    try:
        value = json.loads(HOME.getProperty('jfl.pending') or '{}')
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


class Tracker(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)
        self.active = None      # dict from the plugin's jfl.pending
        self.position = 0.0     # seconds, including any progressive offset
        self.paused = False
        self.last_report = 0
        self.need_start = False
        self.reconnect = None
        self.starting = None
        self.live_reported = False

    # Kodi queues these callbacks and runs them on this script's thread
    # during xbmc.sleep(), so blocking here never stalls the player itself.
    def onPlayBackStarted(self):
        pending = pending_playback()
        self.reconnect = None
        if self.starting and self.starting.get('PlaySessionId') != pending.get('PlaySessionId'):
            self.discard_starting()
        self.starting = None
        if self.active:
            self.finish()
        if not pending:
            return
        HOME.clearProperty('jfl.pending')
        # Ignore a stale hand-off (plugin resolved, but playback never began).
        if time.time() - pending.get('ts', 0) > 120 or (pending.get('Live') and not self.same_intent(pending)):
            close_live(Client(ADDON), pending)
            return
        if pending.get('Live'):
            try:
                matching_stream = pending.get('StreamHash') == stream_hash(self.getPlayingFile())
            except RuntimeError:
                matching_stream = False
            if not matching_stream:
                close_live(Client(ADDON), pending)
                return
        self.active = pending
        self.position = float(self.active.get('Offset') or 0)
        self.paused = False
        self.need_start = True
        self.live_reported = False

    def onPlayBackPaused(self):
        self.paused = True
        self.last_report = 0

    def onPlayBackResumed(self):
        self.paused = False
        self.last_report = 0

    def onPlayBackSeek(self, t, offset):
        self.last_report = 0

    def onPlayBackStopped(self):
        # Helix uses this callback for explicit Stop as well as aborted opens.
        # Never restart after it: the viewer must remain able to stop playback.
        had_playback = self.active is not None or self.starting is not None
        self.reconnect = None
        self.discard_starting()
        self.finish()
        if not had_playback:
            pending = pending_playback()
            if pending.get('Live'):
                HOME.clearProperty('jfl.pending')
                close_live(Client(ADDON), pending)

    def onPlayBackEnded(self):
        # An endless live stream reaching EOF means the connection was lost.
        state = self.active or self.starting
        self.discard_starting()
        self.finish(failed=bool(state and state.get('Live')))
        if state and state.get('Live'):
            self.schedule_reconnect(state)

    def same_intent(self, state):
        return state.get('Intent') == HOME.getProperty('jfl.intent')

    def discard_starting(self):
        if self.starting:
            state = self.starting
            self.starting = None
            if pending_playback().get('PlaySessionId') == state.get('PlaySessionId'):
                HOME.clearProperty('jfl.pending')
            close_live(Client(ADDON), state)

    def schedule_reconnect(self, state):
        self.reconnect = None
        if not self.same_intent(state) or xbmc.abortRequested:
            return
        attempt = int(state.get('RetryCount', 0)) + 1
        if attempt > LIVE_RETRIES:
            log('live TV reconnect limit reached', xbmc.LOGWARNING)
            return
        state = dict(state)
        state['RetryCount'] = attempt
        self.reconnect = {'State': state, 'At': time.time() + 2 ** attempt}
        log('live TV reconnect scheduled (%d/%d)' % (attempt, LIVE_RETRIES))

    def tick_reconnect(self):
        task = self.reconnect
        if not task:
            return
        old = task['State']
        if not self.same_intent(old) or xbmc.abortRequested:
            self.reconnect = None
            return
        if time.time() < task['At']:
            return
        if self.isPlaying():
            self.reconnect = None
            return
        state = None
        client = Client(ADDON)
        try:
            url, state = open_live(client, old['ItemId'], old['Profile'])
            state.update(Intent=old['Intent'], Name=old.get('Name', 'Live TV'),
                         RetryCount=old['RetryCount'])
            # Dispatch Stop/switch callbacks queued while the HTTP request ran.
            xbmc.sleep(1)
            if self.reconnect is not task or not self.same_intent(old) or self.isPlaying() or xbmc.abortRequested:
                close_live(client, state)
                self.reconnect = None
                return
            li = xbmcgui.ListItem(path=url)
            li.setInfo('video', {'title': state['Name']})
            li.setMimeType('video/mp2t')
            self.starting = state
            HOME.setProperty('jfl.pending', json.dumps(state))
            self.reconnect = None
            self.play(url, li)
        except Exception:
            retry_allowed = self.reconnect is task or self.starting is state and state is not None
            if state:
                if self.starting is state:
                    self.discard_starting()
                else:
                    close_live(client, state)
            # A stopped/cancelled request must not schedule itself again.
            if retry_allowed:
                self.schedule_reconnect(old)
            log('live TV reconnect failed', xbmc.LOGWARNING)

    def shutdown(self):
        self.reconnect = None
        self.discard_starting()
        self.finish()
        pending = pending_playback()
        if pending.get('Live'):
            HOME.clearProperty('jfl.pending')
            close_live(Client(ADDON), pending)

    # -- reporting ---------------------------------------------------------
    def body(self):
        a = self.active
        body = {'ItemId': a['ItemId'], 'MediaSourceId': a['MediaSourceId'],
                'PlaySessionId': a['PlaySessionId'], 'PlayMethod': a['PlayMethod'],
                'PositionTicks': int(self.position * TICKS), 'IsPaused': self.paused,
                'CanSeek': not a.get('Live', False)}
        if a.get('LiveStreamId'):
            body['LiveStreamId'] = a['LiveStreamId']
        return body

    def send(self, path, body):
        try:
            Client(ADDON).post(path, body, timeout=10)
            return True
        except Exception as e:
            log('report %s failed: %r' % (path, e), xbmc.LOGWARNING)
            return False

    def finish(self, failed=False):
        if not self.active:
            return
        body = self.body()
        state = self.active
        self.active = None
        for k in ('IsPaused', 'CanSeek', 'PlayMethod'):  # not part of PlaybackStopInfo
            body.pop(k, None)
        if failed:
            body['Failed'] = True
        reported = self.send('/Sessions/Playing/Stopped', body)
        # Jellyfin closes a reported live stream on Stop. Close explicitly only
        # when reporting failed or the stream never got a successful check-in.
        if state.get('LiveStreamId') and (not reported or not self.live_reported):
            close_live(Client(ADDON), state)

    def tick(self):
        if not self.active:
            if self.starting and time.time() - self.starting['ts'] > LIVE_START_TIMEOUT:
                state = self.starting
                self.discard_starting()
                self.schedule_reconnect(state)
            self.tick_reconnect()
            pending = pending_playback()
            if pending.get('Live') and time.time() - pending.get('ts', 0) > 120:
                HOME.clearProperty('jfl.pending')
                close_live(Client(ADDON), pending)
            return
        try:
            if self.isPlayingVideo() or self.isPlayingAudio():
                self.position = self.getTime() + float(self.active.get('Offset') or 0)
        except RuntimeError:
            return
        if self.active.get('Live') and self.position >= 60:
            self.active['RetryCount'] = 0  # A minute of playback restores the retry budget.
        if self.need_start:
            self.need_start = False
            self.live_reported = bool(self.send('/Sessions/Playing', self.body()))
            self.last_report = time.time()
            return
        now = time.time()
        if now - self.last_report >= PROGRESS_EVERY:
            self.last_report = now
            if self.send('/Sessions/Playing/Progress', self.body()):
                self.live_reported = True


def aborted(monitor):
    if monitor is not None and hasattr(monitor, 'abortRequested'):
        return monitor.abortRequested()
    return xbmc.abortRequested


def main():
    log('service started')
    monitor = hasattr(xbmc, 'Monitor') and xbmc.Monitor() or None
    player = Tracker()
    try:
        while not aborted(monitor):
            player.tick()
            xbmc.sleep(1000)
    finally:
        player.shutdown()
    log('service stopped')


if __name__ == '__main__':
    main()
