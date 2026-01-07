import os
import requests

"""ASU Catalog API helper utilities."""

BASE_API_URL = (
    "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
)
HEADERS = {"Authorization": "Bearer null"}
TERM_NUMBER = os.environ.get("TERM_NUMBER", "2261")


def fetch_class_by_section(section_id):
    """Return the first matching class info dict, or None if not found."""
    params = {
        "term": TERM_NUMBER,
        "classNbr": section_id,
        "searchType": "all",
        "refine": "Y",
        "campusOrOnlineSelection": "A",
    }
    try:
        r = requests.get(BASE_API_URL, headers=HEADERS, params=params, timeout=10)
        data = r.json()
    except Exception as e:
        return {"error": f"Error fetching from API: {e}"}

    for item in data.get("classes", []):
        clas = item.get("CLAS", {})
        if str(clas.get("CLASSNBR", "")) == str(section_id):
            seat = item.get("seatInfo", {})
            return {
                "class_number": clas.get("CLASSNBR"),
                "subject": clas.get("SUBJECT", ""),
                "catalog_nbr": clas.get("CATALOGNBR", ""),
                "title": clas.get("TITLE", ""),
                "instructors": ", ".join(clas.get("INSTRUCTORSLIST", []) or []),
                "location": clas.get("LOCATION", ""),
                "meeting_times": clas.get("MEETINGPATTERN", ""),
                "enrolled": clas.get("ENRLTOT", "") or seat.get("ENRL_TOT", ""),
                "capacity": clas.get("ENRLCAP", "") or seat.get("ENRL_CAP", ""),
                "raw": item,
            }
    return None
