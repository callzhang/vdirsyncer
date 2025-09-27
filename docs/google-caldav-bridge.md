# Bridging a CalDAV Server to Google Calendar with vdirsyncer

This guide condenses the relevant parts of the upstream documentation for keeping a CalDAV calendar and Google Calendar in sync with vdirsyncer. It also shows how to keep a local `.ics` file updated alongside Google so you always have an offline copy of your schedule.

> Tip: The upstream docs live in `docs/*.rst` inside this repository; search there when you need more depth.

## 1. Install vdirsyncer and the Google extras

- Use your package manager when possible (`brew install vdirsyncer`, distro packages listed in `docs/installation.rst`).
- If you install with pip or pipx, make sure Python 3.9–3.13, libxml, libxslt, and zlib are present first.
- Google backends are optional extras. Install them with:

```bash
pip install 'vdirsyncer[google]'
```

## 2. Collect Google API credentials once

Google requires you to bring your own OAuth keys (see `docs/config.rst` > Google):

1. Visit https://console.developers.google.com and create a project.
2. Enable the **CalDAV** and **CardDAV** APIs for that project (do *not* enable the Calendar/Contacts REST APIs).
3. Create an OAuth consent screen, then a **Desktop application** OAuth client.
4. Download the Client ID and Client Secret; you will reference them in the config.
5. Decide on a writable `token_file` path (vdirsyncer will create/populate it during the first auth run).
6. Optional but useful: visit https://calendar.google.com/calendar/syncselect and tick the calendars you want Google to expose to CalDAV clients.

Caveats from upstream docs:

- Google rejects `VTODO` items on calendars, so only `VEVENT` is safe.
- Google contacts syncing works but has data-quality caveats; this guide focuses on calendars.

## 3. Prepare CalDAV connection details

From `docs/config.rst` > CalDAV and `docs/tutorial.rst`:

- Gather base collection URL, username, password, and any custom CA certificate or fingerprint your server requires.
- Decide whether you want everything, or limit the sync window via `start_date` / `end_date` (Python expressions returning datetimes).
- Decide on item filtering (`item_types = ["VEVENT"]` keeps events only).
- If your server cannot list collections, you can point the storage URL straight at a single calendar and set `collections = null` in the pair (Google storages do not support the null mode, so keep that on the CalDAV side only).

## 4. Create the vdirsyncer config file

The default location is `~/.config/vdirsyncer/config` (see `docs/tutorial.rst`). Minimal skeleton:

```toml
[general]
status_path = "~/.vdirsyncer/status/"

[pair server_to_google]
a = "remote_caldav"
b = "google_calendar"
collections = ["from a", "from b"]
metadata = ["displayname", "color"]  # optional, keeps Google names/colors aligned
conflict_resolution = "a wins"  # pick your strategy; see docs/tutorial.rst

[storage remote_caldav]
type = "caldav"
url = "https://cal.example.com/dav/calendars/user/"
username = "alice"
password.fetch = ["prompt", "CalDAV password"]  # or command/keyring per docs/keyring.rst
start_date = "datetime.now() - timedelta(days=365)"
end_date = "datetime.now() + timedelta(days=365)"
item_types = ["VEVENT"]
verify = "/usr/local/share/ca-certificates/my_ca.pem"  # optional

[storage google_calendar]
type = "google_calendar"
token_file = "~/.vdirsyncer/google-token.json"
client_id = "your-client-id.apps.googleusercontent.com"
client_secret = "your-client-secret"
item_types = ["VEVENT"]
```

Notes:

- Replace credential placeholders with real values. Storing secrets via `password.fetch` keeps them out of the file (see `docs/keyring.rst`).
- `metadata` sync requires `metadata = [...]` on the pair and `vdirsyncer metasync` after discovery.
- If you want a read-only mirror on either side, add `read_only = true` in that storage section.

## 5. Keep a local `.ics` export in parallel (optional)

Add a second storage using the filesystem backend (see `docs/config.rst` > Local) so Google and a local `.ics` file reflect the same CalDAV data:

```toml
[storage local_ics]
type = "filesystem"
path = "~/Calendars/work"
fileext = ".ics"

[pair caldav_to_ics]
a = "remote_caldav"
b = "local_ics"
collections = ["from a"]
```

Run `vdirsyncer sync caldav_to_ics` whenever you want to refresh the local copy. If you truly need a single-file export, swap the storage for `type = "singlefile"` with a `path = "~/Calendars/work.ics"`, but expect slower syncs (see `docs/config.rst` > singlefile).

## 6. First-time workflow

1. `vdirsyncer discover server_to_google` – enumerates collections on both sides and creates per-collection metadata in `status_path`.
2. `vdirsyncer discover caldav_to_ics` (if you added the local mirror).
3. `vdirsyncer sync server_to_google` – triggers OAuth the first time; follow the URL, paste the token.
4. `vdirsyncer sync caldav_to_ics` – populate the `.ics` mirror.
5. Optionally `vdirsyncer metasync server_to_google` to pull colors/names.

Subsequent runs only need `vdirsyncer sync` (and `metasync` if you care about metadata changes).

## 7. Conflict handling and filters

- Without configuration, simultaneous edits raise a conflict error. Resolve by choosing `"a wins"`, `"b wins"`, or a merge command array, e.g. `conflict_resolution = ["command", "vimdiff"]` (`docs/tutorial.rst`).
- Fine-tune which calendars sync using custom `collections` entries, e.g. `collections = [["work", "remote-id", "google-id"]]` when the CalDAV and Google identifiers differ (server-to-server guidance in `docs/tutorial.rst`).
- To skip or include specific event types use `item_types`. Google does not support `VTODO`, so avoid syncing tasks into Google calendars.

## 8. Hardening and automation

- Use `password.fetch` sources (`command`, `shell`, `prompt`, `keyring`, environment) for both CalDAV and Google secrets (`docs/keyring.rst`).
- Protect the `token_file`; treat it like a password. Revoke it by deleting the file.
- For certificate pinning, either point `verify` at your CA bundle or use `verify_fingerprint` (see `docs/ssl-tutorial.rst`).
- Automate with cron/systemd timers. Example cron entry:

```
*/15 * * * * /usr/local/bin/vdirsyncer sync server_to_google >/tmp/vdirsyncer.log 2>&1
```

- Back up `status_path` and token files if you run scheduled syncs on headless machines.

## 9. Troubleshooting reference points

- Run with `vdirsyncer --verbosity INFO sync` or `-v DEBUG` for more diagnostics (`docs/problems.rst`).
- If Google rejects a collection, check the Sync Settings page mentioned above. Also ensure the calendar is not read-only on the Google side.
- When discovery fails for CalDAV, point the storage URL directly at a collection and set `collections = null` so vdirsyncer skips collection listing.
- Stuck metadata? Re-run `vdirsyncer metasync` or remove the appropriate folder under `status_path` and rediscover.
- Clean reauth: delete the `token_file` and rerun sync; Google will prompt again.

## 10. Useful CLI reminders

- `vdirsyncer discover` – refresh available collections.
- `vdirsyncer sync` – transfer items.
- `vdirsyncer metasync` – sync metadata listed in `metadata = [...]`.
- `vdirsyncer repair <pair>/<collection>` – attempt to fix invalid items (`docs/problems.rst`).

Keep this file alongside your config so the essential steps stay local even when offline.
