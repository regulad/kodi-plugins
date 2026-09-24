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

HOME = xbmcgui.Window(10000)
PROGRESS_EVERY = 10  # seconds


class Tracker(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)
        self.active = None      # dict from the plugin's jfl.pending
        self.position = 0.0     # seconds, including any progressive offset
        self.paused = False
        self.last_report = 0
        self.need_start = False

    # Kodi queues these callbacks and runs them on this script's thread
    # during xbmc.sleep(), so blocking here never stalls the player itself.
    def onPlayBackStarted(self):
        pending = HOME.getProperty('jfl.pending')
        if self.active:
            self.finish()
        if not pending:
            return
        HOME.clearProperty('jfl.pending')
        try:
            pending = json.loads(pending)
        except ValueError:
            return
        # Ignore a stale hand-off (plugin resolved, but playback never began).
        if time.time() - pending.get('ts', 0) > 120:
            return
        self.active = pending
        self.position = float(self.active.get('Offset') or 0)
        self.paused = False
        self.need_start = True

    def onPlayBackPaused(self):
        self.paused = True
        self.last_report = 0

    def onPlayBackResumed(self):
        self.paused = False
        self.last_report = 0

    def onPlayBackSeek(self, t, offset):
        self.last_report = 0

    def onPlayBackStopped(self):
        self.finish()

    def onPlayBackEnded(self):
        self.finish()

    # -- reporting ---------------------------------------------------------
    def body(self):
        a = self.active
        return {'ItemId': a['ItemId'], 'MediaSourceId': a['MediaSourceId'],
                'PlaySessionId': a['PlaySessionId'], 'PlayMethod': a['PlayMethod'],
                'PositionTicks': int(self.position * TICKS), 'IsPaused': self.paused,
                'CanSeek': True}

    def send(self, path, body):
        try:
            Client(ADDON).post(path, body, timeout=10)
        except Exception as e:
            log('report %s failed: %r' % (path, e), xbmc.LOGWARNING)

    def finish(self):
        if not self.active:
            return
        body = self.body()
        self.active = None
        for k in ('IsPaused', 'CanSeek', 'PlayMethod'):  # not part of PlaybackStopInfo
            body.pop(k, None)
        self.send('/Sessions/Playing/Stopped', body)

    def tick(self):
        if not self.active:
            return
        try:
            if self.isPlayingVideo():
                self.position = self.getTime() + float(self.active.get('Offset') or 0)
        except RuntimeError:
            return
        if self.need_start:
            self.need_start = False
            self.send('/Sessions/Playing', self.body())
            self.last_report = time.time()
            return
        now = time.time()
        if now - self.last_report >= PROGRESS_EVERY:
            self.last_report = now
            self.send('/Sessions/Playing/Progress', self.body())


def aborted(monitor):
    if monitor is not None and hasattr(monitor, 'abortRequested'):
        return monitor.abortRequested()
    return xbmc.abortRequested


def main():
    log('service started')
    monitor = hasattr(xbmc, 'Monitor') and xbmc.Monitor() or None
    player = Tracker()
    while not aborted(monitor):
        player.tick()
        xbmc.sleep(1000)
    player.finish()
    log('service stopped')


if __name__ == '__main__':
    main()
