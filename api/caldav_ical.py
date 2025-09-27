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
    except Exception as exc:  # pragma: no cover - logged via platform
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
