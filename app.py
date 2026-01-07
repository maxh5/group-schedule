from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv


# ----- Load environment -----
load_dotenv()


# ----- App config -----
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///dev.db') # Default to SQLite for local dev
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# ----- DB -----
db = SQLAlchemy(app)


# ----- Models-----
import models


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

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == "POST":
        pass  # TODO: Implement registration logic
    else:
        return render_template('register.html')


# ----- Main -----
if __name__ == "__main__":
    with app.app_context(): # DB init for local dev
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)