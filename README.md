# Regulad Helix add-ons

This orphan branch contains the unpacked Kodi 14 add-ons, one directory per
add-on ID. Jellyfin Lite 0.1.0 was imported unchanged from its supplied ZIP.

The independent `master` branch owns the build and deployment tools. Pushing
this branch calls its reusable publishing workflow. Increment `addon.xml`'s
version whenever published package contents change; published ZIPs are immutable.

Install the repository ZIP from:

http://kodi.regulad.xyz/helix/repository.regulad.helix/repository.regulad.helix-1.0.0.zip

Then install Jellyfin Lite from Regulad Helix in Kodi. Configure the Jellyfin
server and credentials in the add-on settings. An ATV runtime smoke test is
still required; CI syntax and package checks do not establish playback support.

Jellyfin Lite appears in both Video and Music add-ons, with separate listings
and search for each category. Opening a bare plugin URL without Kodi's content
type retains the combined library view. Open a music library to
browse Artists, Albums, or Songs, or search within that library. Artist pages
also link to all their songs; albums list tracks in disc/track order. Music
uses direct audio streaming where supported, with progressive MP3 transcoding
otherwise. Set its bitrate separately under Music in the add-on settings.
Video transcoding continues to use progressive TS.

The Video view also includes **Live TV**, with paginated TV channels, logos,
channel numbers, and current-program details. Jellyfin must have live TV configured
and allow the user to access it. Live streams use the existing AVC/TS profile;
Playback settings show **Codec: AVC** as a disabled field.

If a live stream ends unexpectedly, the background service requests a fresh
Jellyfin stream after 2 seconds, with up to three reconnects at 2/4/8-second
backoff. A minute of successful playback restores the retry budget. Stop,
another playback selection, and service shutdown cancel recovery. Helix reports
explicit Stop and some failed opens through the same callback, so those events
are treated as cancellation rather than forcing playback to resume. Live playback
and a real network-drop/recovery still need an ATV smoke test.

The add-on description records Jellyfin **10.11.x** as the tested server version.
Run the browsing and playback regression tests with Python 2.7:

```sh
python -m unittest discover -s tests -v
```
