# Purgarr

A small Discord bot for deletion requests. Users ask for a movie or show to be deleted, you get a card with the poster and **Delete** / **Decline** buttons, and approved titles are deleted through Radarr or Sonarr, files included.

## How it works

1. A user types `/delete movie` (or `/delete tv`) and starts typing a title. Discord suggests matching titles from your Radarr/Sonarr library.
2. They pick one. If they typed a title instead of picking, they get a private **Select a movie here** dropdown of matches, or "doesn't currently exist on the server" if nothing matches.
3. They see the poster and press **Request deletion**. Everything up to here is private ("Only you can see this").
4. A review card appears in your private admin channel: poster, size on disk, who asked and when.
5. You press **Delete** (then **Yes, delete it**) or **Decline**.
6. The bot deletes the title in Radarr/Sonarr, updates the card and tells the requester in the channel where they asked.

If several people ask for the same title, they are added to one request instead of creating duplicates. Buttons keep working after the container restarts.

## Commands

| Command | Who | What |
|---|---|---|
| `/delete movie title:` | Everyone (or requester roles) | Request a movie deletion |
| `/delete tv title:` | Everyone (or requester roles) | Request deletion of a whole show |
| `/mydeletions` | Everyone | Your last 10 requests and their status |
| `/pendingdeletions` | Admins | Re-post cards for pending requests (oldest 10) in the admin channel |

Admins are members with **Administrator** or **Manage Server**, plus any roles in `ADMIN_ROLE_IDS`.

## Setup

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications>, click **New Application**, name it.
2. **Bot** page: click **Reset Token** and copy the token. No privileged intents are needed.
3. Invite it to your server. Replace `APP_ID` with the Application ID from the **General Information** page:
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=274877991936`
   That grants View Channels, Send Messages, Send Messages in Threads, Embed Links and Read Message History, and lets the bot add slash commands.
4. Create a private admin channel and make sure the bot can see it. The bot also needs to see and post in the channels where people use `/delete`, because that's where it announces the result.
5. In Discord, enable **Settings > Advanced > Developer Mode**. Right-click the admin channel and **Copy Channel ID**. Copy role IDs the same way under **Server Settings > Roles** if you want them.

### 2. Prepare Radarr and Sonarr

- Copy each API key from **Settings > General**.
- Strongly recommended: set a recycle bin in **Settings > Media Management** (show advanced settings, **Recycling Bin**). Without one, deletions are permanent. The bot warns you in the confirmation step when none is set.
- Optional: in **Settings > Connect**, turn on **On Movie Delete** / **On Series Delete** for your Plex connection so Plex refreshes. For a public announcement, turn it on for the Notifiarr connection too, then enable the **Deleted** trigger for Radarr/Sonarr on notifiarr.com.

### 3. Run it on Unraid

The image is built by GitHub for every release and published to `ghcr.io/gvrxx/purgarr`.

1. Copy `unraid/my-Purgarr.xml` to `/boot/config/plugins/dockerMan/templates-user/` (on the `flash` share: `config/plugins/dockerMan/templates-user`).
2. **Docker** tab, **Add Container**, pick **Purgarr** under the user templates. Fill in the token, admin channel ID, and Radarr/Sonarr URLs and keys. Use your server's IP, not `localhost`. Click **Apply**.
3. Check the container log. You should see `Connected to Radarr`, `Connected to Sonarr`, `Logged in as ...` and `Slash commands ready in ...`.

For a first test, set **Dry run** to `true`, request something and approve it. The card will say it's deleted but nothing is removed. Then set it back to `false`.

Prefer Docker Compose (for example with the Compose Manager plugin)? Copy `.env.example` to `.env`, fill it in, and run `docker compose up -d --build`.

### Updating

When a new version is released, the Docker tab shows **update ready** for Purgarr (after Unraid's update check, or **Check for Updates**). Click **apply update**. The database in `/mnt/user/appdata/purgarr` is kept.

## Settings

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | | Bot token |
| `ADMIN_CHANNEL_ID` | yes | | Channel for review cards; commands are registered in its server |
| `ADMIN_ROLE_IDS` | no | | Comma-separated role IDs that can Delete/Decline |
| `REQUESTER_ROLE_IDS` | no | everyone | Comma-separated role IDs that can request |
| `RADARR_URL`, `RADARR_API_KEY` | one of Radarr/Sonarr | | e.g. `http://192.168.1.50:7878` |
| `SONARR_URL`, `SONARR_API_KEY` | one of Radarr/Sonarr | | e.g. `http://192.168.1.50:8989` |
| `DRY_RUN` | no | `false` | `true` approves without deleting |
| `ADD_IMPORT_EXCLUSION` | no | `false` | `true` stops import lists from re-adding deleted titles |
| `DB_PATH` | no | `/config/purgarr.db` | SQLite database location |

## Good to know

- Deleting removes the title from Radarr/Sonarr **and deletes its files**. Shows are deleted as a whole, not per season.
- Users can still request a deleted title again through Requestrr.
- The API keys give full control of Radarr/Sonarr. Keep the bot on your LAN and don't share the template or `.env`.

## Versions

The current version is in `purgarr/__init__.py`, and the container log shows it on startup (`Purgarr 1.0.0 starting`). [CHANGELOG.md](CHANGELOG.md) lists what changed in each version, and every version is published on the GitHub **Releases** page.

To put a specific version on the server, Docker tab, Purgarr, **Edit**, change the repository to that version (for example `ghcr.io/gvrxx/purgarr:1.1.0`; versions before 1.1.0 aren't published) and **Apply**. Change it back to `:latest` to get updates again.

## Working on it from another computer

1. Install Git, the GitHub CLI and Python 3.13. On Windows:

   ```bash
   winget install Git.Git GitHub.cli Python.Python.3.13
   ```

2. Install Claude Code (the Claude desktop app, or the `claude` command line tool).
3. Log in to GitHub and download the project:

   ```bash
   gh auth login
   ```

   ```bash
   gh repo clone gvrxx/purgarr
   ```

4. Set up Python and check everything works:

   ```bash
   cd purgarr
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements-dev.txt
   python -m pytest tests
   ```

   On macOS/Linux, activate with `source .venv/bin/activate` instead.
5. Open the `purgarr` folder in Claude Code. It reads [CLAUDE.md](CLAUDE.md) automatically, which explains the project, the server setup and how to release a new version.

When you switch computers, push your changes first (`git push`), and run `git pull` on the other computer before you start.
