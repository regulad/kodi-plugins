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
