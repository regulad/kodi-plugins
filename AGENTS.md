# Commit requirements

Every commit created or rewritten by Codex in this repository must include:

Co-authored-by: Codex <codex@openai.com>

Keep Git commit signing enabled. Use the user's Bitwarden SSH agent and configured
signing key; request access outside the sandbox when needed. Never disable signing.

The `master` and `helix` branches intentionally have independent root histories.
Keep publishing tools on `master` and add-on sources on `helix`.
