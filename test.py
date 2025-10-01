import os
import re
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from together import Together
from flask_wtf.csrf import CSRFProtect, generate_csrf
from datetime import datetime, timedelta
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

# === CONFIGURATION ===
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_ADDRESS = os.environ.get('EMAIL_ADDRESS', 'nellurujaswanth2004@gmail.com')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD', 'xwmcygkwtdalhavi')
TOGETHER_API_KEY = os.environ.get('TOGETHER_API_KEY', '78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff')

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', 'your_twilio_sid_here')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', 'your_twilio_token_here')
TWILIO_WHATSAPP_NUMBER = 'whatsapp:+14155238886'

# === Setup Together client ===
together = Together(api_key=TOGETHER_API_KEY)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'learnhub-secret-key-2024')
csrf = CSRFProtect(app)
scheduler = BackgroundScheduler()

# === GLOBAL PROGRESS STORE ===
progress_store = {}
scheduled_jobs = {}

def increment_progress(email, course):
    key = (email, course)
    progress_store[key] = progress_store.get(key, 0) + 1
    print(f"📊 Progress updated: {email} - {course} - Day {progress_store[key]}")

def get_progress(email, course):
    key = (email, course)
    return progress_store.get(key, 0)

def reset_progress(email, course):
    progress_store[(email, course)] = 0

# === FREE WHATSAPP FUNCTION ===
def send_whatsapp_message_free(phone_number, message):
    """Send WhatsApp using Twilio Sandbox (FREE)"""
    try:
        print(f"📱 Sending WhatsApp via Twilio to {phone_number}...")
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Clean and format phone number
        clean_phone = ''.join(filter(str.isdigit, phone_number))
        if len(clean_phone) == 10:
            clean_phone = '91' + clean_phone  # Add India country code
        
        to_whatsapp = f"whatsapp:+{clean_phone}"
        
        # Send message
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message,
            to=to_whatsapp
        )
        
        print(f"✅ WhatsApp sent successfully! SID: {message.sid}")
        return True
        
    except TwilioRestException as e:
        print(f"❌ Twilio Error: {e.code} - {e.msg}")
        return False
    except Exception as e:
        print(f"❌ WhatsApp sending failed: {str(e)}")
        return False

