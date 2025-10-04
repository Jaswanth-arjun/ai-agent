import os
import re
import sqlite3
from twilio.rest import Client
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_file, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from together import Together
from flask_wtf.csrf import CSRFProtect, generate_csrf
from io import BytesIO
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from datetime import datetime, timedelta
import mysql.connector
import logging
import uuid
import time
import threading
from dotenv import load_dotenv
load_dotenv()

# === CONFIGURATION ===
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"  # Twilio WhatsApp sandbox

if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN]):
    raise ValueError("Missing Twilio credentials in environment variables")

# === Setup Together client ===
together = Together(api_key=TOGETHER_API_KEY)

# === Setup Twilio client ===
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your-fixed-secret-key-change-this")
csrf = CSRFProtect(app)

# FIXED: Persistent scheduler with SQLite job store
jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
}
scheduler = BackgroundScheduler(jobstores=jobstores)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === GLOBAL PROGRESS STORE (for demo/testing; use a DB for production) ===
progress_store = {}  # key: (phone, course), value: int (completed days)
user_schedules = {}  # Store user schedule information

def increment_progress(phone, course):
    key = (phone, course)
    progress_store[key] = progress_store.get(key, 0) + 1
    logger.info(f"📈 Progress updated: {phone} - {course} - Day {progress_store[key]}")

def get_progress(phone, course):
    key = (phone, course)
    return progress_store.get(key, 0)

def reset_progress(phone, course):
    progress_store[(phone, course)] = 0
    logger.info(f"🔄 Progress reset: {phone} - {course}")

def split_long_message(message, max_length=1500):
    """Split long messages into multiple parts that fit within WhatsApp limits"""
    if len(message) <= max_length:
        return [message]
    
    parts = []
    current_part = ""
    
    # Split by sections first
    sections = message.split('\n\n')
    
    for section in sections:
        # If adding this section would exceed limit, start new part
        if len(current_part) + len(section) + 2 > max_length and current_part:
            parts.append(current_part.strip())
            current_part = section
        else:
            if current_part:
                current_part += '\n\n' + section
            else:
                current_part = section
    
    if current_part:
        parts.append(current_part.strip())
    
    # Add part indicators
    if len(parts) > 1:
        for i, part in enumerate(parts):
            parts[i] = f"📚 Part {i+1}/{len(parts)}\n\n{part}"
    
    logger.info(f"✂️ Split message into {len(parts)} parts")
    return parts

def generate_detailed_course_content(course, part, total_days):
    """Generate detailed course content with YouTube links and references"""
    try:
        prompt = f"""
Create a DETAILED lesson {part} of {total_days} for the course: '{course}'.

Structure the lesson with these EXACT sections:

🎯 DAY {part} OBJECTIVES:
• List 3-4 specific learning objectives for this day

🧠 CORE CONCEPTS:
• Explain 2-3 main concepts in detail with examples
• Use simple, clear explanations
• Include practical real-world applications

💻 HANDS-ON EXERCISE:
• Provide one practical coding/project exercise
• Include step-by-step instructions
• Mention expected output/result

🎥 VIDEO REFERENCES:
• Include 2-3 relevant YouTube video links
• Choose popular, high-quality tutorials
• Videos should be under 15 minutes

📚 ADDITIONAL RESOURCES:
• Provide 2-3 web article/documentation links
• Include official documentation if available
• Choose beginner-friendly resources

💡 KEY TAKEAWAYS:
• Summarize 3-4 main points to remember
• Focus on practical applications

Make sure the content is comprehensive but well-structured. Include proper YouTube URLs and web links.

Course: {course}
Day: {part} of {total_days}
"""
        logger.info(f"🤖 Generating detailed content for {course} - Day {part}/{total_days}")
        response = together.chat.completions.create(
            model="meta-llama/Llama-3-70b-chat-hf",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )
        content = response.choices[0].message.content.strip()
        logger.info(f"✅ Detailed content generated for Day {part} ({len(content)} chars)")
        return content
    except Exception as e:
        logger.error(f"❌ Error generating detailed content: {str(e)}")
        # Fallback detailed content
        return f"""🎯 DAY {part} OBJECTIVES:
• Understand key concepts of {course}
• Practice with hands-on exercises
• Explore additional learning resources

🧠 CORE CONCEPTS:
Today we focus on fundamental concepts that form the foundation of {course}. These concepts are essential for building your skills.

💻 HANDS-ON EXERCISE:
Try building a small project applying today's concepts. Start simple and gradually add complexity.

🎥 VIDEO REFERENCES:
• Search YouTube for "{course} tutorial day {part}"
• Look for official documentation videos

📚 ADDITIONAL RESOURCES:
• Official documentation
• MDN Web Docs (for web technologies)
• Stack Overflow for specific questions

💡 KEY TAKEAWAYS:
• Practice consistently
• Build projects to reinforce learning
• Use multiple learning resources"""

