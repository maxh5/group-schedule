from datetime import datetime, timedelta
from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv
import requests


# ----- .env -----
load_dotenv()


# ----- App config -----
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
# Use DATABASE_URL if provided, otherwise fall back to a local SQLite file
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# ----- DB -----
db = SQLAlchemy(app)


# ----- Models-----
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), nullable=False)


# ----- ASU API config -----
BASE_API_URL = 'https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes'
HEADERS = {'Authorization': 'Bearer null'}
TERM_NUMBER = os.environ.get('TERM_NUMBER', '2261')

def fetch_class_by_section(section_id):
    """Return the first matching class info dict, or None if not found."""
    params = {
        'term': TERM_NUMBER,
        'classNbr': section_id,
        'searchType': 'all',
        'refine': 'Y',
        'campusOrOnlineSelection': 'A'
    }
    try:
        r = requests.get(BASE_API_URL, headers=HEADERS, params=params, timeout=10)
        data = r.json()
    except Exception as e:
        return {'error': f'Error fetching from API: {e}'}

    for item in data.get('classes', []):
        clas = item.get('CLAS', {})
        if str(clas.get('CLASSNBR', '')) == str(section_id):
            seat = item.get('seatInfo', {})
            return {
                'class_number': clas.get('CLASSNBR'),
                'subject': clas.get('SUBJECT', ''),
                'catalog_nbr': clas.get('CATALOGNBR', ''),
                'title': clas.get('TITLE', ''),
                'instructors': ', '.join(clas.get('INSTRUCTORSLIST', []) or []),
                'location': clas.get('LOCATION', ''),
                'meeting_times': clas.get('MEETINGPATTERN', ''),
                'enrolled': clas.get('ENRLTOT', '') or seat.get('ENRL_TOT', ''),
                'capacity': clas.get('ENRLCAP', '') or seat.get('ENRL_CAP', ''),
                'raw': item
            }
    return None


# ----- Routes -----
@app.route('/', methods=['GET'])
def index():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('index.html', result=result, query=section)


# ----- DB init for local dev -----
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)