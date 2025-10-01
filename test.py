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

# === GLOBAL PROGRESS STORE ===
progress_store = {}
user_phone_store = {}

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
• Daily lessons via WhatsApp
• Bite-sized content
• Practical exercises
• Progress tracking

⏰ Your first lesson arrives tomorrow!

Happy learning! 🚀
"""
    return send_whatsapp_message(phone, welcome_message)

def send_course_schedule(phone, course, days, time_str):
    """Send course schedule via WhatsApp"""
    schedule_message = f"""
📅 *Your Learning Schedule*

*Course:* {course}
*Duration:* {days} days
*Time:* {time_str} daily

You'll receive {days} daily lessons. Ready to begin? 🚀
"""
    return send_whatsapp_message(phone, schedule_message)

def generate_daily_content(course, part, days):
    """Generate daily course content"""
    if days == 1:
        prompt = f"""
Create a comprehensive one-day course for: '{course}'. Include:
1. Key concepts with examples
2. 2-3 practical exercises
3. Main takeaways
4. Helpful resources

Keep it concise for WhatsApp.
"""
    else:
        prompt = f"""
Create Lesson {part} of {days} for: '{course}'. Include:
1. Today's topic
2. Clear explanations with examples
3. 1-2 practical exercises
4. Key takeaways

