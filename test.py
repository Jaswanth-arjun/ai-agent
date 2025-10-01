import os
import re
import sqlite3
import logging
from twilio.rest import Client
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from together import Together
from datetime import datetime, timedelta

# === CONFIGURATION ===
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "AC528ab24ab623cb4e38bcc3d1bddef076")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "ace2d0abaf2eb68d267685c30044e507")
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"
TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Setup Twilio client ===
try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    TWILIO_AVAILABLE = True
    logger.info("✅ Twilio client initialized successfully")
except Exception as e:
    twilio_client = None
    TWILIO_AVAILABLE = False
    logger.error(f"❌ Twilio initialization failed: {e}")

# === Setup Together client ===
try:
    together = Together(api_key=TOGETHER_API_KEY)
    TOGETHER_AVAILABLE = True
    logger.info("✅ Together AI client initialized successfully")
except Exception as e:
    together = None
    TOGETHER_AVAILABLE = False
    logger.error(f"❌ Together AI initialization failed: {e}")

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-here")
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
    if not twilio_client:
        logger.warning(f"📱 [SIMULATION] WhatsApp to {to_phone}: {message[:100]}...")
        return True, "simulated"
    
    try:
        if not to_phone.startswith('whatsapp:'):
            to_phone = f"whatsapp:{to_phone}"
            
        message_obj = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message,
            to=to_phone
        )
        logger.info(f"✅ WhatsApp sent to {to_phone}: {message_obj.sid}")
        return True, message_obj.sid
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp: {str(e)}")
        return False, str(e)

def send_welcome_message(phone, course, days, time_str):
    """Send welcome message via WhatsApp"""
    welcome_message = f"""
🎉 *Welcome to LearnHub!*

You've successfully enrolled in *{course}*! 

📚 *Course Details:*
• Duration: {days} days
• Daily Time: {time_str}
• Format: WhatsApp lessons

⏰ Your first lesson arrives tomorrow at {time_str}.

We're excited to guide your learning journey! 🚀
"""
    return send_whatsapp_message(phone, welcome_message)

def send_immediate_test_message(phone, course):
    """Send immediate test message"""
    test_message = f"""
🔔 *LearnHub Test Message*

Welcome to {course}! This confirms your WhatsApp is connected.

*Next Steps:*
1. Save this number for future lessons
2. Your daily lessons start tomorrow at your scheduled time

Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M")}

Ready to learn? Let's go! 📚
"""
    return send_whatsapp_message(phone, test_message)

