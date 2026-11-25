# Kodi repository publishing

`master` contains the publishing tools and GitHub repository settings.
`helix` has an independent root commit and contains unpacked add-on sources,
including Jellyfin Lite and `repository.regulad.helix`. Its small workflow calls
the reusable pipeline here. Both a push to either branch and a manual dispatch
publish Helix. A pull request to `master` runs the same pipeline up to the
artifact, packaging and validating Helix without publishing it, and its run
supplies no deployment secrets.

## Pipeline

The Ubuntu runner checks out Helix and Chad Parry's `create_repository.py` 2.3.8
at commit `27c38eddda89e626d05b029edb29fbc83bb27201`. The upstream code retains
its GPL license in that checkout; it is fetched during CI rather than copied here.
The builder exports only tracked add-on directories, checks dependencies, makes
stable ZIPs and MD5 sidecars, and creates an uncompressed catalog and download page.
Python 2.7 syntax and the Helix branch's browsing/playback regression tests run
in `python:2.7.18-slim`; runtime testing on the ATV is still necessary. The
complete static tree is also saved as a GHA artifact, except on a pull request.

The Tailscale action signs in with kodi-plugins' Tailscale federated identity:
it exchanges the run's GitHub OIDC token, which the `id-token: write` permission
lets it mint, for a key that joins the runner to the tailnet as `tag:ci-kodi`, so
no long-lived Tailscale secret is stored. The identity trusts only runs on
`master`; `regulad/homelab`'s `tofu/tailscale` defines it. A following
`tailscale set` explicitly enables subnet routes and MagicDNS without duplicating
the action's own `tailscale up` flags. The tailnet must permit SSH traffic from
`tag:ci-kodi` to the destination, including any subnet route.
Hostname resolution explicitly queries Tailscale's resolver at `100.100.100.100`;
short names are expanded with the tailnet's MagicDNS suffix. IP literals work too.

## Secrets

| Secret | Value |
| --- | --- |
| `TS_OAUTH_ID` | The Tailscale federated identity's client ID |
| `TS_AUDIENCE` | The identity's audience, `api.tailscale.com/<client ID>` |
| `SOPS_AGE_KEY` | The age identity that decrypts `publish.sops.yaml` |
| `SSH_KEY_HOST` | Hostname or IP address; SSH port 22 |
| `SSH_KEY_USER` | Remote SSH account |
| `STATIC_FILE_BASE` | Absolute remote web root, owned by the SSH user |

`regulad/homelab`'s `tofu/github` sets them. The SSH private key is not a
secret of its own: it is `ssh_key` in `publish.sops.yaml`, encrypted with SOPS
to Parker's age key and to this repository's own (see `.sops.yaml`), and the
workflow decrypts it into `SSH_KEY_BLOB` for the publish step alone.

`tools/deploy.py` reads the key from `SSH_KEY_BLOB`: the full `BEGIN/END OPENSSH
PRIVATE KEY` text, including its base64 body; a base64 encoding of the entire
text is also accepted. `SSH_KEY_PASSPHRASE` is optional, for an encrypted key;
the published key has none, as a passphrase kept beside it would add nothing.
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