Focus only on Lesson {part}. Keep it concise.
"""

    try:
        response = together.chat.completions.create(
            model="meta-llama/Llama-3-70b-chat-hf",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"📚 *{course} - Day {part}/{days}*\n\nContent generation issue. Check back later!"

def format_whatsapp_content(content, course, part, total_days):
    """Format content for WhatsApp"""
    formatted_content = content.replace('**', '*').replace('__', '_')
    header = f"📚 *{course} - Day {part}/{total_days}*\n\n"
    
    max_length = 1500
    if len(header + formatted_content) > max_length:
        formatted_content = formatted_content[:max_length - len(header) - 50] + "..."
    
    return header + formatted_content + f"\n\nProgress: {part}/{total_days} ✅"

def scheduled_job(phone, course, part, total_days):
    """Send daily lessons via WhatsApp"""
    try:
        content = generate_daily_content(course, part, total_days)
        formatted_content = format_whatsapp_content(content, course, part, total_days)
        
        if send_whatsapp_message(phone, formatted_content):
            increment_progress(phone, course)
            print(f"✅ Sent Day {part} for {course} to {phone}")
    except Exception as e:
        print(f"❌ Failed to send day {part}: {str(e)}")

def remove_existing_jobs(phone, course):
    """Remove existing scheduled jobs"""
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{phone}_{course}_"):
            try:
                scheduler.remove_job(job.id)
            except:
                pass

def schedule_course(email, phone, course, days, time_str):
    """Schedule the entire course"""
    try:
        now = datetime.now()
        
        # Convert time to 24-hour format
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        hour = time_obj.hour
        minute = time_obj.minute
        
        remove_existing_jobs(phone, course)
        user_phone_store[email] = phone
        
        # Send welcome and schedule messages
        send_welcome_message(phone, course)
        send_course_schedule(phone, course, days, time_str)
        
        # Schedule daily lessons
        for i in range(1, days + 1):
            scheduled_time = now + timedelta(days=i)
            scheduled_time = scheduled_time.replace(hour=hour, minute=minute, second=0)
            
            job_id = f"{phone}_{course}_day{i}"
            scheduler.add_job(
                scheduled_job,
                'date',
                run_date=scheduled_time,
                args=[phone, course, i, days],
                id=job_id
            )
            print(f"📅 Scheduled Day {i} for {phone} at {scheduled_time}")
            
        reset_progress(phone, course)
        session['email'] = email
        session['phone'] = phone
        session['course'] = course
        session['total_days'] = int(days)
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to schedule course: {str(e)}")
        return False

# === COMPLETE HTML TEMPLATE ===
FULL_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LearnHub - WhatsApp Learning</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        {% if template == 'course_selection' %}
        <!-- Course Selection -->
        <div class="text-center mb-12">
            <h1 class="text-4xl font-bold text-blue-600 mb-4">LearnHub</h1>
            <p class="text-lg text-gray-600">Learn via WhatsApp - Free Daily Lessons</p>
        </div>
        
        <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
            {% for course in ['Python Programming', 'Web Development', 'Data Science', 'JavaScript', 'React Framework', 'AI & ML'] %}
            <div class="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow">
                <div class="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center mb-4 text-blue-600 text-xl">
                    📚
                </div>
                <h3 class="text-xl font-semibold mb-3">{{ course }}</h3>
                <form method="POST" action="/schedule">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <input type="hidden" name="course" value="{{ course }}">
                    <button type="submit" class="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors">
                        Select Course
                    </button>
                </form>
            </div>
            {% endfor %}
        </div>

        {% elif template == 'user_form' %}
        <!-- Schedule Form -->
        <div class="max-w-md mx-auto bg-white rounded-lg shadow-lg p-6">
            <h2 class="text-2xl font-bold mb-6">Schedule {{ course }}</h2>
            
            {% if error %}
            <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
                {{ error }}
            </div>
            {% endif %}
            
            <form method="POST" class="space-y-4">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <input type="hidden" name="course" value="{{ course }}">
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
                    <input type="email" name="email" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500"
                           placeholder="your@email.com">
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">WhatsApp Number</label>
                    <input type="tel" name="phone" placeholder="+1234567890" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                    <p class="text-sm text-gray-500 mt-1">We'll send daily lessons to this WhatsApp number</p>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Duration (Days)</label>
                    <input type="number" name="days" min="1" max="90" value="30" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Preferred Time</label>
                    <select name="time" required 
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                        <option value="">Select time</option>
                        {% for hour in range(1, 13) %}
                        <option value="{{ hour }}:00 AM">{{ hour }}:00 AM</option>
                        <option value="{{ hour }}:30 AM">{{ hour }}:30 AM</option>
                        {% endfor %}
                        {% for hour in range(1, 13) %}
                        <option value="{{ hour }}:00 PM">{{ hour }}:00 PM</option>
                        <option value="{{ hour }}:30 PM">{{ hour }}:30 PM</option>
                        {% endfor %}
                    </select>
                </div>
                
                <button type="submit" class="w-full bg-blue-600 text-white py-3 px-4 rounded-lg hover:bg-blue-700 font-medium">
                    Start Learning via WhatsApp
                </button>
            </form>
        </div>

        {% elif template == 'confirm' %}
        <!-- Confirmation -->
        <div class="max-w-2xl mx-auto bg-white rounded-lg shadow-lg p-8 text-center">
            <div class="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <span class="text-3xl">✅</span>
            </div>
            
            <h2 class="text-2xl font-bold mb-4">Course Scheduled Successfully!</h2>
            <p class="text-lg mb-6">Your <strong class="text-blue-600">{{ course }}</strong> course will be delivered via WhatsApp.</p>
            
            <div class="bg-gray-50 rounded-lg p-6 mb-6 text-left">
                <h3 class="font-semibold mb-4 text-center">Your Progress: {{ completed_days }}/{{ total_days }} days</h3>
                <div class="w-full bg-gray-200 rounded-full h-4 mb-2">
                    <div class="bg-green-600 h-4 rounded-full" style="width: {{ (completed_days/total_days*100) if total_days > 0 else 0 }}%"></div>
                </div>
                <p class="text-center text-sm text-gray-600">{{ completed_days }}/{{ total_days }} lessons completed</p>
            </div>
            
            <div class="bg-blue-50 rounded-lg p-6 mb-6 text-left">
                <h4 class="font-semibold mb-3 text-blue-800">📱 What happens next?</h4>
                <ul class="space-y-2 text-sm text-gray-700">
                    <li>• You'll receive a welcome message on WhatsApp</li>
                    <li>• Daily lessons start tomorrow at your chosen time</li>
                    <li>• Each lesson takes 15-30 minutes</li>
                    <li>• Save our number to ensure delivery</li>
                </ul>
            </div>
            
            <div class="space-y-3">
                <a href="/" class="block bg-blue-600 text-white py-3 px-6 rounded-lg hover:bg-blue-700 font-medium">
                    Back to Courses
                </a>
                <p class="text-sm text-gray-500">Check your WhatsApp for confirmation messages!</p>
            </div>
        </div>
        {% endif %}
        
        <!-- Footer -->
        <footer class="mt-12 text-center text-gray-500 text-sm">
            <p>&copy; 2024 LearnHub. Learning delivered via WhatsApp.</p>
        </footer>
    </div>
</body>
</html>
'''

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
            
            if not all([email, phone, days, time]):
                raise ValueError("All fields are required")
            
            if not "@" in email:
                raise ValueError("Please enter a valid email address")
                
            if not phone.replace('+', '').replace(' ', '').isdigit():
                raise ValueError("Please enter a valid WhatsApp number with country code (e.g., +1234567890)")
            
            if not days.isdigit() or int(days) <= 0 or int(days) > 90:
                raise ValueError("Please enter a valid number of days (1-90)")
            
            if schedule_course(email, phone, course, int(days), time):
                return redirect(url_for('progress'))
            else:
                raise ValueError("Scheduling failed. Please try again.")
                
        except ValueError as e:
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=str(e),
                csrf_token=generate_csrf()
            )
        except Exception as e:
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error="An error occurred. Please try again.",
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
    
    if not phone or not course:
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

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "twilio_available": True})

# Remove problematic routes for now
@app.route("/course-agent")
def course_agent():
    return redirect("/")

@app.route("/signup", methods=["POST"])
def signup():
    return redirect("/")

@app.route("/certificate")
def certificate():
    return "Certificate feature coming soon"

if __name__ == "__main__":
    scheduler.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
