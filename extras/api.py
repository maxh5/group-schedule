import os
import re
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

def clean_daylist(daylist):
    """Remove HTML tags/entities from DAYLIST field and format as 'M T Th'."""
    if not daylist:
        return ""
    # Remove HTML tags and entities
    cleaned = re.sub(r'<[^>]+>', '', daylist)  # Remove tags like <br/>
    cleaned = re.sub(r'&[a-z]+;', '', cleaned)  # Remove entities like &nbsp;
    cleaned = cleaned.strip()
    
    # Parse day abbreviations: M, T, W, Th, F
    # Use regex to find day patterns (Th is two chars, others are one)
    days = re.findall(r'Th|[MTWF]', cleaned)
    return ' '.join(days)

def parse_class_item(item):
    """Parse a raw ASU class API item into a normalized dict."""
    clas = item.get("CLAS", {}) # Most things are stored inside this sub-dict
    section_data = {
        "asu_course_id": item.get("SUBJECTNUMBER", ""),
        "title": clas.get("COURSETITLELONG", ""),
        "asu_section_id": clas.get("CLASSNBR", ""),
        "term": clas.get("STRM", ""),
        "location": clas.get("DESCR1", ""),
        "days_of_week": clean_daylist(clas.get("DAYLIST", "")),
        "start_time": parse_time(clas.get("STARTTIME")),
        "end_time": parse_time(clas.get("ENDTIME")),
        "start_date": datetime.fromisoformat(clas.get("STARTDATE", "")).date(),
        "end_date": datetime.fromisoformat(clas.get("ENDDATE", "")).date()
    }
    return section_data

def parse_time(value):
    if not value: return None
    # Clean HTML tags and entities (similar to clean_daylist)
    cleaned = re.sub(r'<[^>]+>', '', value)
    cleaned = re.sub(r'&[a-z]+;', '', cleaned)
    cleaned = cleaned.strip()
    try: return datetime.strptime(cleaned, "%I:%M %p").time()
    except ValueError: return None