def send_whatsapp(to_phone, message):
    """Send WhatsApp message via Twilio with proper length handling"""
    try:
        # Rate limiting - wait 2 seconds between messages
        time.sleep(2)
        
        if not to_phone or not to_phone.startswith('+'):
            logger.error(f"❌ Invalid phone number: {to_phone}")
            return False
        
        # Format phone number for WhatsApp
        whatsapp_to = f"whatsapp:{to_phone}"
        
        logger.info(f"📤 Sending WhatsApp ({len(message)} chars) to: {to_phone}")
        
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=whatsapp_to
        )
        logger.info(f"✅ Successfully sent WhatsApp to: {to_phone}")
        return True
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp: {str(e)}")
        return False

def send_course_lesson(phone, course, day, total_days):
    """Send a detailed course lesson with proper formatting"""
    try:
        logger.info(f"🎯 SENDING DETAILED LESSON: {phone} - {course} - Day {day}")
        
        # Generate detailed content
        content = generate_detailed_course_content(course, day, total_days)
        
        # Create the main message
        header = f"🎓 {course} - Day {day}/{total_days}\n\n"
        footer = f"\n\n---\n📚 Course Progress: {day}/{total_days} days\n💬 Reply STOP to unsubscribe"
        
        full_message = header + content + footer
        
        # Split into multiple messages if too long
        message_parts = split_long_message(full_message)
        
        # Send all parts
        success_count = 0
        for i, part in enumerate(message_parts):
            if send_whatsapp(phone, part):
                success_count += 1
                # Add small delay between parts to avoid rate limiting
                if i < len(message_parts) - 1:
                    time.sleep(2)
        
        if success_count == len(message_parts):
            increment_progress(phone, course)
            logger.info(f"✅ Successfully delivered Day {day} ({len(message_parts)} parts) to {phone}")
            return True
        else:
            logger.error(f"❌ Failed to send all parts of Day {day} to {phone}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending course lesson: {str(e)}")
        return False

def remove_existing_jobs(phone, course):
    """Remove existing jobs for a user and course"""
    try:
        jobs_removed = 0
        for job in scheduler.get_jobs():
            if job.id and job.id.startswith(f"{phone}_{course}_"):
                scheduler.remove_job(job.id)
                jobs_removed += 1
                logger.info(f"🗑️ Removed existing job: {job.id}")
        return jobs_removed
    except Exception as e:
        logger.error(f"❌ Error removing existing jobs: {str(e)}")
        return 0

