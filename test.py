import os
import re
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from flask_wtf.csrf import CSRFProtect, generate_csrf
from datetime import datetime, timedelta
import pytz
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
progress_store = {}  # key: (email, course), value: int (completed days)

def increment_progress(email, course):
    key = (email, course)
    progress_store[key] = progress_store.get(key, 0) + 1

def get_progress(email, course):
    key = (email, course)
    return progress_store.get(key, 0)

def reset_progress(email, course):
    progress_store[(email, course)] = 0

# === HTML Template (same as before) ===
FULL_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="en">
[... rest of your HTML template remains exactly the same ...]
</html>
'''

def generate_daily_content(course, part, days):
    if days == 1:
        prompt = f"""
You are an expert course creator. The topic is: '{course}'. The learner wants to complete this course in **1 day**, so provide the **entire course content in a single comprehensive lesson**.

Include all of the following in your response:

1. 📘 **Course Title**
2. 🧠 **Complete Explanation with Real-World Examples**
3. ✍️ **Practical Exercises**
4. 📌 **Key Takeaways**
5. 🔗 **Curated Resource Links**
6. 📝 **Format Everything in Markdown**
"""
    else:
        prompt = f"""
You are an expert course creator. The topic is: '{course}'. The learner wants to complete this course in {days} days. Generate **Lesson {part} of {days}**.

Include in Lesson {part}:
1. 📘 **Lesson Title**
2. 🧠 **Focused Explanation with Real Examples**
3. ✍️ **2–3 Practical Exercises**
4. 📌 **3–5 Key Takeaways**
5. 🔗 **2–3 Curated Resource Links**
6. 📝 **Markdown Formatting**
"""

    response = together.chat.completions.create(
        model="meta-llama/Llama-3-70b-chat-hf",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1500
    )
    return response.choices[0].message.content.strip()

def send_email(to_email, subject, body):
    try:
        if not to_email or "@" not in to_email:
            print(f"Invalid email address: {to_email}")
            return False
            
        msg = MIMEMultipart()
        msg["From"] = f"LearnHub <{EMAIL_ADDRESS}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .content {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            </style>
        </head>
        <body>
            <div class="content">
                {body.replace('\n', '<br>')}
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
            
        print(f"Email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

def scheduled_job(email, course, part, days):
    try:
        print(f"Sending Day {part} email for {course} to {email}")
        content = generate_daily_content(course, part, days)
        if send_email(email, f"{course} - Day {part}", content):
            increment_progress(email, course)
    except Exception as e:
        print(f"Failed to send day {part} email: {str(e)}")

def remove_existing_jobs(email, course):
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{email}_{course}_"):
            scheduler.remove_job(job.id)

def schedule_course(email, course, days, time_str):
    try:
        # Parse the time string (12-hour format with AM/PM)
        try:
            time_obj = datetime.strptime(time_str, "%I:%M %p")
        except ValueError:
            raise ValueError("Invalid time format. Please use format like '9:00 AM' or '2:30 PM'")
        
        hour = time_obj.hour
        minute = time_obj.minute
        ampm = time_str.split()[-1].upper()
        
        # Convert to 24-hour format if needed
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
            
        remove_existing_jobs(email, course)
        
        # Get current time in UTC
        now = datetime.now(pytz.utc)
        
        # Send welcome email
        welcome_content = f"Welcome to {course}! Your daily lessons will arrive at {time_str}."
        if not send_email(email, f"Welcome to {course}!", welcome_content):
            raise Exception("Failed to send welcome email")
            
        # Schedule daily emails
        for day in range(1, days + 1):
            scheduled_time = now + timedelta(days=day-1)
            scheduled_time = scheduled_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            job_id = f"{email}_{course}_day{day}"
            scheduler.add_job(
                scheduled_job,
                'date',
                run_date=scheduled_time,
                args=[email, course, day, days],
                id=job_id,
                replace_existing=True
            )
            print(f"Scheduled Day {day} for {email} at {scheduled_time} UTC")
            
        reset_progress(email, course)
        session['email'] = email
        session['course'] = course
        session['total_days'] = int(days)
        session['scheduled_time'] = time_str
        
        return True
        
    except Exception as e:
        print(f"Failed to schedule course: {str(e)}")
        return False

@app.route('/', methods=['GET', 'POST'])
def select_course():
    if request.method == "POST":
        return redirect(url_for("schedule_form", course=request.form["course"]))
    return render_template_string(
        FULL_TEMPLATE,
        template='course_selection',
        csrf_token=generate_csrf()
    )

@app.route("/schedule", methods=["GET", "POST"])
def schedule_form():
    course = request.args.get("course") or request.form.get("course")
    if not course:
        return redirect(url_for("select_course"))
        
    if request.method == "POST":
        try:
            email = request.form.get("email", "").strip()
            days = request.form.get("days", "").strip()
            time = request.form.get("time", "").strip()
            
            if not all([email, days, time]):
                raise ValueError("All fields are required")
                
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                raise ValueError("Please enter a valid email address")
                
            if not days.isdigit() or int(days) <= 0 or int(days) > 365:
                raise ValueError("Please enter a valid number of days (1-365)")
                
            if not schedule_course(email, course, int(days), time):
                raise ValueError("Failed to schedule course. Please try again.")
                
            return redirect(url_for('progress'))
            
        except ValueError as e:
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=str(e),
                csrf_token=generate_csrf()
            )
            
    return render_template_string(
        FULL_TEMPLATE,
        template='user_form',
        course=course,
        csrf_token=generate_csrf()
    )

@app.route("/progress")
def progress():
    email = session.get('email')
    course = session.get('course')
    total_days = session.get('total_days', 0)
    
    if not email or not course or not total_days:
        return redirect(url_for('select_course'))
        
    completed_days = get_progress(email, course)
    scheduled_time = session.get('scheduled_time', 'your scheduled time')
    
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=total_days,
        completed_days=completed_days,
        scheduled_time=scheduled_time,
        csrf_token=generate_csrf()
    )

if __name__ == "__main__":
    scheduler.start()
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=False)
