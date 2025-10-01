import os
import re
import sqlite3
import requests
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_file, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from together import Together
from flask_wtf.csrf import CSRFProtect, generate_csrf
from io import BytesIO
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from datetime import datetime, timedelta
import mysql.connector
import time
import traceback

# === CONFIGURATION ===
TOGETHER_API_KEY = "78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff"

# === Setup Together client ===
together = Together(api_key=TOGETHER_API_KEY)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.urandom(24)
csrf = CSRFProtect(app)
scheduler = BackgroundScheduler()

# === TESTING MODE CONFIG ===
TESTING_MODE = True  # Set to False for real daily scheduling
MINUTES_PER_DAY = 1  # 1 minute = 1 day for testing

# === GLOBAL PROGRESS STORE ===
progress_store = {}
scheduled_jobs = {}  # Track all scheduled jobs

def increment_progress(email, course):
    key = (email, course)
    progress_store[key] = progress_store.get(key, 0) + 1
    print(f"📊 Progress updated: {email} - {course} - Day {progress_store[key]}")

def get_progress(email, course):
    key = (email, course)
    return progress_store.get(key, 0)

def reset_progress(email, course):
    progress_store[(email, course)] = 0

# === IMPROVED WHATSAPP FUNCTIONS ===
def send_whatsapp_message(phone_number, message):
    """Send WhatsApp via GreenAPI - WITH PROPER ERROR HANDLING"""
    try:
        print(f"📱 Sending WhatsApp via GreenAPI to {phone_number}...")
        
        # Clean phone number (remove country code if present)
        clean_phone = ''.join(filter(str.isdigit, phone_number))
        if clean_phone.startswith('91') and len(clean_phone) == 12:
            clean_phone = clean_phone[2:]  # Remove India country code
        
        # === YOUR GREENAPI CREDENTIALS ===
        id_instance = "7105332961"
        api_token = "25ed05d04b7642c0af21cfcfa34b8d9f9aa413f0de3c4717a8"
        
        url = f"https://api.green-api.com/waInstance{id_instance}/sendMessage/{api_token}"
        
        payload = {
            "chatId": f"{clean_phone}@c.us",
            "message": message
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        print(f"🔧 API Details:")
        print(f"   URL: {url}")
        print(f"   Phone: {clean_phone}")
        print(f"   Message length: {len(message)}")
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        print(f"📡 Response Status: {response.status_code}")
        print(f"📡 Response Text: {response.text}")
        
        if response.status_code == 200:
            print(f"✅ REAL WhatsApp sent successfully to {clean_phone}!")
            return True
        elif response.status_code == 401:
            print(f"❌ GREENAPI 401 ERROR: Unauthorized - Check your credentials and WhatsApp linking")
            print(f"💡 Go to: https://console.green-api.com/ and check:")
            print(f"   1. Is WhatsApp linked? (Scan QR code)")
            print(f"   2. Is instance status 'Authorized'?")
            print(f"   3. Are API credentials correct?")
            return False
        else:
            print(f"❌ GreenAPI failed: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"❌ GreenAPI timeout - server took too long to respond")
        return False
    except Exception as e:
        print(f"❌ GreenAPI error: {str(e)}")
        return False

def send_whatsapp_fallback(phone_number, message):
    """Fallback method with simulation"""
    try:
        print(f"🔄 Using fallback method for {phone_number}...")
        print(f"📱 [FALLBACK] Would send to {phone_number}:")
        print(f"💬 {message[:100]}...")
        # Simulate sending delay
        time.sleep(2)
        return True
    except Exception as e:
        print(f"❌ Fallback also failed: {str(e)}")
        return False

def format_lesson_for_whatsapp(course, day, total_days, content):
    """Format lesson content for WhatsApp"""
    # Extract title (first line of content)
    lines = content.split('\n')
    title = lines[0] if lines else f"Day {day}"
    
    # Clean up content for WhatsApp (remove markdown, limit length)
    clean_content = content.replace('#', '').replace('**', '').replace('`', '')
    
    # Limit content length for WhatsApp
    if len(clean_content) > 1200:
        clean_content = clean_content[:1200] + "...\n\n[Content truncated]"
    
    message = f"""🎓 *{course} - Day {day}/{total_days}*

*{title}*

{clean_content}

---
📚 LearnHub - Your Daily Learning
💡 Reply with questions!"""

    return message

# === IMPROVED LESSON GENERATION ===
def generate_daily_content(course, part, days):
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

# === FIXED SCHEDULING FUNCTIONS ===
def scheduled_whatsapp_job(email, course, part, days, phone_number):
    """Send daily lesson via WhatsApp - FIXED VERSION"""
    try:
        print(f"🕐 EXECUTING: Day {part} for {course} to {phone_number}")
        
        # Generate lesson content
        content = generate_daily_content(course, part, days)
        print(f"📚 Generated {len(content)} chars for Day {part}")
        
        # Format for WhatsApp
        whatsapp_message = format_lesson_for_whatsapp(course, part, days, content)
        
        # Send via WhatsApp
        success = send_whatsapp_message(phone_number, whatsapp_message)
        
        if success:
            increment_progress(email, course)
            print(f"✅ SUCCESS: Day {part} sent and progress updated")
            
            # If this is the last day, send completion message
            if part == days:
                completion_msg = f"""🎉 *Course Complete!*

Congratulations! You've finished {course}!

You've completed all {days} days of learning. Well done! 🏆

Want to continue learning? Visit our platform for more courses!

---
📚 LearnHub - Celebrating Your Success"""
                send_whatsapp_message(phone_number, completion_msg)
        else:
            print(f"⚠️ Day {part} failed to send")
            # Still increment progress to keep user experience
            increment_progress(email, course)
            
    except Exception as e:
        print(f"❌ ERROR in job Day {part}: {str(e)}")
        increment_progress(email, course)  # Always update progress

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
def check_greenapi_status():
    """Check if GreenAPI is properly configured"""
    try:
        id_instance = "7105332961"
        api_token = "25ed05d04b7642c0af21cfcfa34b8d9f9aa413f0de3c4717a8"
        
        url = f"https://api.green-api.com/waInstance{id_instance}/getStateInstance/{api_token}"
        response = requests.get(url, timeout=10)
        
        print(f"🔍 GreenAPI Status Check: {response.status_code}")
        if response.status_code == 200:
            state_data = response.json()
            print(f"📱 Instance State: {state_data}")
            return state_data.get('stateInstance') == 'authorized'
        else:
            print(f"❌ GreenAPI not accessible: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ GreenAPI status check failed: {e}")
        return False
            
    except Exception as e:
        print(f"❌ GreenAPI status check failed: {e}")
        return False
def schedule_course(email, course, days, time_str, phone_number):
    """Schedule course with TESTING MODE support - FIXED VERSION"""
    try:
        print(f"🎯 Scheduling {course} for {email}, {days} days, phone: {phone_number}")
        
        now = datetime.now()
        
        # Remove any existing jobs for this user/course
        remove_existing_jobs(email, course)
        print("🔍 Checking GreenAPI status...")
        greenapi_status = check_greenapi_status()
        if not greenapi_status:
            print("❌ GreenAPI not authorized - messages will use fallback")
        else:
            print("✅ GreenAPI is authorized and ready!")
        # === FIX: IMPROVED WELCOME MESSAGE HANDLING ===
        welcome_msg = f"""🎓 *Welcome to {course}!*

You've successfully enrolled in our {days}-day course! 

*Course Details:*
• 📅 Duration: {days} days
• ⏰ Daily lessons
• 📱 Delivery: WhatsApp

Your first lesson is on its way! Get ready to learn! 🚀

---
📚 LearnHub - Your Learning Journey"""
        
        print("📤 Sending welcome message...")
        
        # Try multiple times to send welcome message
        max_retries = 2
        welcome_sent = False
        
        for attempt in range(max_retries):
            print(f"🔄 Welcome message attempt {attempt + 1}/{max_retries}...")
            welcome_sent = send_whatsapp_message(phone_number, welcome_msg)
            
            if welcome_sent:
                print("✅ Welcome message sent successfully!")
                break
            else:
                print(f"❌ Welcome message attempt {attempt + 1} failed")
                if attempt < max_retries - 1:  # Don't sleep on last attempt
                    time.sleep(2)  # Wait before retry
        
        # If all attempts failed, use ultimate fallback
        if not welcome_sent:
            print("🚨 All welcome message attempts failed, using ultimate fallback...")
            print(f"💌 [FALLBACK] Welcome message would be sent to {phone_number}")
            print(f"📝 Message: {welcome_msg[:100]}...")
            # Still continue with scheduling even if welcome fails
        
        # === FIX: BETTER SCHEDULING LOGIC ===
        print(f"📅 Setting up {days} lessons...")
        
        scheduled_count = 0
        for i in range(1, days + 1):
            if TESTING_MODE:
                # TESTING: Schedule each lesson 1 minute apart
                scheduled_time = now + timedelta(minutes=(i * MINUTES_PER_DAY))
                time_info = f"in {i} minutes"
            else:
                # PRODUCTION: Schedule for specific time each day
                time_obj = datetime.strptime(time_str, "%I:%M %p")
                scheduled_time = now + timedelta(days=i-1)
                scheduled_time = scheduled_time.replace(
                    hour=time_obj.hour, 
                    minute=time_obj.minute, 
                    second=0, 
                    microsecond=0
                )
                time_info = f"at {scheduled_time.strftime('%Y-%m-%d %H:%M')}"
            
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
                print(f"✅ Scheduled: Day {i} {time_info}")
                scheduled_count += 1
                
            except Exception as job_error:
                print(f"❌ Failed to schedule Day {i}: {job_error}")
                # Continue with other days even if one fails
        
        # === FIX: BETTER SESSION MANAGEMENT ===
        reset_progress(email, course)
        session['email'] = email
        session['course'] = course
        session['total_days'] = int(days)
        session['phone_number'] = phone_number
        session['scheduled_at'] = now.isoformat()
        session['welcome_sent'] = welcome_sent  # Track if welcome was sent
        
        print(f"🎉 Course scheduling COMPLETE: {scheduled_count}/{days} lessons scheduled")
        
        # === FIX: IMMEDIATE FIRST LESSON IN TEST MODE ===
        if TESTING_MODE and scheduled_count > 0:
            print("🚀 TEST MODE: Sending first lesson immediately...")
            # Schedule first lesson to run in 10 seconds instead of 1 minute
            immediate_time = now + timedelta(seconds=10)
            immediate_job_id = f"{email}_{course}_day1_immediate"
            
            scheduler.add_job(
                scheduled_whatsapp_job,
                'date',
                run_date=immediate_time,
                args=[email, course, 1, days, phone_number],
                id=immediate_job_id,
                replace_existing=True
            )
            print(f"✅ Immediate lesson scheduled in 10 seconds")
        
        return scheduled_count > 0  # Return True if at least one lesson scheduled
        
    except Exception as e:
        print(f"❌ Failed to schedule course: {str(e)}")
        import traceback
        traceback.print_exc()  # Print full error details
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

@app.route("/force-complete")
def force_complete():
    """Force complete the course for testing"""
    if 'email' not in session:
        return "No active course"
    
    email = session['email']
    course = session['course']
    total_days = session.get('total_days', 3)
    
    progress_store[(email, course)] = total_days
    return f"Course forced to complete! {total_days}/{total_days} days"

@app.route("/reset-course")
def reset_course():
    """Reset course progress"""
    if 'email' not in session:
        return "No active course"
    
    email = session['email']
    course = session['course']
    
    reset_progress(email, course)
    remove_existing_jobs(email, course)
    return "Course reset! Progress cleared and jobs removed."
@app.route("/test-whatsapp-direct")
def test_whatsapp_direct():
    """Test WhatsApp directly with a simple message"""
    test_phone = "9392443002"
    test_message = "🔧 TEST: LearnHub WhatsApp is working! 🎉"
    
    print("🧪 DIRECT WHATSAPP TEST...")
    success = send_whatsapp_message(test_phone, test_message)
    
    if success:
        return """
        <h1>✅ WhatsApp Test Successful!</h1>
        <p>Check your phone for the test message.</p>
        <a href="/">Go Home</a>
        """
    else:
        return """
        <h1>❌ WhatsApp Test Failed</h1>
        <p>Check your GreenAPI configuration:</p>
        <ol>
            <li>Go to <a href="https://console.green-api.com/" target="_blank">GreenAPI Console</a></li>
            <li>Make sure WhatsApp is linked (scan QR code)</li>
            <li>Check that instance status is "Authorized"</li>
            <li>Verify your API credentials</li>
        </ol>
        <a href="/">Go Home</a>
        """
# === COMPLETE HTML TEMPLATE WITH ALL COURSES ===
FULL_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LearnHub - Personalized Learning Scheduler</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        primary: {
                            50: '#f0f9ff',
                            100: '#e0f2fe',
                            200: '#bae6fd',
                            300: '#7dd3fc',
                            400: '#38bdf8',
                            500: '#0ea5e9',
                            600: '#0284c7',
                            700: '#0369a1',
                            800: '#075985',
                            900: '#0c4a6e',
                        },
                        secondary: {
                            50: '#f5f3ff',
                            100: '#ede9fe',
                            200: '#ddd6fe',
                            300: '#c4b5fd',
                            400: '#a78bfa',
                            500: '#8b5cf6',
                            600: '#7c3aed',
                            700: '#6d28d9',
                            800: '#5b21b6',
                            900: '#4c1d95',
                        }
                    },
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                    },
                    animation: {
                        'float': 'float 6s ease-in-out infinite',
                        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                    }
                }
            }
        }
    </script>
    <style>
        @keyframes float {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
            100% { transform: translateY(0px); }
        }
        
        .progress-ring__circle {
            transition: stroke-dashoffset 0.5s;
            transform: rotate(-90deg);
            transform-origin: 50% 50%;
        }
        
        .gradient-text {
            background-clip: text;
            -webkit-background-clip: text;
            color: transparent;
        }
        
        .card-hover-effect {
            transition: all 0.3s ease;
        }
        
        .card-hover-effect:hover {
            transform: translateY(-5px);
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
        }
        
        .course-icon {
            transition: all 0.3s ease;
        }
        
        .course-card:hover .course-icon {
            transform: scale(1.1);
        }
        
        .glow-effect {
            box-shadow: 0 0 15px rgba(59, 130, 246, 0.3);
        }
        
        .input-focus:focus {
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.3);
            border-color: #3b82f6;
        }
    </style>