def schedule_course_messages_detailed(phone, course, days, time_str):
    """Schedule detailed course messages with proper intervals"""
    try:
        logger.info(f"🚀 DETAILED COURSE MODE: Scheduling {course} for {phone} over {days} days")
        
        # Remove any existing jobs for this user/course
        removed_count = remove_existing_jobs(phone, course)
        if removed_count > 0:
            logger.info(f"🗑️ Removed {removed_count} existing jobs")
        
        # Create a unique schedule ID
        schedule_id = str(uuid.uuid4())[:8]
        user_schedules[(phone, course)] = {
            'schedule_id': schedule_id,
            'total_days': days,
            'start_time': datetime.now(),
            'test_mode': True
        }
        
        # Send welcome message immediately
        welcome_message = (
            f"Welcome to {course}! 🎉\n\n"
            f"Your {days}-day detailed learning journey starts NOW!\n\n"
            "📚 What you'll get each day:\n"
            "• Clear learning objectives\n"
            "• Detailed core concepts\n"
            "• Hands-on exercises\n"
            "• YouTube video references\n"
            "• Additional resources\n"
            "• Key takeaways\n\n"
            f"⏰ Lessons will arrive daily at {time_str}\n\n"
            "Reply STOP to unsubscribe."
        )
        
        if not send_whatsapp(phone, welcome_message):
            logger.error("❌ Failed to send welcome message")
            return False
        
        # Convert user's preferred time to 24-hour format
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        now = datetime.now()
        
        # ✅ FIXED: Enhanced scheduling logic
        day1_sent_immediately = False
        
        for day in range(1, days + 1):
            # Calculate the target date for this lesson
            days_to_add = day - 1
            scheduled_time = now + timedelta(days=days_to_add)
            scheduled_time = scheduled_time.replace(
                hour=time_obj.hour, 
                minute=time_obj.minute, 
                second=0, 
                microsecond=0
            )
            
            # ✅ FIXED: Check if this is Day 1 AND time has passed
            if day == 1:
                time_difference = scheduled_time - now
                logger.info(f"⏰ Day 1 time check: scheduled={scheduled_time}, now={now}, diff={time_difference}")
                
                if scheduled_time < now:
                    # For Day 1, send immediately instead of scheduling
                    logger.info(f"🚀 Day 1 scheduled time was in past, sending immediately!")
                    
                    # Send Day 1 lesson right away
                    send_success = send_course_lesson(phone, course, day, days)
                    if send_success:
                        logger.info(f"✅ Day 1 lesson sent immediately to {phone}")
                        day1_sent_immediately = True
                        # Don't schedule Day 1 - we already sent it
                        continue
                    else:
                        logger.error(f"❌ Failed to send Day 1 lesson immediately to {phone}")
                        # Fall back to scheduling for tomorrow
                        scheduled_time += timedelta(days=1)
            
            # For other days OR if Day 1 immediate send failed
            if scheduled_time < now:
                scheduled_time += timedelta(days=1)
                logger.info(f"⏰ Scheduled time was in past, moved to: {scheduled_time}")
            
            job_id = f"{phone}_{course}_day{day}_{schedule_id}"
            
            scheduler.add_job(
                send_course_lesson,
                'date',
                run_date=scheduled_time,
                args=[phone, course, day, days],
                id=job_id,
                replace_existing=True
            )
            logger.info(f"✅ Scheduled Day {day} for {scheduled_time}")
        
        # Reset progress for this course (unless we sent Day 1 immediately)
        if not day1_sent_immediately:
            reset_progress(phone, course)
        else:
            # If Day 1 was sent immediately, progress should be 1
            increment_progress(phone, course)
            logger.info(f"📈 Progress updated after immediate Day 1 delivery")
        
        logger.info(f"🎯 Successfully scheduled {days} days of detailed content")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to schedule detailed course: {str(e)}")
        return False

# Health check endpoint
@app.route("/health")
def health_check():
    return jsonify({
        "status": "healthy",
        "scheduler_running": scheduler.running,
        "jobs_count": len(scheduler.get_jobs()),
        "timestamp": datetime.now().isoformat()
    })

