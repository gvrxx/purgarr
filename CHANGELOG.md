# Changelog

Versions follow `MAJOR.MINOR.PATCH`:
- **PATCH** (1.0.1): bug fixes only.
- **MINOR** (1.1.0): new features that don't change how you set it up.
- **MAJOR** (2.0.0): changes that need you to redo part of the setup.

## 1.1.0 (2026-11-21)

- Updates come from GitHub: every release is built into `ghcr.io/gvrxx/purgarr`, and the Unraid Docker tab shows **update ready**. To switch, change the container's repository to `ghcr.io/gvrxx/purgarr:latest`.
- The Unraid template has an icon.

## 1.0.0 (2026-11-21)

First tracked version. It's what has been running on the Unraid server since September 2026.

- `/delete movie` and `/delete tv` suggest titles from the Radarr/Sonarr library while you type. Typing a title without picking one gives a private "Select a movie here" dropdown, or "doesn't currently exist on the server".
- Users see the poster privately and press **Request deletion**.
- The admin channel gets a review card with the poster, size, requesters and **Delete** / **Decline** buttons. Delete asks for confirmation first.
- Approved titles are deleted through Radarr/Sonarr, files included. Requesters are told in the channel where they asked.
- Several people asking for the same title join one request.
- `/mydeletions` shows a user's requests. `/pendingdeletions` re-posts pending cards for admins.
- Buttons keep working after a restart; requests are stored in SQLite.
- Dry-run mode, optional import exclusion, admin and requester roles.
- Runs as one Docker container on Unraid, with a user template.