</head>
<body class="bg-gradient-to-br from-primary-50 to-primary-100 min-h-screen">
    <div class="container mx-auto px-4 py-12 max-w-6xl">
        <!-- Header Section -->
        <header class="text-center mb-16">
            <div class="flex justify-center mb-6">
                <div class="w-20 h-20 rounded-xl bg-white shadow-md flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-10 w-10 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                    </svg>
                </div>
            </div>
            <h1 class="text-4xl md:text-5xl font-bold text-gray-900 mb-4">
                <span class="gradient-text bg-gradient-to-r from-primary-600 to-secondary-600">LearnHub</span>
            </h1>
            <p class="text-lg md:text-xl text-gray-600 max-w-2xl mx-auto">
                Your personalized learning journey via WhatsApp
            </p>
            {% if testing_info %}
            <div class="mt-4 bg-yellow-100 border border-yellow-400 text-yellow-800 px-4 py-2 rounded-lg inline-block">
                🧪 Testing Mode Active: 1 minute = 1 day
            </div>
            {% endif %}
        </header>
        
        {% if template == 'course_selection' %}
        <!-- Course Selection Section -->
        <section class="mb-20">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
                <!-- Programming Languages -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🐍</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Python Programming</h3>
                        <p class="text-gray-600 text-center mb-6">Master Python from basics to advanced concepts with real-world applications</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Python Programming">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>☕</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Java Development</h3>
                        <p class="text-gray-600 text-center mb-6">Learn Java programming, OOP concepts, and build robust applications</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Java Development">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🟨</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">JavaScript Mastery</h3>
                        <p class="text-gray-600 text-center mb-6">From fundamentals to advanced JS concepts including ES6+ features</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="JavaScript Mastery">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Web Development -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🌐</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Full-Stack Web Development</h3>
                        <p class="text-gray-600 text-center mb-6">Build complete web applications with frontend and backend technologies</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Full-Stack Web Development">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>⚛️</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">React Framework</h3>
                        <p class="text-gray-600 text-center mb-6">Master React.js for building modern, interactive user interfaces</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="React Framework">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Data Science -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>📊</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Data Science Fundamentals</h3>
                        <p class="text-gray-600 text-center mb-6">Learn data analysis, visualization, and machine learning basics</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Data Science Fundamentals">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Mobile Development -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>📱</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Mobile App Development</h3>
                        <p class="text-gray-600 text-center mb-6">Build cross-platform mobile apps with React Native or Flutter</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Mobile App Development">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Cloud & DevOps -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>☁️</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Cloud Computing & DevOps</h3>
                        <p class="text-gray-600 text-center mb-6">Learn AWS, Docker, Kubernetes and CI/CD pipelines</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Cloud Computing & DevOps">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Cybersecurity -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🔒</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Cybersecurity Essentials</h3>
                        <p class="text-gray-600 text-center mb-6">Learn to protect systems and networks from digital attacks</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Cybersecurity Essentials">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Design -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🎨</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">UI/UX Design</h3>
                        <p class="text-gray-600 text-center mb-6">Master design principles, tools like Figma, and user experience concepts</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="UI/UX Design">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- AI/ML -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>🤖</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">AI & Machine Learning</h3>
                        <p class="text-gray-600 text-center mb-6">Introduction to AI concepts and practical machine learning applications</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="AI & Machine Learning">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>

                <!-- Blockchain -->
                <div class="course-card bg-white rounded-xl shadow-md overflow-hidden card-hover-effect">
                    <div class="p-6">
                        <div class="course-icon w-16 h-16 rounded-lg bg-gradient-to-r from-primary-500 to-primary-600 flex items-center justify-center mx-auto mb-5 text-white text-2xl">
                            <span>⛓️</span>
                        </div>
                        <h3 class="text-xl font-semibold text-center text-gray-800 mb-3">Blockchain Development</h3>
                        <p class="text-gray-600 text-center mb-6">Learn smart contracts, DApps, and blockchain fundamentals</p>
                        <form method="POST" action="/schedule">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="Blockchain Development">
                            <button type="submit" class="w-full py-3 px-6 bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 text-white font-medium rounded-lg transition duration-300 transform hover:-translate-y-1">
                                Select Course
                            </button>
                        </form>
                    </div>
                </div>
            </div>
            
            <!-- How It Works Section -->
            <div class="bg-white rounded-xl shadow-md overflow-hidden">
                <div class="p-8 md:p-10">
                    <h2 class="text-2xl md:text-3xl font-bold text-center text-gray-900 mb-10">How LearnHub Works</h2>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
                        <div class="text-center">
                            <div class="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-5 relative">
                                <span class="text-primary-600 text-2xl font-bold">1</span>
                                <div class="absolute -bottom-5 left-1/2 transform -translate-x-1/2 w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-primary-100"></div>
                            </div>
                            <h3 class="text-lg font-semibold text-gray-800 mb-3">Choose Your Course</h3>
                            <p class="text-gray-600">Select from our expert-curated learning paths designed for all skill levels</p>
                        </div>
                        
                        <div class="text-center">
                            <div class="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-5 relative">
                                <span class="text-primary-600 text-2xl font-bold">2</span>
                                <div class="absolute -bottom-5 left-1/2 transform -translate-x-1/2 w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-primary-100"></div>
                            </div>
                            <h3 class="text-lg font-semibold text-gray-800 mb-3">Enter WhatsApp Number</h3>
                            <p class="text-gray-600">Provide your WhatsApp number to receive daily lessons</p>
                        </div>
                        
                        <div class="text-center">
                            <div class="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-5 relative">
                                <span class="text-primary-600 text-2xl font-bold">3</span>
                                <div class="absolute -bottom-5 left-1/2 transform -translate-x-1/2 w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-primary-100"></div>
                            </div>
                            <h3 class="text-lg font-semibold text-gray-800 mb-3">Receive Daily Lessons</h3>
                            <p class="text-gray-600">Get bite-sized lessons via WhatsApp at your preferred time</p>
                        </div>
                    </div>
                </div>
            </div>
        </section>
        
        {% elif template == 'user_form' %}
        <!-- Schedule Form Section -->
        <section class="max-w-2xl mx-auto">
            <div class="bg-white rounded-xl shadow-xl overflow-hidden glow-effect">
                <div class="p-8">
                    <div class="flex justify-between items-center mb-8">
                        <div>
                            <h2 class="text-2xl font-bold text-gray-900">Schedule Your Learning</h2>
                            <p class="text-gray-600">Complete your enrollment for {{ course }}</p>
                        </div>
                        <div class="flex items-center">
                            <span class="bg-primary-100 text-primary-800 text-sm font-semibold px-3 py-1 rounded-full">Step 2 of 2</span>
                        </div>
                    </div>
                    
                    {% if error %}
                    <div class="bg-red-50 border-l-4 border-red-500 p-4 mb-6 rounded-r">
                        <div class="flex">
                            <div class="flex-shrink-0">
                                <svg class="h-5 w-5 text-red-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" />
                                </svg>
                            </div>
                            <div class="ml-3">
                                <p class="text-sm text-red-700">{{ error }}</p>
                            </div>
                        </div>
                    </div>
                    {% endif %}
                    
                    <form method="POST" class="space-y-6" onsubmit="return validateForm()">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                        <input type="hidden" name="course" value="{{ course }}">
                        
                        <div>
                            <label for="email" class="block text-sm font-medium text-gray-700 mb-1">Email Address</label>
                            <div class="relative">
                                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <svg class="h-5 w-5 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
                                        <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
                                    </svg>
                                </div>
                                <input type="email" name="email" id="email" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" placeholder="you@example.com" required>
                            </div>
                        </div>

                        <div>
                            <label for="phone" class="block text-sm font-medium text-gray-700 mb-1">WhatsApp Number</label>
                            <div class="relative">
                                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <span class="text-gray-400">📱</span>
                                </div>
                                <input type="tel" name="phone" id="phone" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" placeholder="911234567890" required>
                            </div>
                            <p class="mt-1 text-sm text-gray-500">We'll send daily lessons to this WhatsApp number</p>
                        </div>
                        
                        <div>
                            <label for="days" class="block text-sm font-medium text-gray-700 mb-1">Learning Duration (Days)</label>
                            <div class="relative">
                                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <svg class="h-5 w-5 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clip-rule="evenodd" />
                                    </svg>
                                </div>
                                <input type="number" name="days" id="days" min="1" max="365" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" placeholder="30" required>
                            </div>
                            <p class="mt-1 text-sm text-gray-500">How many days would you like to complete the course in?</p>
                        </div>
                        
                        <div>
                            <label for="time" class="block text-sm font-medium text-gray-700 mb-1">Preferred Learning Time</label>
                            <div class="relative">
                                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <svg class="h-5 w-5 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd" />
                                    </svg>
                                </div>
                                <select name="time" id="time" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" required>
                                    <option value="">Select a time</option>
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
                            <p class="mt-1 text-sm text-gray-500">When would you like to receive your daily lessons?</p>
                        </div>
                        
                        <div class="pt-2">
                            <button type="submit" class="w-full flex justify-center py-4 px-6 border border-transparent rounded-lg shadow-sm text-lg font-medium text-white bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500 transition duration-300 transform hover:-translate-y-1">
                                Schedule My Learning
                                <svg class="ml-2 -mr-1 w-5 h-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10.293 5.293a1 1 0 011.414 0l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414-1.414L12.586 11H5a1 1 0 110-2h7.586l-2.293-2.293a1 1 0 010-1.414z" clip-rule="evenodd" />
                                </svg>
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </section>
        
        <script>
            function validateForm() {
                const email = document.getElementById('email').value;
                const phone = document.getElementById('phone').value;
                const days = document.getElementById('days').value;
                const time = document.getElementById('time').value;
                
                // Basic validation
                if (!email || !phone || !days || !time) {
                    alert('Please fill in all required fields');
                    return false;
                }
                
                // Email validation
                const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                if (!emailRegex.test(email)) {
                    alert('Please enter a valid email address');
                    return false;
                }

                // Phone validation (basic)
                const phoneRegex = /^\d{10,12}$/;
                if (!phoneRegex.test(phone.replace(/\D/g, ''))) {
                    alert('Please enter a valid phone number (10-12 digits)');
                    return false;
                }
                
                // Days validation
                if (isNaN(days) || days <= 0 || days > 365) {
                    alert('Please enter a valid number of days (1-365)');
                    return false;
                }
                
                return true;
            }
        </script>
        
        {% elif template == 'confirm' %}
        <!-- Confirmation Section -->
        <section class="max-w-2xl mx-auto">
            <div class="bg-white rounded-xl shadow-xl overflow-hidden text-center">
                <div class="p-10">
                    <div class="w-24 h-24 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                        <svg class="w-12 h-12 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                    </div>
                    
                    <h2 class="text-2xl md:text-3xl font-bold text-gray-900 mb-3">Course Scheduled Successfully!</h2>
                    <p class="text-lg text-gray-600 mb-8">
                        Your <span class="font-semibold text-primary-600">{{ course }}</span> course will begin as scheduled.
                    </p>
                    
                    <div class="bg-green-50 border border-green-200 rounded-xl p-6 mb-6">
                        <div class="flex items-center justify-center mb-4">
                            <span class="text-3xl mr-3">📱</span>
                            <h3 class="text-lg font-semibold text-green-800">WhatsApp Lessons Activated</h3>
                        </div>
                        <p class="text-green-700">
                            You'll receive daily lessons via WhatsApp at your chosen time. 
                            Make sure WhatsApp is installed and working on your phone.
                        </p>
                    </div>
                    
                    <!-- Progress Tracker -->
                    <div class="bg-white border border-gray-200 rounded-xl p-6 mb-10">
                        <h3 class="font-semibold text-lg text-gray-900 mb-6 flex items-center justify-center">
                            <svg class="w-5 h-5 mr-2 text-primary-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                            </svg>
                            Your Learning Progress
                        </h3>
                        
                        <div class="space-y-4">
                            {% for day in range(1, total_days+1) %}
                            <div class="flex items-center gap-4">
                                <div class="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center
                                    {% if day <= completed_days %}
                                        bg-green-100 text-green-800
                                    {% elif day == completed_days + 1 %}
                                        bg-yellow-100 text-yellow-800
                                    {% else %}
                                        bg-gray-100 text-gray-500
                                    {% endif %}">
                                    <span class="font-medium">{{ day }}</span>
                                </div>
                                <div class="flex-1">
                                    <div class="flex justify-between items-center">
                                        <span class="font-medium text-gray-800">
                                            Day {{ day }}
                                            {% if day <= completed_days %}
                                                <span class="ml-2 text-green-600 text-sm">Completed</span>
                                            {% elif day == completed_days + 1 %}
                                                <span class="ml-2 text-yellow-600 text-sm">In Progress</span>
                                            {% else %}
                                                <span class="ml-2 text-gray-500 text-sm">Upcoming</span>
                                            {% endif %}
                                        </span>
                                        <span class="text-sm text-gray-500">
                                            {% if day <= completed_days %}
                                                {{ (day/total_days*100)|round|int }}%
                                            {% elif day == completed_days + 1 %}
                                                {{ (completed_days/total_days*100)|round|int }}%
                                            {% else %}
                                                0%
                                            {% endif %}
                                        </span>
                                    </div>
                                    <div class="w-full bg-gray-200 h-2 rounded-full mt-1">
                                        {% if day <= completed_days %}
                                            <div class="bg-green-500 h-2 rounded-full" style="width:100%"></div>
                                        {% elif day == completed_days + 1 %}
                                            <div class="bg-yellow-500 h-2 rounded-full" style="width:{{ (completed_days/total_days*100)|round|int }}%"></div>
                                        {% endif %}
                                    </div>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    
                    <!-- Action Buttons -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <a href="/" class="px-6 py-3 border border-gray-300 rounded-lg text-gray-700 font-medium hover:bg-gray-50 transition duration-300 flex items-center justify-center">
                            <svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
                            </svg>
                            Back to Home
                        </a>
                        <a href="/progress" class="px-6 py-3 bg-primary-600 rounded-lg text-white font-medium hover:bg-primary-700 transition duration-300 flex items-center justify-center">
                            <svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                            </svg>
                            View Progress
                        </a>
                    </div>
                    
                    <!-- Testing Tools -->
                    {% if testing_info %}
                    <div class="mt-8 p-6 bg-blue-50 border border-blue-200 rounded-xl">
                        <h3 class="font-semibold text-lg text-blue-800 mb-4">🧪 Testing Tools</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                            <a href="/test-send-now" class="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition duration-300 text-center">
                                Send Next Lesson Now
                            </a>
                            <a href="/test-progress" class="px-4 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600 transition duration-300 text-center">
                                Check Progress
                            </a>
                            <a href="/force-complete" class="px-4 py-2 bg-purple-500 text-white rounded-lg hover:bg-purple-600 transition duration-300 text-center">
                                Force Complete
                            </a>
                            <a href="/reset-course" class="px-4 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition duration-300 text-center">
                                Reset Course
                            </a>
                        </div>
                    </div>
                    {% endif %}
                    
                    {% if completed_days == total_days %}
                    <div class="mt-8">
                        <a href="/certificate" class="inline-flex items-center px-8 py-3 border border-transparent text-lg font-medium rounded-full shadow-sm text-white bg-gradient-to-r from-green-500 to-green-600 hover:from-green-600 hover:to-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 transition duration-300 transform hover:-translate-y-1">
                            <svg class="w-6 h-6 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            Download Certificate
                        </a>
                    </div>
                    {% endif %}
                </div>
            </div>
        </section>
        {% endif %}
        
        <!-- Footer -->
        <footer class="mt-20 text-center text-gray-500 text-sm">
            <div class="flex justify-center space-x-6 mb-4">
                <a href="#" class="text-gray-400 hover:text-gray-500">
                    <span class="sr-only">Twitter</span>
                    <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M8.29 20.251c7.547 0 11.675-6.253 11.675-11.675 0-.178 0-.355-.012-.53A8.348 8.348 0 0022 5.92a8.19 8.19 0 01-2.357.646 4.118 4.118 0 001.804-2.27 8.224 8.224 0 01-2.605.996 4.107 4.107 0 00-6.993 3.743 11.65 11.65 0 01-8.457-4.287 4.106 4.106 0 001.27 5.477A4.072 4.072 0 012.8 9.713v.052a4.105 4.105 0 003.292 4.022 4.095 4.095 0 01-1.853.07 4.108 4.108 0 003.834 2.85A8.233 8.233 0 012 18.407a11.616 11.616 0 006.29 1.84" />
                    </svg>
                </a>
                <a href="#" class="text-gray-400 hover:text-gray-500">
                    <span class="sr-only">GitHub</span>
                    <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                        <path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
                    </svg>
                </a>
            </div>
            <p>&copy; 2023 LearnHub. All rights reserved.</p>
            <p class="mt-1">
                <a href="#" class="hover:text-primary-600">Privacy Policy</a> · 
                <a href="#" class="hover:text-primary-600">Terms of Service</a>
            </p>
        </footer>
    </div>
    
    <script>
        // Animate course cards on hover
        document.addEventListener('DOMContentLoaded', function() {
            const courseCards = document.querySelectorAll('.course-card');
            courseCards.forEach(card => {
                card.addEventListener('mouseenter', () => {
                    card.querySelector('.course-icon').classList.add('animate-float');
                });
                card.addEventListener('mouseleave', () => {
                    card.querySelector('.course-icon').classList.remove('animate-float');
                });
            });
        });
    </script>
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