# ... (KEEP ALL THE HTML TEMPLATE EXACTLY THE SAME AS BEFORE)
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
        
        .test-mode-banner {
            background: linear-gradient(90deg, #ff6b6b, #ff8e53);
            color: white;
            padding: 8px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            text-align: center;
            font-weight: bold;
        }
    </style>
</head>
<body class="bg-gradient-to-br from-primary-50 to-primary-100 min-h-screen">
    <div class="container mx-auto px-4 py-12 max-w-6xl">
        {% if test_mode %}
        <div class="test-mode-banner">
            🚀 TEST MODE: 1 Day = 1 Minute - Lessons will arrive every minute instead of daily
        </div>
        {% endif %}
        
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
                Your personalized learning journey, tailored to your schedule and goals
            </p>
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
                            <h3 class="text-lg font-semibold text-gray-800 mb-3">Personalize Your Plan</h3>
                            <p class="text-gray-600">Set your preferred schedule and learning pace that fits your lifestyle</p>
                        </div>
                        
                        <div class="text-center">
                            <div class="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-5 relative">
                                <span class="text-primary-600 text-2xl font-bold">3</span>
                                <div class="absolute -bottom-5 left-1/2 transform -translate-x-1/2 w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-primary-100"></div>
                            </div>
                            <h3 class="text-lg font-semibold text-gray-800 mb-3">Start Learning</h3>
                            <p class="text-gray-600">Receive daily bite-sized lessons via WhatsApp and track your progress</p>
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
                            <label for="phone" class="block text-sm font-medium text-gray-700 mb-1">WhatsApp Number</label>
                            <div class="relative">
                                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <i class="fab fa-whatsapp text-gray-400"></i>
                                </div>
                                <input type="tel" name="phone" id="phone" class="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-lg input-focus focus:outline-none focus:ring-primary-500 focus:border-primary-500" placeholder="+1234567890" required>
                            </div>
                            <p class="mt-1 text-sm text-gray-500">Enter your WhatsApp number with country code (e.g., +1 for US)</p>
                            <p class="mt-1 text-sm text-primary-600 font-medium">📱 First, join our WhatsApp sandbox by sending "join {{ sandbox_code }}" to {{ twilio_whatsapp_number }}</p>
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
                            {% if test_mode %}
                            <p class="mt-1 text-sm text-orange-600 font-medium">⏰ TEST MODE: Each day = 1 minute for quick testing</p>
                            {% endif %}
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
                const phone = document.getElementById('phone').value;
                const days = document.getElementById('days').value;
                const time = document.getElementById('time').value;
                
                // Basic validation
                if (!phone || !days || !time) {
                    alert('Please fill in all required fields');
                    return false;
                }
                
                // Phone validation - basic check for country code
                const phoneRegex = /^\+[1-9]\d{1,14}$/;
                if (!phoneRegex.test(phone)) {
                    alert('Please enter a valid WhatsApp number with country code (e.g., +1234567890)');
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
                    
                    <!-- Progress Tracker - Moved to top -->
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
                    
                    <!-- What's Next Section - Moved below progress tracker -->
                    <div class="bg-primary-50 rounded-xl p-6 mb-10 text-left">
                        <h3 class="font-semibold text-primary-800 text-lg mb-4 flex items-center">
                            <svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z" />
                            </svg>
                            What's Next?
                        </h3>
                        <ul class="space-y-3">
                            <li class="flex items-start">
                                <svg class="flex-shrink-0 w-5 h-5 text-primary-600 mt-0.5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                                </svg>
                                <span class="text-gray-700">Check your WhatsApp for the first lesson - it should arrive within 24 hours</span>
                            </li>
                            <li class="flex items-start">
                                <svg class="flex-shrink-0 w-5 h-5 text-primary-600 mt-0.5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                                </svg>
                                <span class="text-gray-700">Save <span class="font-mono text-primary-600">{{ twilio_whatsapp_number }}</span> to your contacts</span>
                            </li>
                            <li class="flex items-start">
                                <svg class="flex-shrink-0 w-5 h-5 text-primary-600 mt-0.5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                                </svg>
                                <span class="text-gray-700">Join our <a href="#" class="text-primary-600 hover:underline">community forum</a> for peer support and additional resources</span>
                            </li>
                        </ul>
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
            
            // Animate progress rings
            const progressRings = document.querySelectorAll('.progress-ring');
            progressRings.forEach(ring => {
                const circle = ring.querySelector('.progress-ring__circle');
                const radius = circle.r.baseVal.value;
                const circumference = 2 * Math.PI * radius;
                const progress = ring.dataset.progress;
                
                circle.style.strokeDasharray = circumference;
                circle.style.strokeDashoffset = circumference - (progress / 100) * circumference;
            });
        });
    </script>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def select_course():
    if request.method == "POST":
        course = request.form.get("course")
        if course:
            return redirect(url_for("schedule_form", course=course))
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
            phone = request.form.get("phone", "").strip()
            days = request.form.get("days", "").strip()
            time = request.form.get("time", "").strip()
            
            # Validation
            if not all([phone, days, time]):
                raise ValueError("All fields are required")
            
            if not phone.startswith('+'):
                raise ValueError("Please enter a valid WhatsApp number with country code (e.g., +1 for US)")
            
            if not days.isdigit() or int(days) <= 0 or int(days) > 365:
                raise ValueError("Please enter a valid number of days (1-365)")
            
            # Schedule the course with DETAILED CONTENT MODE
            if schedule_course_messages_detailed(phone, course, int(days), time):
                session['phone'] = phone
                session['course'] = course
                session['total_days'] = int(days)
                return redirect(url_for('progress'))
            else:
                raise Exception("Failed to schedule course messages")
                
        except ValueError as e:
            error_message = str(e)
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=error_message,
                sandbox_code="aloud-coming",
                twilio_whatsapp_number="+14155238886",
                csrf_token=generate_csrf()
            )
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            return render_template_string(
                FULL_TEMPLATE,
                template='user_form',
                course=course,
                error=error_message,
                sandbox_code="sea-sun",
                twilio_whatsapp_number="+14155238886",
                csrf_token=generate_csrf()
            )
    
    return render_template_string(
        FULL_TEMPLATE,
        template='user_form',
        course=course,
        sandbox_code="sea-sun",
        twilio_whatsapp_number="+14155238886",
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
    
    logger.info(f"📊 Progress check: {phone} - {course} - {completed_days}/{total_days} days")
    
    return render_template_string(
        FULL_TEMPLATE,
        template='confirm',
        course=course,
        total_days=total_days,
        completed_days=completed_days,
        twilio_whatsapp_number="+14155238886",
        csrf_token=generate_csrf()
    )

@app.route("/debug-schedules")
def debug_schedules():
    """Debug endpoint to see scheduled jobs"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'next_run': job.next_run_time,
            'args': job.args
        })
    
    return jsonify({
        'total_jobs': len(jobs),
        'jobs': jobs,
        'progress_store': progress_store,
        'user_schedules': user_schedules
    })

@app.route("/send-now")
def send_now():
    """Send a lesson immediately for testing"""
    phone = session.get('phone')
    course = session.get('course')
    total_days = session.get('total_days', 3)
    
    if not phone or not course:
        return "No phone or course in session"
    
    try:
        current_progress = get_progress(phone, course)
        day_to_send = current_progress + 1
        
        if day_to_send > total_days:
            return "Course already completed!"
        
        success = send_course_lesson(phone, course, day_to_send, total_days)
        return f"Day {day_to_send} {'sent successfully!' if success else 'failed to send'}"
    except Exception as e:
        return f"Error: {str(e)}"

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
    phone = request.form["phone"].strip()
    password = request.form["password"]

    try:
        conn = sqlite3.connect("userform.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO users (fullname, phone, password) VALUES (?, ?, ?)", 
                    (fullname, phone, password))
        conn.commit()
        conn.close()

        session["phone"] = phone
        return redirect(url_for("schedule_form"))
    except Exception as e:
        return f"Signup failed: {str(e)}"

@app.route("/certificate")
def certificate():
    if "phone" not in session:
        return redirect(url_for("schedule_form"))

    phone = session["phone"]
    course = session.get("course", "Your Course")
    date = datetime.now().strftime("%B %d, %Y")

    try:
        conn = mysql.connector.connect(
            host="profound-jade-orca-vpmg5-mysql.profound-jade-orca-vpmg5.svc.cluster.local",
            user="mink",
            password="wK2+fH1_wU4=pO4-zJ0_",
            database="profound-jade-orca"
        )

        cur = conn.cursor()
        cur.execute("SELECT name FROM usertable WHERE phone = %s", (phone,))
        result = cur.fetchone()
        name = result[0] if result else phone

        cur.close()
        conn.close()

    except Exception as e:
        print("❌ Error fetching name from MySQL:", e)
        name = phone

    return render_template("cert.html", name=name, course=course, date=date)

# Graceful shutdown
import atexit
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    # FIXED: Start persistent scheduler
    if not scheduler.running:
        scheduler.start()
        job_count = len(scheduler.get_jobs())
        logger.info(f"✅ Persistent scheduler started with {job_count} jobs")
    
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"🚀 Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