# === EMAIL FUNCTION ===
def send_email(to_email, subject, body):
    """Send email via Gmail SMTP"""
    try:
        if not to_email or "@" not in to_email:
            print(f"❌ Invalid email address: {to_email}")
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
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #4361ee, #3a0ca3); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ padding: 20px; background: #f9f9f9; border-radius: 0 0 8px 8px; }}
                .footer {{ margin-top: 20px; text-align: center; font-size: 12px; color: #777; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎓 LearnHub</h1>
                    <p>Your personalized learning journey</p>
                </div>
                <div class="content">
                    {body.replace('\n', '<br>')}
                    <div class="footer">
                        <p>You received this email because you signed up for a course on LearnHub.</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
            
        print(f"✅ Email sent: {subject} to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False

# === LESSON GENERATION ===
def generate_daily_content(course, part, days):
    """Generate lesson content using AI"""
    print(f"🧠 Generating content for {course} - Day {part}/{days}...")
    
    if days == 1:
        prompt = f"""
Create a comprehensive single-day course about: '{course}'. 

Include:
1. Clear title
2. Main concepts with examples
3. 2-3 practical exercises
4. Key takeaways
5. Helpful resources

Make it engaging and practical!
"""
    else:
        prompt = f"""
Create lesson {part} of {days} for: '{course}'. 

This should be a standalone lesson covering one specific topic. Include:
1. Clear lesson title
2. Focused explanation with examples
3. 1-2 practical exercises
4. Key points to remember
5. Relevant resources

Keep it focused only on lesson {part}, not other lessons.
"""

    try:
        response = together.chat.completions.create(
            model="meta-llama/Llama-3-70b-chat-hf",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1200
        )
        content = response.choices[0].message.content.strip()
        print(f"✅ Content generated for Day {part}")
        return content
    except Exception as e:
        print(f"❌ AI generation failed: {e}")
        return f"📚 {course} - Day {part}\n\nLesson content coming soon! Stay tuned for the full lesson."

def format_lesson_for_whatsapp(course, day, total_days, content):
    """Format lesson content for WhatsApp"""
    lines = content.split('\n')
    title = lines[0] if lines else f"Day {day}"
    
    clean_content = content.replace('#', '').replace('**', '').replace('`', '')
    
    if len(clean_content) > 1200:
        clean_content = clean_content[:1200] + "...\n\n[Content truncated]"
    
    message = f"""🎓 *{course} - Day {day}/{total_days}*

*{title}*

{clean_content}

---
📚 LearnHub - Your Daily Learning
💡 Reply with questions!"""

    return message

# === SCHEDULING FUNCTIONS ===
def scheduled_whatsapp_job(email, course, part, days, phone_number):
    """Send daily lesson via WhatsApp"""
    try:
        print(f"🕐 EXECUTING: Day {part} for {course} to {phone_number}")
        
        content = generate_daily_content(course, part, days)
        whatsapp_message = format_lesson_for_whatsapp(course, part, days, content)
        
        success = send_whatsapp_message_free(phone_number, whatsapp_message)
        
        if success:
            increment_progress(email, course)
            print(f"✅ SUCCESS: Day {part} sent and progress updated")
            
            if part == days:
                completion_msg = f"""🎉 *Course Complete!*

Congratulations! You've finished {course}!

You've completed all {days} days of learning. Well done! 🏆

Want to continue learning? Visit our platform for more courses!

---
📚 LearnHub - Celebrating Your Success"""
                send_whatsapp_message_free(phone_number, completion_msg)
        else:
            print(f"⚠️ Day {part} failed to send")
            increment_progress(email, course)
            
    except Exception as e:
        print(f"❌ ERROR in job Day {part}: {str(e)}")
        increment_progress(email, course)

def remove_existing_jobs(email, course):
    """Remove all jobs for this user and course"""
    job_prefix = f"{email}_{course}_"
    jobs_to_remove = []
    
    for job in scheduler.get_jobs():
        if job.id.startswith(job_prefix):
            jobs_to_remove.append(job.id)
    
    for job_id in jobs_to_remove:
        try:
            scheduler.remove_job(job_id)
            print(f"🗑️ Removed old job: {job_id}")
        except Exception as e:
            print(f"⚠️ Could not remove job {job_id}: {e}")

def schedule_course(email, course, days, time_str, phone_number):
    """Schedule course with WhatsApp and Email notifications"""
    try:
        print(f"🎯 Scheduling {course} for {email}, {days} days, phone: {phone_number}")
        
        now = datetime.now()
        remove_existing_jobs(email, course)
        
        # Send welcome message via WhatsApp
        welcome_whatsapp_msg = f"""🎓 *Welcome to {course}!*

You've successfully enrolled in our {days}-day course! 

*Course Details:*
• 📅 Duration: {days} days
• ⏰ Daily lessons at {time_str}
• 📱 Delivery: WhatsApp

Your first lesson arrives tomorrow! Get ready to learn! 🚀

---
📚 LearnHub - Your Learning Journey"""
        
        print("📤 Sending welcome WhatsApp...")
        whatsapp_sent = send_whatsapp_message_free(phone_number, welcome_whatsapp_msg)
        
        # Send welcome email
        welcome_email_content = f"""
        Welcome to <b>{course}</b>!
        
        We're excited to have you on board for this {days}-day learning journey.
        
        <b>Course Details:</b>
        • Duration: {days} days
        • Daily lessons at: {time_str}
        • Delivery: WhatsApp + Email
        
        Your first lesson will arrive tomorrow at your scheduled time.
        
        Get ready to learn and grow! 🚀
        
        Best regards,
        The LearnHub Team
        """
        
        print("📧 Sending welcome email...")
        email_sent = send_email(email, f"Welcome to {course}!", welcome_email_content)
        
        # Schedule lessons
        print(f"📅 Setting up {days} lessons...")
        scheduled_count = 0
        
        for i in range(1, days + 1):
            # Schedule for next day at specified time
            time_obj = datetime.strptime(time_str, "%I:%M %p")
            scheduled_time = now + timedelta(days=i)
            scheduled_time = scheduled_time.replace(
                hour=time_obj.hour, 
                minute=time_obj.minute, 
                second=0, 
                microsecond=0
            )
            
            job_id = f"{email}_{course}_day{i}"
            
            try:
                scheduler.add_job(
                    scheduled_whatsapp_job,
                    'date',
                    run_date=scheduled_time,
                    args=[email, course, i, days, phone_number],
                    id=job_id,
                    replace_existing=True
                )
                print(f"✅ Scheduled: Day {i} at {scheduled_time.strftime('%Y-%m-%d %H:%M')}")
                scheduled_count += 1
                
            except Exception as job_error:
                print(f"❌ Failed to schedule Day {i}: {job_error}")
        
        # Store session data
        reset_progress(email, course)
        session['email'] = email
        session['course'] = course
        session['total_days'] = int(days)
        session['phone_number'] = phone_number
        session['scheduled_at'] = now.isoformat()
        
        print(f"🎉 Course scheduling COMPLETE: {scheduled_count}/{days} lessons scheduled")
        return scheduled_count > 0
        
    except Exception as e:
        print(f"❌ Failed to schedule course: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# === TESTING ROUTES ===
@app.route("/test-send-now")
def test_send_now():
    """Test route to send a lesson immediately"""
    if 'email' not in session:
        return "No active course. Please schedule a course first."
    
    email = session['email']
    course = session['course']
    phone = session.get('phone_number', '9392443002')
    total_days = session.get('total_days', 3)
    current_progress = get_progress(email, course)
    
    next_day = current_progress + 1
    if next_day > total_days:
        return "Course already completed!"
    
    print(f"🚀 MANUAL TRIGGER: Sending Day {next_day} now...")
    scheduled_whatsapp_job(email, course, next_day, total_days, phone)
    
    return f"Sent Day {next_day}! Check your WhatsApp."

@app.route("/test-progress")
def test_progress():
    """Test route to check progress"""
    if 'email' not in session:
        return "No active course"
    
    email = session['email']
    course = session['course']
    total_days = session.get('total_days', 0)
    completed = get_progress(email, course)
    
    return f"""
    Progress for {email} - {course}:
    Completed: {completed}/{total_days} days
    Progress: {completed/total_days*100 if total_days > 0 else 0:.1f}%
    """

@app.route("/test-whatsapp")
def test_whatsapp():
    """Test WhatsApp directly"""
    test_phone = "9392443002"
    test_message = "🔧 TEST: LearnHub WhatsApp is working! 🎉"
    
    success = send_whatsapp_message_free(test_phone, test_message)
    
    if success:
        return "✅ WhatsApp test successful! Check your phone."
    else:
        return "❌ WhatsApp test failed. Check Twilio configuration."

# === MAIN ROUTES ===
@app.route('/', methods=['GET', 'POST'])
def select_course():
    if request.method == "POST":
        return redirect(url_for("schedule_form", course=request.form["course"]))
    return render_template_string(
        FULL_TEMPLATE,  # Your existing FULL_TEMPLATE variable
        template='course_selection',
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
    
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=total_days,
        completed_days=completed_days,
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
            phone = request.form.get("phone", "").strip()
            days = request.form.get("days", "").strip()
            time_str = request.form.get("time", "").strip()
            
            if not all([email, phone, days, time_str]):
                raise ValueError("All fields are required")
            
            if not "@" in email or not "." in email:
                raise ValueError("Please enter a valid email address")
            
            if not days.isdigit() or int(days) <= 0:
                raise ValueError("Please enter a valid number of days")
            
            # Schedule the course
            success = schedule_course(email, course, int(days), time_str, phone)
            
            if success:
                return redirect(url_for('progress'))
            else:
                raise Exception("Scheduling failed")
                
        except ValueError as e:
            error_message = str(e)
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=error_message,
                csrf_token=generate_csrf()
            )
        except Exception as e:
            error_message = "An error occurred. Please try again."
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=error_message,
                csrf_token=generate_csrf()
            )
    
    return render_template_string(
        FULL_TEMPLATE,
        template='user_form',
        course=course,
        csrf_token=generate_csrf()
    )

# === STARTUP ===
if __name__ == '__main__':
    scheduler.start()
    print("🚀 LearnHub Started on Render.com!")
    print("✅ Scheduler running")
    print("✅ WhatsApp integration ready")
    print("✅ Email integration ready")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
