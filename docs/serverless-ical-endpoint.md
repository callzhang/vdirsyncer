# Serving a CalDAV-Backed iCal Feed from Vercel

This note explains how to repurpose vdirsyncer's CalDAV pieces inside a Vercel serverless function so Google Calendar (or any iCal consumer) can import your schedule via `From URL`.

## 1. Understand the Constraints

- Vercel serverless functions are stateless and short-lived (10–60 seconds). Each invocation must fetch CalDAV items fresh.
- Any CalDAV credentials must be supplied via environment variables or Vercel's encrypted secrets.
- The filesystem is ephemeral; you cannot persist vdirsyncer's normal `status_path` or token files between runs.
- Network egress to your CalDAV server must be reachable from Vercel. If it sits behind a VPN or private LAN, this approach will not work.
- Large calendars may exceed the execution time budget. Keep the timerange narrow with `LOOKBACK_DAYS` / `LOOKAHEAD_DAYS`.
- If Deployment Protection is enabled, Google and other clients must be granted a bypass token (see the note in Section 5).

## 2. Handler Implementation (`api/caldav_ical.py`)

Create the serverless handler below. Vercel treats files under `api/` as serverless functions. For Python you export a `handler(request)` callable that returns `(status_code, headers, body)`.

```python
import asyncio
import os
from datetime import datetime, timedelta
from typing import Tuple

from aiohttp import TCPConnector

from vdirsyncer.cli.utils import storage_instance_from_config
from vdirsyncer.vobject import join_collection


def _config() -> Tuple[str, str, str, int, int]:
    url = os.environ.get("CALDAV_URL")
    if not url:
        raise RuntimeError("CALDAV_URL is not set")

    username = os.environ.get("CALDAV_USERNAME", "")
    password = os.environ.get("CALDAV_PASSWORD", "")
    lookback = int(os.environ.get("CALDAV_LOOKBACK_DAYS", "365"))
    lookahead = int(os.environ.get("CALDAV_LOOKAHEAD_DAYS", "365"))
    return url, username, password, lookback, lookahead


def _empty_calendar() -> str:
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//vdirsyncer//serverless//EN",
        "END:VCALENDAR",
        "",
    ])


async def _build_feed() -> str:
    url, username, password, lookback, lookahead = _config()

    connector = TCPConnector(limit=8)
    try:
        storage = await storage_instance_from_config(
            {
                "type": "caldav",
                "url": url,
                "username": username,
                "password": password,
                "item_types": ["VEVENT"],
                "start_date": "datetime.utcnow() - timedelta(days=%d)" % lookback,
                "end_date": "datetime.utcnow() + timedelta(days=%d)" % lookahead,
            },
            connector=connector,
        )

        hrefs: list[str] = []
        async for href, _etag in storage.list():
            hrefs.append(href)

        if not hrefs:
            return _empty_calendar()

        items: list[str] = []
        async for _href, item, _etag in storage.get_multi(hrefs):
            items.append(item.raw)

        return join_collection(items) if items else _empty_calendar()
    finally:
        connector.close()


def handler(request):
    try:
        ical_body = asyncio.run(_build_feed())
    except Exception as exc:  # surface failures for easier debugging/logging
        return (
            502,
            {"Content-Type": "text/plain; charset=utf-8"},
            f"Failed to build feed: {exc}".encode("utf-8"),
        )

    headers = {
        "Content-Type": "text/calendar; charset=utf-8",
        "Cache-Control": "s-maxage=300, stale-while-revalidate=600",
        "X-Generated-At": datetime.utcnow().isoformat() + "Z",
    }
    return (200, headers, ical_body.encode("utf-8"))
```

## 3. Declare Dependencies

Add a `requirements.txt` so Vercel installs vdirsyncer during the build:

```
vdirsyncer
```

Pin versions if you need reproducible builds.

## 4. Deploy via Vercel CLI

1. Authenticate once: `vercel login`.
2. Link the repo: `vercel link --yes` (creates `.vercel/`).
3. Provide CalDAV configuration secrets. Example using standard input:
   ```bash
   printf 'https://cal.example.com/dav\n' | vercel env add CALDAV_URL production
   printf 'https://cal.example.com/dav\n' | vercel env add CALDAV_URL preview
   printf 'username\n' | vercel env add CALDAV_USERNAME production
   printf 'username\n' | vercel env add CALDAV_USERNAME preview
   printf 'super-secret\n' | vercel env add CALDAV_PASSWORD production
   printf 'super-secret\n' | vercel env add CALDAV_PASSWORD preview
   ```
   Add the optional lookback/lookahead overrides the same way if desired.
4. Deploy: `vercel deploy --prod`.

After the first deploy, the CLI prints both the Inspect URL and the production hostname, e.g. `https://<project>.vercel.app`.

## 5. Verify and Share the Endpoint

- `curl https://<project>.vercel.app/api/caldav_ical` should return `HTTP/200` and a `text/calendar` payload once the CalDAV credentials are valid.
- Vercel’s Deployment Protection returns `401 Authentication Required` for unauthenticated requests. If you leave protection enabled, generate a bypass token (Vercel Dashboard → Project → Deployment Protection) and append it to the URL as documented: `...?x-vercel-set-bypass-cookie=true&x-vercel-protection-bypass=<token>` before sharing the feed with Google Calendar.
- In Google Calendar choose *Settings → Add calendar → From URL* and paste the protected or unprotected endpoint URL.

## 6. Operational Notes & Limitations

- Every invocation re-reads the CalDAV collection. Large calendars may exceed the function timeout; trim the timerange or run the sync on persistent infrastructure if that happens.
- The endpoint is read-only. Google or other subscribers cannot change the source CalDAV data through this feed.
- Use TLS pinning by adding `verify` / `verify_fingerprint` entries to the storage config and surfacing those paths via environment variables.
- Treat the Vercel env vars (especially `CALDAV_PASSWORD`) and any bypass tokens as secrets—rotate them if leaked.

Keep this file updated as you iterate on the deployment strategy.
