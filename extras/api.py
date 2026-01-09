import os
import requests
from datetime import datetime

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
        data = r.json() # Full API response in JSON format
    except Exception as e:
        return {"error": f"Error fetching from API: {e}"}

    for item in data.get("classes", []): # All classes
        clas = item.get("CLAS", {})
        if str(clas.get("CLASSNBR", "")) == str(section_id): # Searched section
            return item
    return None

def parse_class_item(item):
    """Parse a raw ASU class API item into a normalized dict."""
    clas = item.get("CLAS", {}) # Most things are stored inside this sub-dict
    section_data = {
        "asu_course_id": item.get("SUBJECTNUMBER", ""),
        "title": clas.get("COURSETITLELONG", ""),
        "asu_section_id": clas.get("CLASSNBR", ""),
        "term": clas.get("STRM", ""),
        "location": clas.get("DESCR1", ""),
        "days_of_week": clas.get("DAYLIST", ""),
        "start_time": datetime.strptime(clas.get("STARTTIME", ""), "%I:%M %p").time(),
        "end_time": datetime.strptime(clas.get("ENDTIME", ""), "%I:%M %p").time(),
        "start_date": datetime.fromisoformat(clas.get("STARTDATE", "")).date(),
        "end_date": datetime.fromisoformat(clas.get("ENDDATE", "")).date()
    }
    return section_data