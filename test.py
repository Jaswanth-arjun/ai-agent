import os
import re
import sqlite3
from twilio.rest import Client
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_file, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from together import Together
from flask_wtf.csrf import CSRFProtect, generate_csrf
from io import BytesIO
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from datetime import datetime, timedelta
import mysql.connector

# === CONFIGURATION ===
TWILIO_ACCOUNT_SID = "AC528ab24ab623cb4e38bcc3d1bddef076"
TWILIO_AUTH_TOKEN = "ace2d0abaf2eb68d267685c30044e507"
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"  # Twilio sandbox number
TOGETHER_API_KEY = "78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff"

# === Setup Twilio client ===
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# === Setup Together client ===
together = Together(api_key=TOGETHER_API_KEY)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.urandom(24)
csrf = CSRFProtect(app)
scheduler = BackgroundScheduler()

# === GLOBAL PROGRESS STORE (for demo/testing; use a DB for production) ===
progress_store = {}  # key: (phone, course), value: int (completed days)
user_phone_store = {}  # Store user phone numbers by email

def increment_progress(phone, course):
    key = (phone, course)
    progress_store[key] = progress_store.get(key, 0) + 1

def get_progress(phone, course):
    key = (phone, course)
    return progress_store.get(key, 0)

def reset_progress(phone, course):
    progress_store[(phone, course)] = 0

def send_whatsapp_message(to_phone, message):
    """Send WhatsApp message using Twilio"""
    try:
        message = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message,
            to=f"whatsapp:{to_phone}"
        )
        print(f"📱 WhatsApp message sent to {to_phone}: {message.sid}")
        return True
    except Exception as e:
        print(f"❌ Error sending WhatsApp message: {str(e)}")
        return False

def send_welcome_message(phone, course):
    """Send welcome message via WhatsApp"""
    welcome_message = f"""
🎉 *Welcome to LearnHub!*

You've successfully enrolled in *{course}*! 

📚 *What to expect:*
• Daily lessons delivered via WhatsApp
• Bite-sized content for easy learning
• Practical exercises and resources
• Progress tracking

⏰ Your first lesson will arrive tomorrow at your scheduled time.

💡 *Pro tip:* Save this number to receive all messages properly!

Happy learning! 🚀
"""
    return send_whatsapp_message(phone, welcome_message)

def send_course_schedule(phone, course, days, time_str):
    """Send course schedule via WhatsApp"""
    schedule_message = f"""
📅 *Your Learning Schedule*

*Course:* {course}
*Duration:* {days} days
*Daily Time:* {time_str}

🗓️ *Schedule Overview:*
• You'll receive {days} daily lessons
• Each lesson takes 15-30 minutes
• Lessons arrive at {time_str} daily
• Complete at your own pace

🎯 *Tips for success:*
1. Set aside dedicated time daily
2. Complete the practical exercises
3. Review previous lessons
4. Don't hesitate to revisit content

Ready to begin your learning journey? 🚀
"""
    return send_whatsapp_message(phone, schedule_message)