def generate_daily_content(course, part, days):
    """Generate daily course content using Together AI"""
    if not TOGETHER_AVAILABLE:
        return f"📚 *Lesson {part} of {days} - {course}*\n\nToday's lesson: Learn about {course}. Practice the concepts and explore real-world applications."

    if days == 1:
        prompt = f"""
Create a comprehensive one-day course about '{course}'. Include:
1. Key concepts and fundamentals
2. Practical examples
3. Simple exercises
4. Learning resources

Keep it concise for WhatsApp delivery.
"""
    else:
        prompt = f"""
Create lesson {part} of {days} for a course about '{course}'. 
Focus on one specific topic suitable for day {part}.
Include:
1. Clear explanations
2. Practical examples  
3. 2-3 exercises
4. Key takeaways

Keep it concise for WhatsApp delivery.
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
        return f"📚 *Lesson {part} of {days} - {course}*\n\nToday's focus: Building skills in {course}. Practice regularly and apply what you learn!"

def format_whatsapp_content(content, course, part, total_days):
    """Format content for WhatsApp"""
    formatted_content = content.replace('**', '*').replace('__', '_')
    header = f"📚 *{course} - Day {part}/{total_days}*\n\n"
    
    max_length = 1500
    if len(header + formatted_content) > max_length:
        formatted_content = formatted_content[:max_length - len(header) - 50] + "..."
    
    return header + formatted_content + f"\n\nProgress: {part}/{total_days} days completed ✅"

def scheduled_job(phone, course, part, total_days):
    """Scheduled job to send daily lessons via WhatsApp"""
    try:
        content = generate_daily_content(course, part, total_days)
        formatted_content = format_whatsapp_content(content, course, part, total_days)
        
        success, result = send_whatsapp_message(phone, formatted_content)
        if success:
            increment_progress(phone, course)
            logger.info(f"✅ Sent Day {part} for {course} to {phone}")
        else:
            logger.error(f"❌ Failed to send Day {part}: {result}")
            
    except Exception as e:
        logger.error(f"❌ Failed to send day {part}: {str(e)}")

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
        
        # Convert AM/PM time to 24-hour format
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        hour = time_obj.hour
        minute = time_obj.minute
        
        # Remove existing jobs
        remove_existing_jobs(phone, course)
        
        # Store phone number
        user_phone_store[email] = phone
        
        # Send immediate messages
        message_results = []
        
        # Send test message
        test_success, test_result = send_immediate_test_message(phone, course)
        message_results.append(("Test Message", test_success, test_result))
        
        # Send welcome message
        welcome_success, welcome_result = send_welcome_message(phone, course, days, time_str)
        message_results.append(("Welcome Message", welcome_success, welcome_result))
        
        # Schedule daily lessons
        for i in range(1, int(days) + 1):
            scheduled_time = now + timedelta(days=i)
            scheduled_time = scheduled_time.replace(hour=hour, minute=minute, second=0)
            
            job_id = f"{phone}_{course}_day{i}"
            scheduler.add_job(
                scheduled_job,
                'date',
                run_date=scheduled_time,
                args=[phone, course, i, int(days)],
                id=job_id,
                replace_existing=True
            )
            logger.info(f"📅 Scheduled Day {i} for {phone} at {scheduled_time}")
            
        # Reset progress
        reset_progress(phone, course)
        
        # Store in session
        session['email'] = email
        session['phone'] = phone
        session['course'] = course
        session['total_days'] = int(days)
        session['message_results'] = message_results
        
        return True, message_results
        
    except Exception as e:
        logger.error(f"❌ Failed to schedule course: {str(e)}")
        return False, []

# === SIMPLIFIED HTML TEMPLATES ===

COURSE_SELECTION_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LearnHub - AI Learning Platform</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="text-center mb-12">
            <h1 class="text-4xl font-bold text-blue-600 mb-4">LearnHub</h1>
            <p class="text-xl text-gray-600">AI-powered daily lessons via WhatsApp</p>
        </header>

        <div class="max-w-4xl mx-auto mb-8 bg-blue-50 p-6 rounded-lg">
            <h3 class="text-lg font-semibold mb-2">📱 WhatsApp Setup</h3>
            <p class="text-sm text-gray-700">Send "join source-whispered" to +14155238886 on WhatsApp before starting.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {% for course in courses %}
            <div class="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow">
                <div class="text-3xl mb-4 text-center">{{ course.emoji }}</div>
                <h3 class="text-xl font-semibold text-center mb-3">{{ course.name }}</h3>
                <p class="text-gray-600 text-sm mb-4 text-center">{{ course.description }}</p>
                <form method="POST" action="/schedule">
                    <input type="hidden" name="course" value="{{ course.name }}">
                    <button type="submit" class="w-full bg-blue-500 text-white py-2 px-4 rounded hover:bg-blue-600 transition-colors">
                        Start Learning
                    </button>
                </form>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
'''

USER_FORM_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enroll in {{ course }} - LearnHub</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-md">
        <div class="bg-white rounded-lg shadow-md p-6">
            <h1 class="text-2xl font-bold text-center mb-6">Enroll in {{ course }}</h1>
            
            {% if error %}
            <div class="bg-red-50 border border-red-200 rounded p-3 mb-4">
                <p class="text-red-700">{{ error }}</p>
            </div>
            {% endif %}

            <form method="POST" class="space-y-4">
                <input type="hidden" name="course" value="{{ course }}">
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
                    <input type="email" name="email" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">WhatsApp Number</label>
                    <input type="tel" name="phone" placeholder="+1234567890" required
                           class="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <p class="text-xs text-gray-500 mt-1">Include country code</p>
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Duration (days)</label>
                    <input type="number" name="days" min="1" max="90" required
                           class="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Preferred Time</label>
                    <select name="time" required class="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <option value="">Select time</option>
                        <option value="08:00 AM">8:00 AM</option>
                        <option value="10:00 AM">10:00 AM</option>
                        <option value="12:00 PM">12:00 PM</option>
                        <option value="02:00 PM">2:00 PM</option>
                        <option value="04:00 PM">4:00 PM</option>
                        <option value="06:00 PM">6:00 PM</option>
                        <option value="08:00 PM">8:00 PM</option>
                    </select>
                </div>

                <button type="submit" class="w-full bg-blue-500 text-white py-2 px-4 rounded hover:bg-blue-600 transition-colors">
                    Start Learning Journey
                </button>
            </form>

            <div class="text-center mt-4">
                <a href="/" class="text-blue-500 hover:text-blue-700">← Back to Courses</a>
            </div>
        </div>
    </div>
</body>
</html>
'''

CONFIRMATION_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome! - LearnHub</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-green-50 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-2xl">
        <div class="bg-white rounded-lg shadow-md p-6 text-center">
            <div class="text-6xl mb-4">🎉</div>
            <h1 class="text-3xl font-bold text-green-600 mb-4">Welcome to LearnHub!</h1>
            <p class="text-xl text-gray-700 mb-6">You're enrolled in <strong>{{ course }}</strong></p>
            
            <div class="bg-gray-50 rounded p-4 mb-6">
                <h3 class="font-semibold mb-2">Your Progress: {{ completed_days }}/{{ total_days }} days</h3>
                <div class="w-full bg-gray-200 rounded-full h-4">
                    <div class="bg-green-500 h-4 rounded-full" style="width: {{ (completed_days/total_days)*100 }}%"></div>
                </div>
            </div>

            <div class="space-y-3 text-left mb-6">
                <h4 class="font-semibold">Next Steps:</h4>
                <p>✅ Check WhatsApp for welcome messages</p>
                <p>⏰ Daily lessons start tomorrow</p>
                <p>📚 Save the WhatsApp number</p>
            </div>

            <div class="flex space-x-4 justify-center">
                <a href="/" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">Home</a>
                <a href="/progress" class="bg-green-500 text-white px-4 py-2 rounded hover:bg-green-600">View Progress</a>
            </div>
        </div>
    </div>
</body>
</html>
'''

# Course data
COURSES = [
    {"name": "Python Programming", "emoji": "🐍", "description": "Master Python from basics to advanced"},
    {"name": "Web Development", "emoji": "🌐", "description": "Build complete web applications"},
    {"name": "Data Science", "emoji": "📊", "description": "Learn data analysis and visualization"},
    {"name": "JavaScript", "emoji": "🟨", "description": "Master JavaScript and modern frameworks"},
    {"name": "Machine Learning", "emoji": "🤖", "description": "Introduction to AI and ML concepts"},
    {"name": "Mobile Development", "emoji": "📱", "description": "Build cross-platform mobile apps"}
]

@app.route('/', methods=['GET', 'POST'])
def select_course():
    if request.method == "POST":
        return redirect(url_for("schedule_form", course=request.form["course"]))
    return render_template_string(COURSE_SELECTION_TEMPLATE, courses=COURSES)

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
            
            if not "@" in email:
                raise ValueError("Please enter a valid email address")
                
            if not phone.startswith('+'):
                raise ValueError("Please include country code (e.g., +1)")
            
            if not days.isdigit() or int(days) <= 0:
                raise ValueError("Please enter a valid number of days")
            
            # Schedule the course
            success, message_results = schedule_course(email, phone, course, int(days), time)
            
            if success:
                session['email'] = email
                session['phone'] = phone
                session['course'] = course
                session['total_days'] = int(days)
                session['message_results'] = message_results
                return redirect(url_for('progress'))
            else:
                raise ValueError("Failed to schedule course. Please try again.")
                
        except ValueError as e:
            return render_template_string(USER_FORM_TEMPLATE, course=course, error=str(e))
        except Exception as e:
            return render_template_string(USER_FORM_TEMPLATE, course=course, error="An error occurred. Please try again.")
    
    return render_template_string(USER_FORM_TEMPLATE, course=course)

@app.route("/progress")
def progress():
    phone = session.get('phone')
    course = session.get('course')
    total_days = session.get('total_days', 0)
    
    if not phone or not course:
        return redirect(url_for('select_course'))
        
    completed_days = get_progress(phone, course)
    
    return render_template_string(
        CONFIRMATION_TEMPLATE,
        course=course,
        total_days=total_days,
        completed_days=completed_days
    )

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "twilio_available": TWILIO_AVAILABLE,
        "together_available": TOGETHER_AVAILABLE,
        "users_count": len(user_phone_store),
        "timestamp": datetime.now().isoformat()
    })

@app.route("/")
def home():
    return redirect(url_for('select_course'))

# Initialize scheduler
try:
    scheduler.start()
    logger.info("✅ Scheduler started successfully")
except Exception as e:
    logger.error(f"❌ Scheduler failed to start: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "False").lower() == "true"
    
    print(f"🚀 LearnHub Starting on port {port}")
    print(f"📱 WhatsApp Integration: {'Active' if TWILIO_AVAILABLE else 'Simulated'}")
    print(f"🤖 AI Integration: {'Active' if TOGETHER_AVAILABLE else 'Simulated'}")
    
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False)
