from flask import Flask, render_template, request, redirect, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from groq import Groq
import os, sqlite3, json
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import whisper
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
import json

@app.template_filter('from_json')
def from_json(value):
    try:
        return json.loads(value)
    except:
        return []

whisper_model = whisper.load_model("tiny")
from dotenv import load_dotenv
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def init_db():
    conn = sqlite3.connect("meetings.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        transcript TEXT,
        summary TEXT,
        key_points TEXT,
        action_items TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        conn = sqlite3.connect("meetings.db")
        cursor = conn.cursor()

        try:
            cursor.execute("INSERT INTO users (username, password) VALUES (?,?)", (u,p))
            conn.commit()
            conn.close()
            return redirect("/login")
        except:
            conn.close()
            return "User already exists"

    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        conn = sqlite3.connect("meetings.db")
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (u,p))
        user = cursor.fetchone()
        conn.close()

        if user:
            login_user(User(u))
            return redirect("/")

        return "Invalid credentials"

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

@app.route("/", methods=["GET","POST"])
@login_required
def home():
    result = None

    if request.method == "POST":

        text = ""

        if "meeting_text" in request.form and request.form["meeting_text"].strip():
            text = request.form["meeting_text"].strip()

        elif "audio_file" in request.files:
            file = request.files["audio_file"]

            if file and file.filename != "":
                os.makedirs("uploads", exist_ok=True)
                filepath = os.path.join("uploads", file.filename)
                file.save(filepath)

                try:
                    transcript = whisper_model.transcribe(filepath)
                    text = transcript["text"].strip()
                except Exception as e:
                    result = {"summary":"Audio Error","key_points":[],"action_items":[str(e)]}

        if text:
            prompt = f"""
Analyze the meeting transcript below and return a clean response.

Rules:
- Do NOT use **, numbers, or extra formatting
- Keep sentences simple and clear
- Use short bullet points only

Format strictly like this:

Summary:
<short paragraph>

Key Points:
- point
- point

Action Items:
- task
- task

Transcript:
{text}
"""
            try:
                chat = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role":"user","content":prompt}]
                )

                ai_text = chat.choices[0].message.content

                summary = ai_text.split("Summary:")[-1].split("Key Points:")[0].strip()
                key_points = ai_text.split("Key Points:")[-1].split("Action Items:")[0].strip().split("\n")
                action_items = ai_text.split("Action Items:")[-1].strip().split("\n")

                data = {
                    "summary": summary,
                    "key_points": [k for k in key_points if k.strip()],
                    "action_items": [a for a in action_items if a.strip()]
                }

                conn = sqlite3.connect("meetings.db")
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO meetings (user, transcript, summary, key_points, action_items,created_at)
                VALUES (?,?,?,?,?,?)
                """,(current_user.id, text, data["summary"],
                     json.dumps(data["key_points"]),
                     json.dumps(data["action_items"]),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()

                result = data

            except Exception as e:
                result = {"summary":"AI Error","key_points":[],"action_items":[str(e)]}

    return render_template("index.html", result=result)

@app.route("/history")
@login_required
def history():
    conn = sqlite3.connect("meetings.db")
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM meetings WHERE user=?", (current_user.id,))
    rows = cursor.fetchall()

    conn.close()
    return render_template("history.html", rows=rows)

@app.route("/download/<int:id>")
@login_required
def download(id):
    conn = sqlite3.connect("meetings.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM meetings WHERE id=?", (id,))
    row = cursor.fetchone()
    conn.close()

    file = f"meeting_{id}.pdf"

    doc = SimpleDocTemplate(file)
    styles = getSampleStyleSheet()

    content = [
        Paragraph("Summary: " + row[3], styles["Normal"]),
        Paragraph("Key Points: " + row[4], styles["Normal"]),
        Paragraph("Action Items: " + row[5], styles["Normal"])
    ]

    doc.build(content)
    return send_file(file, as_attachment=True)

@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = sqlite3.connect("meetings.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM meetings WHERE id=?", (id,))
    conn.commit()
    conn.close()

    return redirect("/history")

import os

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))