"""iCloud Calendar helpers via CalDAV (read-only).

iCloud does not expose an OAuth flow for third-party calendar API access. The
standard approach is CalDAV at https://caldav.icloud.com/ authenticated with an
app-specific password the user generates at appleid.apple.com.

Public API mirrors the shape of extras/google_calendar.py so app.py can dispatch
on the UserOAuthToken.provider column.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import caldav
from caldav.lib.error import AuthorizationError, DAVError

from extras.google_calendar import (
    WORK_END_HOUR,
    WORK_START_HOUR,
    _clip_interval_to_day_local,
)


ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"


def make_client(apple_id: str, app_password: str) -> caldav.DAVClient:
    return caldav.DAVClient(
        url=ICLOUD_CALDAV_URL,
        username=apple_id,
        password=app_password,
        timeout=30,
    )


def validate_credentials(apple_id: str, app_password: str) -> Tuple[bool, Optional[str]]:
    """Try to fetch the principal. Returns (ok, error_message_or_None)."""
    try:
        client = make_client(apple_id, app_password)
        client.principal()
        return True, None
    except AuthorizationError:
        return False, "Invalid Apple ID or app-specific password."
    except DAVError as e:
        return False, f"iCloud rejected the connection: {e}"
    except Exception as e:
        return False, f"Could not reach iCloud: {e}"


def fetch_calendar_list(client: caldav.DAVClient) -> List[Dict[str, Any]]:
    """Return one dict per calendar discovered on the principal."""
    principal = client.principal()
    out: List[Dict[str, Any]] = []
    for cal in principal.calendars():
        try:
            name = cal.name or "(Untitled)"
        except Exception:
            name = "(Untitled)"
        out.append({
            "external_id": str(cal.url),
            "summary": name,
            "background_color": None,
        })
    return out


def events_list_for_calendar(
    client: caldav.DAVClient,
    calendar_url: str,
    time_min: dt.datetime,
    time_max: dt.datetime,
) -> List[Any]:
    """Search a single calendar for events in [time_min, time_max).

    Returns a list of caldav CalendarObjectResource items; each one carries an
    icalendar VEVENT component (split-expanded so each occurrence is its own
    item).
    """
    cal = client.calendar(url=calendar_url)
    return cal.search(
        start=time_min,
        end=time_max,
        event=True,
        expand=True,
    )


def _to_local_dt(value, local_tz) -> Optional[dt.datetime]:
    """Coerce icalendar DTSTART/DTEND value (date or datetime) into a local-tz datetime."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(local_tz)
    if isinstance(value, dt.date):
        # All-day: anchor at local midnight
        return dt.datetime.combine(value, dt.time.min, tzinfo=local_tz)
    return None


def icloud_events_to_week_slots(
    events: List[Any],
    week_start: dt.date,
    week_end: dt.date,
    local_tz,
) -> List[Dict[str, Any]]:
    """Convert caldav event resources to {day, start, end, title} dicts.

    week_end is exclusive. All-day events span the configured work window so
    they render as a full busy block, matching the Google helper.
    """
    out: List[Dict[str, Any]] = []
    days = (week_end - week_start).days
    if days <= 0:
        return out

    for ev in events:
        try:
            comp = ev.icalendar_component
        except Exception:
            continue
        if comp is None:
            continue

        summary_raw = comp.get("SUMMARY")
        title = (str(summary_raw).strip() if summary_raw else "") or "(No title)"

        dtstart_prop = comp.get("DTSTART")
        if dtstart_prop is None:
            continue
        start_val = dtstart_prop.dt

        dtend_prop = comp.get("DTEND")
        if dtend_prop is not None:
            end_val = dtend_prop.dt
        else:
            # Fall back to DURATION, else assume 1h for datetime / 1d for date
            duration_prop = comp.get("DURATION")
            if duration_prop is not None:
                end_val = start_val + duration_prop.dt
            elif isinstance(start_val, dt.datetime):
                end_val = start_val + dt.timedelta(hours=1)
            else:
                end_val = start_val + dt.timedelta(days=1)

        is_all_day = (
            not isinstance(start_val, dt.datetime)
            and isinstance(start_val, dt.date)
        )

        if is_all_day:
            cur = start_val
            # DTEND on all-day VEVENTs is exclusive (RFC 5545)
            end_date = end_val if isinstance(end_val, dt.date) and not isinstance(end_val, dt.datetime) else start_val + dt.timedelta(days=1)
            while cur < end_date:
                if week_start <= cur < week_end:
                    d0 = dt.datetime.combine(cur, dt.time(WORK_START_HOUR), tzinfo=local_tz)
                    d1 = dt.datetime.combine(cur, dt.time(WORK_END_HOUR), tzinfo=local_tz)
                    out.append({
                        "day": cur.weekday(),
                        "start": d0.strftime("%H:%M"),
                        "end": d1.strftime("%H:%M"),
                        "title": title,
                    })
                cur += dt.timedelta(days=1)
            continue

        s_loc = _to_local_dt(start_val, local_tz)
        e_loc = _to_local_dt(end_val, local_tz)
        if not s_loc or not e_loc:
            continue

        for i in range(days):
            d = week_start + dt.timedelta(days=i)
            clipped = _clip_interval_to_day_local(d, s_loc, e_loc)
            if not clipped:
                continue
            st, et = clipped
            out.append({
                "day": d.weekday(),
                "start": st.strftime("%H:%M"),
                "end": et.strftime("%H:%M"),
                "title": title,
            })
    return out


def week_bounds_utc(week_start: dt.date, week_end: dt.date) -> Tuple[dt.datetime, dt.datetime]:
    """Inclusive start, exclusive end in UTC datetimes for caldav search()."""
    t_min = dt.datetime.combine(week_start, dt.time.min, tzinfo=dt.timezone.utc)
    t_max = dt.datetime.combine(week_end, dt.time.min, tzinfo=dt.timezone.utc)
    return t_min, t_max