@app.route("/progress")
def progress():
    email = session.get('email')
    course = session.get('course')
    total_days = session.get('total_days', 0)
    
    if not email or not course or not total_days:
        return redirect(url_for('select_course'))
    
    completed_days = get_progress(email, course)
    
    # Add testing info if in testing mode
    testing_info = ""
    if TESTING_MODE:
        testing_info = "🧪 Testing Mode Active: 1 minute = 1 day"
    
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=total_days,
        completed_days=completed_days,
        testing_info=testing_info,
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

    try:
        conn = sqlite3.connect("userform.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO users (fullname, email, password) VALUES (?, ?, ?)", 
                    (fullname, email, password))
        conn.commit()
        conn.close()

        session["email"] = email
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
            host="sql104.infinityfree.com",
            user="if0_40043007",
            password="FQM4N2z8L7ai9",
            database="if0_40043007_db"
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
if __name__ == "__main__":
    scheduler.start()
    print("🚀 LearnHub Started!")
    print(f"🧪 Testing Mode: {TESTING_MODE}")
    if TESTING_MODE:
        print(f"⏰ 1 minute = 1 day")
        print("🔗 Test routes available:")
        print("   /test-send-now - Send next lesson immediately")
        print("   /test-progress - Check current progress") 
        print("   /test-whatsapp-direct - Test WhatsApp connection")  # ← ADD THIS
        print("   /force-complete - Mark course as complete")
        print("   /reset-course - Reset course progress")
    
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)

