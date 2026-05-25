"""Google Calendar API helpers (read-only). Uses refresh token from User."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import requests

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDAR_META_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}"
EVENTS_LIST_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"

# Match static/js/calendar.js work window for clipping
WORK_START_HOUR = 8
WORK_END_HOUR = 20


def refresh_google_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise ValueError("Google token response missing access_token")
    return token


def fetch_calendar_metadata(access_token: str, calendar_id: str) -> Optional[Dict[str, Any]]:
    """GET calendars/{id}. Returns None if not found (404)."""
    from urllib.parse import quote

    cid = quote(calendar_id, safe="@")
    url = CALENDAR_META_URL.format(calendar_id=cid)
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fetch_calendar_list(access_token: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        params: Dict[str, str] = {"maxResults": "250"}
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(
            CALENDAR_LIST_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def _parse_google_datetime(val: Dict[str, str]) -> Optional[dt.datetime]:
    if "dateTime" in val:
        s = val["dateTime"]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    return None


def _parse_google_date(val: Dict[str, str]) -> Optional[dt.date]:
    if "date" in val:
        return dt.date.fromisoformat(val["date"])
    return None


def _clip_interval_to_day_local(
    day: dt.date,
    start_local: dt.datetime,
    end_local: dt.datetime,
) -> Optional[Tuple[dt.time, dt.time]]:
    """Return (start_time, end_time) local wall times clipped to [day 00:00, day+1 00:00) and work hours."""
    day_start = dt.datetime.combine(day, dt.time.min, tzinfo=start_local.tzinfo)
    day_end = day_start + dt.timedelta(days=1)
    lo = max(start_local, day_start)
    hi = min(end_local, day_end)
    if hi <= lo:
        return None
    work_lo = day_start.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
    work_hi = day_start.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
    lo2 = max(lo, work_lo)
    hi2 = min(hi, work_hi)
    if hi2 <= lo2:
        return None
    return lo2.time().replace(tzinfo=None), hi2.time().replace(tzinfo=None)


def events_list_for_calendar(
    access_token: str,
    calendar_id: str,
    time_min_rfc3339: str,
    time_max_rfc3339: str,
) -> List[Dict[str, Any]]:
    from urllib.parse import quote

    cid = quote(calendar_id, safe="")
    url = EVENTS_LIST_URL.format(calendar_id=cid)
    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        params: Dict[str, str] = {
            "timeMin": time_min_rfc3339,
            "timeMax": time_max_rfc3339,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "250",
        }
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=45,
        )
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


def google_events_to_week_slots(
    raw_events: List[Dict[str, Any]],
    week_start: dt.date,
    week_end: dt.date,
    local_tz,
) -> List[Dict[str, Any]]:
    """
    week_end is exclusive (first day after the week), matching app week logic.
    Output dicts: day (0 Mon .. 6 Sun), start/end HH:MM, title (str).
    """
    out: List[Dict[str, Any]] = []
    days = (week_end - week_start).days
    if days <= 0:
        return out

    for ev in raw_events:
        if ev.get("status") == "cancelled":
            continue
        summary = (ev.get("summary") or "(No title)").strip() or "(No title)"
        start_obj = ev.get("start") or {}
        end_obj = ev.get("end") or {}

        start_dt = _parse_google_datetime(start_obj)
        end_dt = _parse_google_datetime(end_obj)
        start_d = _parse_google_date(start_obj)
        end_d = _parse_google_date(end_obj)

        if start_dt and end_dt:
            s_loc = start_dt.astimezone(local_tz)
            e_loc = end_dt.astimezone(local_tz)
            for i in range(days):
                d = week_start + dt.timedelta(days=i)
                clipped = _clip_interval_to_day_local(d, s_loc, e_loc)
                if not clipped:
                    continue
                st, et = clipped
                out.append(
                    {
                        "day": d.weekday(),
                        "start": st.strftime("%H:%M"),
                        "end": et.strftime("%H:%M"),
                        "title": summary,
                    }
                )
        elif start_d and end_d:
            # All-day: Google end.date is exclusive
            cur = start_d
            while cur < end_d:
                if week_start <= cur < week_end:
                    d0 = dt.datetime.combine(cur, dt.time(WORK_START_HOUR), tzinfo=local_tz)
                    d1 = dt.datetime.combine(cur, dt.time(WORK_END_HOUR), tzinfo=local_tz)
                    out.append(
                        {
                            "day": cur.weekday(),
                            "start": d0.strftime("%H:%M"),
                            "end": d1.strftime("%H:%M"),
                            "title": summary,
                        }
                    )
                cur += dt.timedelta(days=1)
    return out


def week_bounds_rfc3339_utc(week_start: dt.date, week_end: dt.date) -> Tuple[str, str]:
    """Inclusive timeMin, exclusive timeMax in UTC for Google API."""
    t_min = dt.datetime.combine(week_start, dt.time.min, tzinfo=dt.timezone.utc)
    t_max = dt.datetime.combine(week_end, dt.time.min, tzinfo=dt.timezone.utc)
    return t_min.isoformat().replace("+00:00", "Z"), t_max.isoformat().replace("+00:00", "Z")