def generate_daily_content(course, part, days):
    """Generate daily course content using Together AI"""
    if days == 1:
        prompt = f"""
You are an expert course creator. The topic is: '{course}'. The learner wants to complete this course in **1 day**, so provide the **entire course content in a single comprehensive lesson**.

Include all of the following in your response:

1. 📘 **Course Title**
2. 🧠 **Complete Explanation with Real-World Examples**
   - Cover all major concepts a beginner should know.
   - Include relevant examples and clear breakdowns.
3. ✍️ **Practical Exercises**
   - Add 3–5 hands-on tasks or projects.
4. 📌 **Key Takeaways**
   - Summarize essential points to remember.
5. 🔗 **Curated Resource Links**
   - Provide 3–5 helpful links to tutorials, videos, or documentation.

Make sure the content is concise and suitable for WhatsApp delivery.
"""
    else:
        prompt = f"""
You are an expert course creator. The topic is: '{course}'. The learner wants to complete this course in {days} days. Generate **Lesson {part} of {days}**.

Include in Lesson {part}:

1. 📘 **Lesson Title**
2. 🧠 **Focused Explanation with Real Examples**
   - Teach one part of the topic clearly.
3. ✍️ **2–3 Practical Exercises**
4. 📌 **3–5 Key Takeaways**
5. 🔗 **2–3 Curated Resource Links**

Keep the content concise and suitable for WhatsApp delivery. Focus only on Lesson {part}.
"""

    try:
        response = together.chat.completions.create(
            model="meta-llama/Llama-3-70b-chat-hf",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800  # Reduced for WhatsApp
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"📚 *Lesson {part} of {days} - {course}*\n\n🚧 Content generation temporarily unavailable. Please check back later for today's lesson!"

def format_whatsapp_content(content, course, part, days, total_days):
    """Format content for WhatsApp with proper formatting"""
    # Basic formatting for WhatsApp
    formatted_content = content.replace('**', '*').replace('__', '_')
    
    # Add header
    header = f"📚 *{course} - Day {part}/{total_days}*\n\n"
    
    # Truncate if too long for WhatsApp
    max_length = 1500
    if len(header + formatted_content) > max_length:
        formatted_content = formatted_content[:max_length - len(header) - 50] + "...\n\n💡 *Message too long? Some content was trimmed for WhatsApp delivery.*"
    
    return header + formatted_content + f"\n\n---\n*Progress: {part}/{total_days} days completed* ✅"

def scheduled_job(phone, course, part, days, total_days):
    """Scheduled job to send daily lessons via WhatsApp"""
    try:
        content = generate_daily_content(course, part, days)
        formatted_content = format_whatsapp_content(content, course, part, days, total_days)
        
        if send_whatsapp_message(phone, formatted_content):
            increment_progress(phone, course)
            print(f"✅ Sent Day {part} WhatsApp message for {course} to {phone}")
        else:
            print(f"❌ Failed to send Day {part} WhatsApp message")
            
    except Exception as e:
        print(f"❌ Failed to send day {part} WhatsApp: {str(e)}")

def remove_existing_jobs(phone, course):
    """Remove existing scheduled jobs for a user"""
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{phone}_{course}_"):
            try:
                scheduler.remove_job(job.id)
            except:
                pass

def schedule_course(email, phone, course, days, time_str):
    """Schedule the entire course via WhatsApp"""
    try:
        now = datetime.now()
        
        # Convert AM/PM time to 24-hour format for scheduling
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        hour = time_obj.hour
        minute = time_obj.minute
        
        # Remove any existing jobs for this user/course
        remove_existing_jobs(phone, course)
        
        # Store phone number for this email
        user_phone_store[email] = phone
        
        # Send welcome message
        if not send_welcome_message(phone, course):
            raise Exception("Failed to send welcome WhatsApp message")
            
        # Send schedule information
        if not send_course_schedule(phone, course, days, time_str):
            raise Exception("Failed to send schedule WhatsApp message")
        
        # Schedule daily lessons
        for i in range(1, days + 1):
            scheduled_time = now + timedelta(days=i)
            scheduled_time = scheduled_time.replace(hour=hour, minute=minute, second=0)
            
            job_id = f"{phone}_{course}_day{i}"
            scheduler.add_job(
                scheduled_job,
                'date',
                run_date=scheduled_time,
                args=[phone, course, i, days, days],
                id=job_id,
                replace_existing=True
            )
            print(f"📅 Scheduled Day {i} WhatsApp for {phone} at {scheduled_time}")
            
        # Reset progress
        reset_progress(phone, course)
        
        # Store in session
        session['email'] = email
        session['phone'] = phone
        session['course'] = course
        session['total_days'] = int(days)
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to schedule course: {str(e)}")
        return False

# === FLASK ROUTES ===

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
            phone = request.form.get("phone", "").strip()
            days = request.form.get("days", "").strip()
            time = request.form.get("time", "").strip()
            
            # Validation
            if not all([email, phone, days, time]):
                raise ValueError("All fields are required")
            
            if not "@" in email or not "." in email:
                raise ValueError("Please enter a valid email address")
                
            # Basic phone validation (you might want to enhance this)
            if not phone.replace('+', '').replace(' ', '').isdigit():
                raise ValueError("Please enter a valid phone number with country code (e.g., +1234567890)")
            
            if not days.isdigit() or int(days) <= 0:
                raise ValueError("Please enter a valid number of days")
            
            # Schedule the course
            if schedule_course(email, phone, course, int(days), time):
                return redirect(url_for('progress'))
            else:
                raise ValueError("Failed to schedule course. Please try again.")
                
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

@app.route("/progress")
def progress():
    phone = session.get('phone')
    course = session.get('course')
    total_days = session.get('total_days', 0)
    
    if not phone or not course or not total_days:
        return redirect(url_for('select_course'))
        
    completed_days = get_progress(phone, course)
    
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=total_days,
        completed_days=completed_days,
        csrf_token=generate_csrf()
    )

@app.route("/course-agent", methods=["GET", "POST"])
def course_agent():
    return render_template_string(
        FULL_TEMPLATE,
        template='course_selection',
        csrf_token=generate_csrf()
    )

@app.route("/signup", methods=["POST"])
def signup():
    fullname = request.form["fullname"].strip()
    email = request.form["email"].strip()
    password = request.form["password"]
    phone = request.form.get("phone", "").strip()

    try:
        conn = sqlite3.connect("userform.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO users (fullname, email, password, phone) VALUES (?, ?, ?, ?)", 
                    (fullname, email, password, phone))
        conn.commit()
        conn.close()

        session["email"] = email
        session["phone"] = phone
        return redirect(url_for("schedule_form"))
    except Exception as e:
        return f"Signup failed: {str(e)}"

@app.route("/certificate")
def certificate():
    if "email" not in session:
        return redirect(url_for("schedule_form"))

    email = session["email"]
    course = session.get("course", "Your Course")
    date = datetime.now().strftime("%B %d, %Y")

    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="userform"
        )

        cur = conn.cursor()
        cur.execute("SELECT name FROM usertable WHERE email = %s", (email,))
        result = cur.fetchone()
        name = result[0] if result else email.split("@")[0]

        cur.close()
        conn.close()

    except Exception as e:
        print("❌ Error fetching name from MySQL:", e)
        name = email.split("@")[0]

    return render_template("cert.html", name=name, course=course, date=date)

# === MODIFIED HTML TEMPLATE SECTION ===
# Add phone field to the user form in the FULL_TEMPLATE
# Look for the user_form section and add this after the email field:

PHONE_FIELD_HTML = '''
<div>
    <label for="phone" class="block text-sm font-medium text-gray-700 mb-1">WhatsApp Number</label>
    <div class="relative">
        <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <svg class="h-5 w-5 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M7 2a2 2 0 00-2 2v12a2 2 0 002 2h6a2 2 0 002-2V4a2 2 0 00-2-2H7zm3 14a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
            </svg>
        </div>
        <input type="tel" name="phone" id="phone" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" placeholder="+1234567890" required>
    </div>
    <p class="mt-1 text-sm text-gray-500">We'll send daily lessons to this WhatsApp number</p>
</div>
'''

# You'll need to insert the PHONE_FIELD_HTML in the appropriate place in your FULL_TEMPLATE
# After the email field in the user_form section

if __name__ == "__main__":
    scheduler.start()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
