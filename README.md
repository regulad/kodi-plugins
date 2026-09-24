# Kodi repository publishing

`master` contains the publishing tools and GitHub repository settings.
`helix` has an independent root commit and contains unpacked add-on sources,
including Jellyfin Lite and `repository.regulad.helix`. Its small workflow calls
the reusable pipeline here. Both a push to either branch and a manual dispatch
publish Helix. No deployment secrets are supplied to pull-request workflows.

## Pipeline

The Ubuntu runner checks out Helix and Chad Parry's `create_repository.py` 2.3.8
at commit `27c38eddda89e626d05b029edb29fbc83bb27201`. The upstream code retains
its GPL license in that checkout; it is fetched during CI rather than copied here.
The builder exports only tracked add-on directories, checks dependencies, makes
stable ZIPs and MD5 sidecars, and creates an uncompressed catalog and download page.
Python 2.7 syntax and the Helix branch's browsing/playback regression tests run
in `python:2.7.18-slim`; runtime testing on the ATV is still necessary. The
complete static tree is also saved as a GHA artifact.

The Tailscale action signs in using `TS_AUTHKEY`. A following `tailscale set`
explicitly enables subnet routes and MagicDNS without duplicating the action's
own `tailscale up` flags. The key must permit the runner to join the tailnet, and the
tailnet must permit SSH traffic to the destination, including any subnet route.
Hostname resolution explicitly queries Tailscale's resolver at `100.100.100.100`;
short names are expanded with the tailnet's MagicDNS suffix. IP literals work too.

## Secrets

| Secret | Value |
| --- | --- |
| `TS_AUTHKEY` | Reusable, ephemeral Tailscale authentication key |
| `SSH_KEY_HOST` | Hostname or IP address; SSH port 22 |
| `SSH_KEY_USER` | Remote SSH account |
| `SSH_KEY_BLOB` | Full `BEGIN/END OPENSSH PRIVATE KEY` text, including its base64 body; a base64 encoding of the entire text is also accepted |
| `SSH_KEY_PASSPHRASE` | Passphrase for the encrypted key |
| `STATIC_FILE_BASE` | Absolute remote web root, owned by the SSH user |

OpenSSH loads the key into a temporary agent using an askpass helper. ED25519
and other OpenSSH-supported key types work. No key algorithm is imposed. The
private key file is removed after loading, and the agent is stopped on exit.
Host keys use `accept-new` in a job-local known-hosts file: first-use trust over
the authenticated tailnet, not a persistent, independently pinned host identity.

## Publication

The destination is Linux/POSIX with `sh`, `tar`, GNU `cp`, `mv`, `cmp`, and
`md5sum`. No Python, Git, rsync, sudo, or additional service is needed there.
The runner streams a tar archive over SSH into a fresh directory under
`STATIC_FILE_BASE/.releases/`, retaining prior packages. Checksums are verified
before an atomic symlink replacement publishes `STATIC_FILE_BASE/helix`.
The webserver must follow this symlink, serve `/helix/` over HTTP without a
mandatory HTTPS redirect, and allow its `index.html` download page.

An existing real directory at `STATIC_FILE_BASE/helix` is deliberately not
replaced. Old release directories are retained; prune them manually when safe.
The deployment refuses different bytes for an already-published package version.
Increment the add-on version for any package change. A server rollback does not
downgrade add-ons already installed on clients.

Install the repository on the ATV using **Install from zip file**:

http://kodi.regulad.xyz/helix/repository.regulad.helix/repository.regulad.helix-1.0.0.zip

Then install Jellyfin Lite from Regulad Helix. HTTP/MD5 is a legacy compatibility
choice and does not authenticate client downloads.

`.github/settings.yml` declares `master` as the default branch for the Repository
Settings app. GitHub does not apply this file natively; the default branch is also
set directly during initial setup using `gh repo edit --default-branch master`.

## Local validation

Run `python -m unittest discover -s tests -v`. With separate checkouts of the
Helix branch and the pinned upstream tool, run:

```sh
python tools/build.py sources upstream/tools/create_repository.py dist
```

The output directory must be empty. The deployment command is
`python tools/deploy.py dist`, with the SSH secrets supplied via environment.
