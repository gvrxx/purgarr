# Purgarr: notes for Claude Code

Discord bot that lets users of the owner's Discord server request deletion of movies/shows. An admin approves each request with a button, then the bot deletes the title through Radarr/Sonarr. See README.md for what users see and the full setup.

## Owner preferences

- Keep it simple. The owner explicitly asked for "not many fancy stuff": no extra features unless asked, mention possible add-ons instead of building them.
- The owner runs server commands in the **Unraid web UI terminal**, not over SSH. Give plain commands, one per code block.
- Explain steps one at a time when guiding setup or updates.

## Code layout

| File | What it does |
|---|---|
| `purgarr/__main__.py` | Entry point (`python -m purgarr`), logging, config errors |
| `purgarr/config.py` | Reads settings from environment variables |
| `purgarr/bot.py` | Discord client, permission checks, review cards, request lifecycle (submit, decline, confirm, delete, notify) |
| `purgarr/commands.py` | Slash commands: `/delete movie`, `/delete tv` (with library autocomplete), `/mydeletions`, `/pendingdeletions` |
| `purgarr/views.py` | Buttons and dropdowns. `AdminButton` is a `DynamicItem`, so buttons survive restarts |
| `purgarr/arr.py` | Small Radarr/Sonarr v3 API client and title search |
| `purgarr/db.py` | SQLite storage. Status flow: pending -> deleting -> deleted/gone, or pending -> declined |
| `purgarr/embeds.py` | Embeds for previews, review cards and `/mydeletions` |
| `unraid/my-Purgarr.xml` | Unraid container template |

Python 3.13, discord.py 2.7.1, no other runtime dependencies (aiohttp comes with discord.py).

## Working on it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests
```

All tests must pass before committing. Tests fake Discord and Radarr/Sonarr, so no server or token is needed.

Things that were deliberately decided:
- Slash commands only, so the bot needs no Message Content intent. Commands are synced to the admin channel's server in `on_ready`.
- `/delete` refuses to run in channels where the bot can't post, because the result is announced there.
- Shows are deleted whole (no per-season deletes). Import exclusion is off by default.
- Never auto-approve; every delete needs an admin click plus a confirmation.

## The live deployment

- Server-specific details (addresses, history) are in `CLAUDE.local.md`, which is not committed because the repo is public.
- The database is `/mnt/user/appdata/purgarr/purgarr.db`: never overwrite or delete it.
- Container `Purgarr`, created from the user template, runs `ghcr.io/gvrxx/purgarr:latest`. GitHub Actions (`.github/workflows/docker.yml`) builds and publishes that image when a `v*` tag is pushed. The repo and the image are public, so the server needs no GitHub login.
- If the bot goes offline, check the container log first. A DNS error for discord.com means the container can't look up addresses; adding `--dns=1.1.1.1` to the container's Extra Parameters fixes that. `Could not reach Radarr/Sonarr` means the Radarr/Sonarr URLs in the container settings are wrong, for example after the server's IP changed.

### Updating the server

1. Release a version (below). Wait for the **Docker image** workflow to finish: `gh run list --workflow docker.yml`. If no run started for the tag (this happened for v1.1.0), start it by hand: `gh workflow run docker.yml --ref v1.1.0`.
2. Unraid Docker tab: **Check for Updates**, then **apply update** on Purgarr.
3. Check the log for `Purgarr <version> starting`, `Logged in as ...` and `Slash commands ready in ...`.

### The Docker tab icon

The template's Icon URL points at `unraid/purgarr.png` on GitHub (drawn from `unraid/purgarr.svg`). Unraid caches it; if it shows a question mark, delete the cached copies and refresh the Docker tab:

```bash
rm -f /var/lib/docker/unraid/images/Purgarr-icon.png /usr/local/emhttp/state/plugins/dynamix.docker.manager/images/Purgarr-icon.png
```

## Releasing a version

Version numbers follow CHANGELOG.md (MAJOR.MINOR.PATCH).

1. Bump `__version__` in `purgarr/__init__.py`.
2. Add an entry at the top of CHANGELOG.md.
3. Run the tests.
4. Commit, then tag and publish. Pushing the tag starts the Docker image build:

```bash
git tag v1.0.1
git push origin main --tags
gh release create v1.0.1 --title "v1.0.1" --notes "<changelog entry>"
```

## Secrets

The repo is public. The Discord token and Radarr/Sonarr API keys live only in the Unraid container settings (or a local `.env`). Never commit them; `.gitignore` excludes `.env` and `*.db`.
