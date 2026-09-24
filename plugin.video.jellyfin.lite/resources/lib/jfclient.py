# -*- coding: utf-8 -*-
# Minimal Jellyfin API client. Python 2.6 compatible: no dict/set
# comprehensions, no auto-numbered str.format, urllib2 only.
import json
import urllib
import urllib2
import urlparse
import uuid

import xbmc
import xbmcaddon

ADDON_ID = 'plugin.video.jellyfin.lite'
CLIENT_NAME = 'Kodi Helix Lite'
VERSION = '0.3.1'
TICKS = 10000000  # 100 ns ticks per second


def log(msg, level=None):
    if level is None:
        level = xbmc.LOGNOTICE
    if isinstance(msg, unicode):
        msg = msg.encode('utf-8')
    xbmc.log('[jellyfin.lite] ' + msg, level)


def utf8(v):
    if isinstance(v, unicode):
        return v.encode('utf-8')
    return v


def qs(params):
    """urlencode that tolerates unicode values and drops None."""
    pairs = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = v and 'true' or 'false'
        pairs.append((utf8(k), utf8(v) if isinstance(v, basestring) else str(v)))
    return urllib.urlencode(pairs)


class AuthError(Exception):
    pass


class Request(urllib2.Request):
    def __init__(self, url, data=None, headers=None, method=None):
        urllib2.Request.__init__(self, url, data, headers or {})
        self._method = method

    def get_method(self):
        if self._method:
            return self._method
        return urllib2.Request.get_method(self)


class Client(object):
    def __init__(self, addon=None):
        self.addon = addon or xbmcaddon.Addon(ADDON_ID)
        self.server = self.addon.getSetting('server').strip().rstrip('/')
        if self.server and '://' not in self.server:
            self.server = 'http://' + self.server.lstrip('/')
        if self.server:
            parts = urlparse.urlsplit(self.server)
            if parts.port is None:
                self.server = urlparse.urlunsplit((parts.scheme, parts.netloc + ':8096',
                                                  parts.path, parts.query, parts.fragment))
        self.token = self.addon.getSetting('token')
        self.user_id = self.addon.getSetting('userid')
        self.device_id = self.addon.getSetting('deviceid')
        if not self.device_id:
            self.device_id = uuid.uuid4().hex
            self.addon.setSetting('deviceid', self.device_id)

    # -- plumbing ---------------------------------------------------------
    def _auth_header(self):
        h = ('MediaBrowser Client="%s", Device="AppleTV", DeviceId="%s", Version="%s"'
             % (CLIENT_NAME, self.device_id, VERSION))
        if self.token:
            h += ', Token="%s"' % self.token
        return h

    def _raw(self, method, path, params=None, body=None, timeout=30):
        url = self.server + path
        if params:
            url += '?' + qs(params)
        data = None
        headers = {'Authorization': self._auth_header(),
                   'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        elif method in ('POST', 'DELETE'):
            data = ''
        req = Request(url, data, headers, method)
        try:
            resp = urllib2.urlopen(req, timeout=timeout)
        except urllib2.HTTPError as e:
            if e.code == 401:
                raise AuthError(path)
            raise
        raw = resp.read()
        resp.close()
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                return None
        return None

    def request(self, method, path, params=None, body=None, timeout=30):
        if not self.token:
            self.login()
        try:
            return self._raw(method, path, params, body, timeout)
        except AuthError:
            # Token revoked or expired: log in again once.
            log('401 on %s, re-authenticating' % path)
            self.token = ''
            self.login()
            return self._raw(method, path, params, body, timeout)

    def get(self, path, **params):
        return self.request('GET', path, params)

    def post(self, path, body=None, params=None, timeout=30):
        return self.request('POST', path, params, body, timeout)

    # -- auth -------------------------------------------------------------
    def login(self):
        user = self.addon.getSetting('username')
        pw = self.addon.getSetting('password')
        if not self.server or not user:
            raise AuthError('Server URL and username must be set')
        self.token = ''
        try:
            res = self._raw('POST', '/Users/AuthenticateByName',
                            body={'Username': user, 'Pw': pw})
        except AuthError:
            raise AuthError('Jellyfin rejected the username/password')
        self.token = res['AccessToken']
        self.user_id = res['User']['Id']
        self.addon.setSetting('token', self.token)
        self.addon.setSetting('userid', self.user_id)
        log('logged in as %s' % utf8(res['User'].get('Name', user)))

    def logout(self):
        if self.token:
            try:
                self._raw('POST', '/Sessions/Logout')
            except Exception:
                pass
        self.token = ''
        self.user_id = ''
        self.addon.setSetting('token', '')
        self.addon.setSetting('userid', '')

    # -- URLs Kodi fetches directly (no custom headers possible) ------------
    def url(self, path, params=None):
        p = dict(params or {})
        p['ApiKey'] = self.token
        return self.server + path + '?' + qs(p)

    def image(self, item_id, kind='Primary', tag=None, width=300):
        # Images are served without auth; keep them small for 512 MB of RAM.
        p = {'maxWidth': width, 'quality': 80}
        if tag:
            p['tag'] = tag
        return '%s/Items/%s/Images/%s?%s' % (self.server, item_id, kind, qs(p))
