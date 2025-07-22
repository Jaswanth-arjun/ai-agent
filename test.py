import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template_string, request, redirect, url_for, session
from apscheduler.schedulers.background import BackgroundScheduler
from flask_wtf.csrf import CSRFProtect, generate_csrf
from datetime import datetime, timedelta
from together import Together

# === CONFIGURATION ===
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_ADDRESS = "nellurujaswanth2004@gmail.com"
EMAIL_PASSWORD = "mcilyaehoztsvewa"
TOGETHER_API_KEY = "78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff"

# === Setup Together client ===
together = Together(api_key=TOGETHER_API_KEY)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.urandom(24)
csrf = CSRFProtect(app)
scheduler = BackgroundScheduler(timezone="UTC")

# === GLOBAL PROGRESS STORE ===
progress_store = {}

def increment_progress(email, course):
    key = (email, course)
    progress_store[key] = progress_store.get(key, 0) + 1

def get_progress(email, course):
    key = (email, course)
    return progress_store.get(key, 0)

def reset_progress(email, course):
    progress_store[(email, course)] = 0

# [Insert your FULL_TEMPLATE HTML here exactly as you had it before]

def generate_daily_content(course, part, days):
    if days == 1:
        prompt = f"""Create comprehensive content for '{course}' covering all essentials in one lesson."""
    else:
        prompt = f"""Create content for Lesson {part} of {days} for '{course}' focusing on one specific topic."""
    
    response = together.chat.completions.create(
        model="meta-llama/Llama-3-70b-chat-hf",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1500
    )
    return response.choices[0].message.content.strip()

def send_email(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        return False

def scheduled_job(email, course, day, days):
    content = generate_daily_content(course, day, days)
    if send_email(email, f"{course} - Day {day}", content):
        increment_progress(email, course)

def remove_existing_jobs(email, course):
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{email}_{course}_"):
            scheduler.remove_job(job.id)

def schedule_course(email, course, days, time_str):
    try:
        # Parse 12-hour time format
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        hour = time_obj.hour
        minute = time_obj.minute
        
        # Convert to 24-hour format
        if "PM" in time_str.upper() and hour != 12:
            hour += 12
        elif "AM" in time_str.upper() and hour == 12:
            hour = 0
            
        remove_existing_jobs(email, course)
        
        # Schedule emails
        for day in range(1, days + 1):
            run_time = datetime.now() + timedelta(days=day-1)
            run_time = run_time.replace(hour=hour, minute=minute, second=0)
            
            scheduler.add_job(
                scheduled_job,
                'date',
                run_date=run_time,
                args=[email, course, day, days],
                id=f"{email}_{course}_day{day}"
            )
        
        reset_progress(email, course)
        session['email'] = email
        session['course'] = course
        session['total_days'] = days
        session['scheduled_time'] = time_str
        
        return True
    except Exception as e:
        print(f"Scheduling error: {str(e)}")
        return False

@app.route('/')
def home():
    return render_template_string(FULL_TEMPLATE, template='course_selection', csrf_token=generate_csrf())

@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    if request.method == 'POST':
        email = request.form.get('email')
        course = request.form.get('course')
        days = int(request.form.get('days'))
        time = request.form.get('time')
        
        if not all([email, course, days, time]):
            return "Missing required fields", 400
            
        if schedule_course(email, course, days, time):
            return redirect(url_for('progress'))
        return "Scheduling failed", 400
        
    course = request.args.get('course')
    return render_template_string(FULL_TEMPLATE, template='user_form', course=course, csrf_token=generate_csrf())

@app.route('/progress')
def progress():
    email = session.get('email')
    course = session.get('course')
    days = session.get('total_days', 0)
    if not all([email, course, days]):
        return redirect(url_for('home'))
    
    completed = get_progress(email, course)
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=days,
        completed_days=completed,
        csrf_token=generate_csrf()
    )

if __name__ == '__main__':
    scheduler.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
