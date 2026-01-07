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


# ----- ASU API config (moved to extras/api.py) -----
from extras.api import fetch_class_by_section


# ----- Routes -----
@app.route('/', methods=['GET'])
def index():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('index.html', result=result, query=section)

@app.route('/view', methods=['GET'])
def view():
    return render_template('view.html')

@app.route('/view2', methods=['GET'])
def view2():
    return render_template('view2.html')

@app.route('/view3', methods=['GET'])
def view3():
    return render_template('view3.html')


# ----- DB init for local dev -----
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)