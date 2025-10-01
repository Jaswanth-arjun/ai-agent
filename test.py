import os
import re
import sqlite3
import logging
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
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"
TOGETHER_API_KEY = "78099f081adbc36ae685a12a798f72ee5bc90e17436b71aba902cc1f854495ff"

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
together = Together(api_key=TOGETHER_API_KEY)

# === Flask & Scheduler Setup ===
app = Flask(__name__)
app.secret_key = os.urandom(24)
csrf = CSRFProtect(app)
scheduler = BackgroundScheduler()

# === GLOBAL PROGRESS STORE ===
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
    if not twilio_client:
        logger.warning(f"📱 [SIMULATION] WhatsApp to {to_phone}: {message[:100]}...")
        return True, "simulated"
    
    try:
        # Clean phone number
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

💡 *Important Setup:*
To receive all messages, please send "join source-whispered" to +14155238886 on WhatsApp.

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
3. Each lesson takes 15-30 minutes

Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M")}

Ready to learn? Let's go! 📚
"""
    return send_whatsapp_message(phone, test_message)

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
            max_tokens=800
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"📚 *Lesson {part} of {days} - {course}*\n\n🚧 Content generation temporarily unavailable. Please check back later!"

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

# === ATTRACTIVE HTML TEMPLATES WITH INTERACTIVITY ===

COURSE_SELECTION_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LearnHub - AI-Powered Learning Platform</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); }
        }
        .animate-float { animation: float 3s ease-in-out infinite; }
        .animate-fadeIn { animation: fadeIn 0.6s ease-out; }
        .animate-pulse-slow { animation: pulse 2s ease-in-out infinite; }
        .gradient-text {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .glass-effect {
            background: rgba(255, 255, 255, 0.25);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.18);
        }
        .course-card {
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            transform-style: preserve-3d;
        }
        .course-card:hover {
            transform: translateY(-8px) rotateX(5deg);
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        }
        .progress-ring {
            transform: rotate(-90deg);
        }
    </style>
</head>
<body class="bg-gradient-to-br from-blue-50 via-white to-purple-50 min-h-screen font-sans">
    <!-- Animated Background Elements -->
    <div class="fixed inset-0 overflow-hidden pointer-events-none">
        <div class="absolute -top-40 -right-32 w-80 h-80 bg-purple-200 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-float"></div>
        <div class="absolute -bottom-40 -left-32 w-80 h-80 bg-blue-200 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-float" style="animation-delay: 1.5s;"></div>
        <div class="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-80 h-80 bg-pink-200 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-float" style="animation-delay: 3s;"></div>
    </div>

    <div class="relative z-10 container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-16 animate-fadeIn">
            <div class="flex justify-center mb-6">
                <div class="w-24 h-24 rounded-2xl glass-effect flex items-center justify-center shadow-lg animate-pulse-slow">
                    <i class="fas fa-graduation-cap text-4xl gradient-text"></i>
                </div>
            </div>
            <h1 class="text-5xl md:text-6xl font-bold mb-6 gradient-text">
                LearnHub
            </h1>
            <p class="text-xl md:text-2xl text-gray-600 max-w-3xl mx-auto leading-relaxed">
                Transform your learning journey with <span class="font-semibold text-purple-600">AI-powered</span> daily lessons delivered via WhatsApp
            </p>
            <div class="mt-8 flex justify-center space-x-4">
                <div class="flex items-center text-green-600 bg-green-50 px-4 py-2 rounded-full">
                    <i class="fas fa-check-circle mr-2"></i>
                    <span>Personalized Content</span>
                </div>
                <div class="flex items-center text-blue-600 bg-blue-50 px-4 py-2 rounded-full">
                    <i class="fab fa-whatsapp mr-2"></i>
                    <span>WhatsApp Delivery</span>
                </div>
                <div class="flex items-center text-purple-600 bg-purple-50 px-4 py-2 rounded-full">
                    <i class="fas fa-robot mr-2"></i>
                    <span>AI Powered</span>
                </div>
            </div>
        </header>

        <!-- WhatsApp Setup Banner -->
        <div class="max-w-4xl mx-auto mb-12 animate-fadeIn" style="animation-delay: 0.2s;">
            <div class="bg-gradient-to-r from-blue-500 to-purple-600 rounded-2xl p-6 text-white shadow-xl transform hover:scale-[1.02] transition-transform duration-300">
                <div class="flex items-center">
                    <div class="flex-shrink-0 text-3xl mr-4">
                        <i class="fab fa-whatsapp"></i>
                    </div>
                    <div class="flex-1">
                        <h3 class="text-xl font-bold mb-2">🚀 Get Ready to Learn!</h3>
                        <p class="opacity-90 mb-3">Before you start, set up WhatsApp to receive your daily lessons:</p>
                        <ol class="list-decimal list-inside space-y-1 text-sm opacity-90">
                            <li>Open WhatsApp on your phone</li>
                            <li>Send <code class="bg-black bg-opacity-20 px-2 py-1 rounded">join source-whispered</code> to <strong>+14155238886</strong></li>
                            <li>Wait for the confirmation message</li>
                            <li>Select your course below and start learning!</li>
                        </ol>
                    </div>
                </div>
            </div>
        </div>

        <!-- Course Grid -->
        <section class="mb-20">
            <h2 class="text-3xl md:text-4xl font-bold text-center text-gray-800 mb-12 animate-fadeIn" style="animation-delay: 0.4s;">
                Choose Your <span class="gradient-text">Learning Path</span>
            </h2>
            
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                {% for course in courses %}
                <div class="course-card bg-white rounded-2xl shadow-lg overflow-hidden border border-gray-100 animate-fadeIn" style="animation-delay: {{ loop.index * 0.1 }}s;">
                    <div class="p-6">
                        <div class="text-4xl mb-4 text-center course-icon transform transition-transform duration-300">
                            {{ course.emoji }}
                        </div>
                        <h3 class="text-xl font-bold text-center text-gray-800 mb-3">{{ course.name }}</h3>
                        <p class="text-gray-600 text-center mb-6 text-sm leading-relaxed">{{ course.description }}</p>
                        <form method="POST" action="/schedule" class="course-form">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="course" value="{{ course.name }}">
                            <button type="submit" class="w-full bg-gradient-to-r from-blue-500 to-purple-600 text-white py-3 px-6 rounded-xl font-semibold transition-all duration-300 transform hover:scale-105 hover:shadow-lg active:scale-95 flex items-center justify-center">
                                <i class="fas fa-rocket mr-2"></i>
                                Start Learning
                            </button>
                        </form>
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>

        <!-- How It Works Section -->
        <section class="mb-20">
            <div class="bg-white rounded-3xl shadow-xl p-8 md:p-12 glass-effect">
                <h2 class="text-3xl md:text-4xl font-bold text-center text-gray-800 mb-12">
                    How <span class="gradient-text">LearnHub</span> Works
                </h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
                    <div class="text-center group">
                        <div class="w-20 h-20 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center mx-auto mb-6 transform group-hover:scale-110 transition-transform duration-300 shadow-lg">
                            <span class="text-white text-2xl font-bold">1</span>
                        </div>
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Choose Your Course</h3>
                        <p class="text-gray-600 leading-relaxed">Select from 12+ expert-curated learning paths designed for all skill levels and interests</p>
                    </div>
                    
                    <div class="text-center group">
                        <div class="w-20 h-20 bg-gradient-to-br from-green-500 to-blue-600 rounded-2xl flex items-center justify-center mx-auto mb-6 transform group-hover:scale-110 transition-transform duration-300 shadow-lg">
                            <span class="text-white text-2xl font-bold">2</span>
                        </div>
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Schedule & Personalize</h3>
                        <p class="text-gray-600 leading-relaxed">Set your preferred schedule and receive AI-generated personalized content daily</p>
                    </div>
                    
                    <div class="text-center group">
                        <div class="w-20 h-20 bg-gradient-to-br from-purple-500 to-pink-600 rounded-2xl flex items-center justify-center mx-auto mb-6 transform group-hover:scale-110 transition-transform duration-300 shadow-lg">
                            <span class="text-white text-2xl font-bold">3</span>
                        </div>
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Learn via WhatsApp</h3>
                        <p class="text-gray-600 leading-relaxed">Receive bite-sized lessons, exercises, and progress tracking directly on WhatsApp</p>
                    </div>
                </div>
            </div>
        </section>

        <!-- Stats Section -->
        <section class="mb-20 text-center">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-100">
                    <div class="text-3xl font-bold text-blue-600 mb-2" id="coursesCount">12+</div>
                    <div class="text-gray-600">Courses</div>
                </div>
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-100">
                    <div class="text-3xl font-bold text-green-600 mb-2" id="studentsCount">1.2k+</div>
                    <div class="text-gray-600">Active Learners</div>
                </div>
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-100">
                    <div class="text-3xl font-bold text-purple-600 mb-2" id="lessonsCount">15k+</div>
                    <div class="text-gray-600">Lessons Delivered</div>
                </div>
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-100">
                    <div class="text-3xl font-bold text-orange-600 mb-2" id="successRate">98%</div>
                    <div class="text-gray-600">Success Rate</div>
                </div>
            </div>
        </section>
    </div>

    <script>
        // Animation on scroll
        document.addEventListener('DOMContentLoaded', function() {
            // Animate stats counting
            function animateCounter(element, target, duration = 2000) {
                let start = 0;
                const increment = target / (duration / 16);
                const timer = setInterval(() => {
                    start += increment;
                    if (start >= target) {
                        element.textContent = target + (element.id === 'coursesCount' ? '+' : 
                                                    element.id === 'studentsCount' ? 'k+' :
                                                    element.id === 'lessonsCount' ? 'k+' : '%');
                        clearInterval(timer);
                    } else {
                        element.textContent = Math.floor(start) + (element.id === 'coursesCount' ? '+' : 
                                                    element.id === 'studentsCount' ? 'k+' :
                                                    element.id === 'lessonsCount' ? 'k+' : '%');
                    }
                }, 16);
            }

            // Start counters when visible
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        animateCounter(document.getElementById('coursesCount'), 12);
                        animateCounter(document.getElementById('studentsCount'), 1.2);
                        animateCounter(document.getElementById('lessonsCount'), 15);
                        animateCounter(document.getElementById('successRate'), 98);
                        observer.unobserve(entry.target);
                    }
                });
            });

            observer.observe(document.querySelector('.grid.grid-cols-2'));

            // Course card interactions
            const courseCards = document.querySelectorAll('.course-card');
            courseCards.forEach(card => {
                card.addEventListener('mouseenter', () => {
                    card.querySelector('.course-icon').style.transform = 'scale(1.2) rotate(5deg)';
                });
                card.addEventListener('mouseleave', () => {
                    card.querySelector('.course-icon').style.transform = 'scale(1) rotate(0deg)';
                });
            });

            // Form submission loading states
            const forms = document.querySelectorAll('.course-form');
            forms.forEach(form => {
                form.addEventListener('submit', function(e) {
                    const button = this.querySelector('button');
                    button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Loading...';
                    button.disabled = true;
                });
            });

            // Add floating animation to background elements
            const floatingElements = document.querySelectorAll('.absolute');
            floatingElements.forEach((el, index) => {
                el.style.animationDelay = (index * 1) + 's';
            });

            // Typing effect for header
            const taglines = [
                "AI-powered daily lessons",
                "Personalized learning paths", 
                "WhatsApp delivery",
                "Expert-curated content"
            ];
            let currentTagline = 0;
            const taglineElement = document.querySelector('.text-xl');
            
            function typeWriter(text, element, speed = 50) {
                let i = 0;
                element.innerHTML = '';
                function typing() {
                    if (i < text.length) {
                        element.innerHTML += text.charAt(i);
                        i++;
                        setTimeout(typing, speed);
                    }
                }
                typing();
            }

            // Change tagline every 4 seconds
            setInterval(() => {
                currentTagline = (currentTagline + 1) % taglines.length;
                typeWriter(`Transform your learning journey with ${taglines[currentTagline]}`, taglineElement);
            }, 4000);
        });
    </script>
</body>
</html>
'''

USER_FORM_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Schedule Your Course - LearnHub</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        .animate-slideIn { animation: slideIn 0.5s ease-out; }
        .gradient-bg {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
    </style>
</head>
<body class="bg-gradient-to-br from-blue-50 via-white to-purple-50 min-h-screen font-sans">
    <div class="container mx-auto px-4 py-8 max-w-2xl">
        <!-- Progress Steps -->
        <div class="flex justify-center mb-8">
            <div class="flex items-center">
                <div class="w-10 h-10 rounded-full bg-green-500 text-white flex items-center justify-center">
                    <i class="fas fa-check"></i>
                </div>
                <div class="w-16 h-1 bg-green-500 mx-2"></div>
                <div class="w-10 h-10 rounded-full gradient-bg text-white flex items-center justify-center animate-pulse">
                    <i class="fas fa-2"></i>
                </div>
            </div>
        </div>

        <div class="bg-white rounded-3xl shadow-2xl overflow-hidden animate-slideIn">
            <!-- Header -->
            <div class="gradient-bg text-white p-8 text-center">
                <h1 class="text-3xl font-bold mb-2">Almost There! 🚀</h1>
                <p class="text-blue-100">Complete your enrollment for <strong>{{ course }}</strong></p>
            </div>

            <!-- Form -->
            <div class="p-8">
                {% if error %}
                <div class="bg-red-50 border border-red-200 rounded-xl p-4 mb-6 animate-shake">
                    <div class="flex items-center">
                        <i class="fas fa-exclamation-triangle text-red-500 mr-3"></i>
                        <p class="text-red-700">{{ error }}</p>
                    </div>
                </div>
                {% endif %}

                <form method="POST" class="space-y-6" id="enrollmentForm">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <input type="hidden" name="course" value="{{ course }}">
                    
                    <!-- Email Field -->
                    <div class="group">
                        <label for="email" class="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            <i class="fas fa-envelope text-blue-500 mr-2"></i>
                            Email Address
                        </label>
                        <div class="relative">
                            <input type="email" name="email" id="email" 
                                   class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-300 group-hover:border-blue-300"
                                   placeholder="your.email@example.com"
                                   required>
                            <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
                                <i class="fas fa-user text-gray-400"></i>
                            </div>
                        </div>
                    </div>

                    <!-- WhatsApp Field -->
                    <div class="group">
                        <label for="phone" class="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            <i class="fab fa-whatsapp text-green-500 mr-2"></i>
                            WhatsApp Number
                        </label>
                        <div class="relative">
                            <input type="tel" name="phone" id="phone" 
                                   class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-green-500 focus:border-green-500 transition-all duration-300 group-hover:border-green-300"
                                   placeholder="+1234567890"
                                   required>
                            <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
                                <i class="fas fa-mobile-alt text-gray-400"></i>
                            </div>
                        </div>
                        <p class="mt-2 text-sm text-gray-500 flex items-center">
                            <i class="fas fa-info-circle text-blue-500 mr-1"></i>
                            Include country code (e.g., +1 for US, +44 for UK)
                        </p>
                    </div>

                    <!-- Duration Field -->
                    <div class="group">
                        <label for="days" class="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            <i class="fas fa-calendar-alt text-purple-500 mr-2"></i>
                            Learning Duration
                        </label>
                        <div class="relative">
                            <input type="number" name="days" id="days" min="1" max="365" 
                                   class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-purple-500 focus:border-purple-500 transition-all duration-300 group-hover:border-purple-300"
                                   placeholder="30"
                                   required>
                            <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
                                <span class="text-gray-500">days</span>
                            </div>
                        </div>
                        <p class="mt-2 text-sm text-gray-500">
                            How many days would you like to complete this course in?
                        </p>
                    </div>

                    <!-- Time Field -->
                    <div class="group">
                        <label for="time" class="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            <i class="fas fa-clock text-orange-500 mr-2"></i>
                            Preferred Learning Time
                        </label>
                        <select name="time" id="time" 
                                class="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-orange-500 transition-all duration-300 group-hover:border-orange-300 appearance-none bg-white"
                                required>
                            <option value="">Select your preferred time</option>
                            <option value="06:00 AM">🌅 6:00 AM - Early Bird</option>
                            <option value="08:00 AM">☀️ 8:00 AM - Morning Start</option>
                            <option value="10:00 AM">📚 10:00 AM - Study Time</option>
                            <option value="12:00 PM">🍽️ 12:00 PM - Lunch Break</option>
                            <option value="02:00 PM">💪 2:00 PM - Afternoon Boost</option>
                            <option value="04:00 PM">☕ 4:00 PM - Coffee Break</option>
                            <option value="06:00 PM">🌇 6:00 PM - Evening Session</option>
                            <option value="08:00 PM">🌙 8:00 PM - Night Owl</option>
                            <option value="10:00 PM">✨ 10:00 PM - Late Night</option>
                        </select>
                        <div class="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
                            <i class="fas fa-chevron-down text-gray-400"></i>
                        </div>
                    </div>

                    <!-- Submit Button -->
                    <button type="submit" 
                            class="w-full gradient-bg text-white py-4 px-6 rounded-xl font-semibold text-lg transition-all duration-300 transform hover:scale-105 hover:shadow-lg active:scale-95 flex items-center justify-center group"
                            id="submitBtn">
                        <i class="fas fa-paper-plane mr-3 group-hover:animate-bounce"></i>
                        Start Learning Journey
                        <i class="fas fa-arrow-right ml-3 transform group-hover:translate-x-1 transition-transform"></i>
                    </button>
                </form>
            </div>
        </div>

        <!-- Back Button -->
        <div class="text-center mt-6">
            <a href="/" class="text-blue-600 hover:text-blue-800 transition-colors duration-300 flex items-center justify-center">
                <i class="fas fa-arrow-left mr-2"></i>
                Back to Course Selection
            </a>
        </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const form = document.getElementById('enrollmentForm');
            const submitBtn = document.getElementById('submitBtn');

            // Real-time validation
            const emailInput = document.getElementById('email');
            const phoneInput = document.getElementById('phone');
            const daysInput = document.getElementById('days');

            function validateEmail(email) {
                const re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
                return re.test(email);
            }

            function validatePhone(phone) {
                const re = /^\\+[1-9]\\d{1,14}$/;
                return re.test(phone);
            }

            function validateDays(days) {
                return days >= 1 && days <= 365;
            }

            function updateValidationState(input, isValid) {
                if (isValid) {
                    input.classList.remove('border-red-300');
                    input.classList.add('border-green-300');
                } else {
                    input.classList.remove('border-green-300');
                    input.classList.add('border-red-300');
                }
            }

            emailInput.addEventListener('input', function() {
                updateValidationState(this, validateEmail(this.value));
            });

            phoneInput.addEventListener('input', function() {
                updateValidationState(this, validatePhone(this.value));
            });

            daysInput.addEventListener('input', function() {
                updateValidationState(this, validateDays(parseInt(this.value)));
            });

            // Form submission
            form.addEventListener('submit', function(e) {
                const email = emailInput.value;
                const phone = phoneInput.value;
                const days = parseInt(daysInput.value);

                if (!validateEmail(email) || !validatePhone(phone) || !validateDays(days)) {
                    e.preventDefault();
                    submitBtn.innerHTML = '<i class="fas fa-exclamation-triangle mr-2"></i>Please check your inputs';
                    submitBtn.classList.remove('gradient-bg');
                    submitBtn.classList.add('bg-red-500');
                    setTimeout(() => {
                        submitBtn.innerHTML = '<i class="fas fa-paper-plane mr-3"></i>Start Learning Journey<i class="fas fa-arrow-right ml-3"></i>';
                        submitBtn.classList.remove('bg-red-500');
                        submitBtn.classList.add('gradient-bg');
                    }, 2000);
                    return;
                }

                // Show loading state
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Scheduling Your Course...';
                submitBtn.disabled = true;
            });

            // Add animation to form elements
            const formGroups = document.querySelectorAll('.group');
            formGroups.forEach((group, index) => {
                group.style.animationDelay = `${index * 0.1}s`;
                group.classList.add('animate-slideIn');
            });
        });
    </script>
</body>
</html>
'''

CONFIRMATION_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to LearnHub! 🎉</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes confetti {
            0% { transform: translateY(-100px) rotate(0deg); opacity: 1; }
            100% { transform: translateY(100vh) rotate(360deg); opacity: 0; }
        }
        @keyframes bounce {
            0%, 20%, 50%, 80%, 100% { transform: translateY(0); }
            40% { transform: translateY(-10px); }
            60% { transform: translateY(-5px); }
        }
        .confetti {
            position: fixed;
            width: 10px;
            height: 10px;
            background: #ff0000;
            animation: confetti 5s ease-in-out infinite;
        }
        .animate-bounce-slow { animation: bounce 2s infinite; }
        .progress-ring {
            transform: rotate(-90deg);
        }
    </style>
</head>
<body class="bg-gradient-to-br from-green-50 via-white to-blue-50 min-h-screen font-sans">
    <!-- Confetti Animation -->
    <div id="confetti-container"></div>

    <div class="container mx-auto px-4 py-8 max-w-4xl">
        <!-- Success Header -->
        <div class="text-center mb-12">
            <div class="w-32 h-32 bg-gradient-to-br from-green-400 to-blue-500 rounded-full flex items-center justify-center mx-auto mb-6 shadow-2xl animate-bounce-slow">
                <i class="fas fa-trophy text-white text-5xl"></i>
            </div>
            <h1 class="text-4xl md:text-5xl font-bold text-gray-800 mb-4">
                Welcome to <span class="text-transparent bg-clip-text bg-gradient-to-r from-green-500 to-blue-600">LearnHub</span>! 🎉
            </h1>
            <p class="text-xl text-gray-600">
                Your <strong class="text-green-600">{{ course }}</strong> journey starts now!
            </p>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-12">
            <!-- Progress Card -->
            <div class="bg-white rounded-3xl shadow-xl p-8 border border-gray-100">
                <h3 class="text-2xl font-bold text-gray-800 mb-6 flex items-center">
                    <i class="fas fa-chart-line text-blue-500 mr-3"></i>
                    Your Learning Progress
                </h3>
                
                <!-- Circular Progress -->
                <div class="flex justify-center mb-6">
                    <div class="relative w-48 h-48">
                        <svg class="w-full h-full progress-ring" viewBox="0 0 100 100">
                            <!-- Background circle -->
                            <circle cx="50" cy="50" r="40" stroke="#e5e7eb" stroke-width="8" fill="transparent"/>
                            <!-- Progress circle -->
                            <circle cx="50" cy="50" r="40" stroke="url(#gradient)" stroke-width="8" fill="transparent"
                                    stroke-dasharray="251.2"
                                    stroke-dashoffset="{{ 251.2 * (1 - (completed_days/total_days)) }}"
                                    stroke-linecap="round"/>
                            <defs>
                                <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="0%">
                                    <stop offset="0%" stop-color="#10b981"/>
                                    <stop offset="100%" stop-color="#3b82f6"/>
                                </linearGradient>
                            </defs>
                        </svg>
                        <div class="absolute inset-0 flex flex-col items-center justify-center">
                            <span class="text-3xl font-bold text-gray-800">{{ completed_days }}/{{ total_days }}</span>
                            <span class="text-sm text-gray-600">days completed</span>
                        </div>
                    </div>
                </div>

                <!-- Progress Details -->
                <div class="space-y-4">
                    {% for day in range(1, total_days+1) %}
                    <div class="flex items-center justify-between p-3 rounded-lg {% if day <= completed_days %}bg-green-50 border border-green-200{% elif day == completed_days + 1 %}bg-blue-50 border border-blue-200 animate-pulse{% else %}bg-gray-50{% endif %}">
                        <div class="flex items-center">
                            <div class="w-8 h-8 rounded-full flex items-center justify-center mr-3 
                                {% if day <= completed_days %}bg-green-500 text-white
                                {% elif day == completed_days + 1 %}bg-blue-500 text-white
                                {% else %}bg-gray-300 text-gray-600{% endif %}">
                                {{ day }}
                            </div>
                            <span class="font-medium">Day {{ day }}</span>
                        </div>
                        <span class="text-sm font-semibold 
                            {% if day <= completed_days %}text-green-600
                            {% elif day == completed_days + 1 %}text-blue-600
                            {% else %}text-gray-500{% endif %}">
                            {% if day <= completed_days %}✅ Complete
                            {% elif day == completed_days + 1 %}🔄 Next
                            {% else %}⏳ Coming
                            {% endif %}
                        </span>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- Next Steps Card -->
            <div class="space-y-6">
                <!-- WhatsApp Status -->
                {% if message_results %}
                <div class="bg-white rounded-3xl shadow-xl p-6 border border-gray-100">
                    <h4 class="text-lg font-semibold text-gray-800 mb-4 flex items-center">
                        <i class="fab fa-whatsapp text-green-500 mr-2"></i>
                        WhatsApp Status
                    </h4>
                    <div class="space-y-3">
                        {% for msg_type, success, detail in message_results %}
                        <div class="flex items-center justify-between p-3 rounded-lg {% if success %}bg-green-50{% else %}bg-red-50{% endif %}">
                            <span class="font-medium">{{ msg_type }}</span>
                            <span class="{% if success %}text-green-600{% else %}text-red-600{% endif %} font-semibold">
                                {% if success %}
                                <i class="fas fa-check-circle mr-1"></i>Sent
                                {% else %}
                                <i class="fas fa-exclamation-triangle mr-1"></i>Failed
                                {% endif %}
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}

                <!-- What's Next -->
                <div class="bg-gradient-to-br from-blue-500 to-purple-600 rounded-3xl shadow-xl p-6 text-white">
                    <h4 class="text-lg font-semibold mb-4 flex items-center">
                        <i class="fas fa-rocket mr-2"></i>
                        What's Next?
                    </h4>
                    <ul class="space-y-3">
                        <li class="flex items-start">
                            <i class="fas fa-check-circle mt-1 mr-3 text-green-300"></i>
                            <span>Check WhatsApp for welcome messages (arrives within minutes)</span>
                        </li>
                        <li class="flex items-start">
                            <i class="fas fa-bell mt-1 mr-3 text-yellow-300"></i>
                            <span>Daily lessons start tomorrow at your chosen time</span>
                        </li>
                        <li class="flex items-start">
                            <i class="fas fa-save mt-1 mr-3 text-blue-300"></i>
                            <span>Save the WhatsApp number to receive all lessons</span>
                        </li>
                        <li class="flex items-start">
                            <i class="fas fa-chart-bar mt-1 mr-3 text-purple-300"></i>
                            <span>Track your progress and stay motivated!</span>
                        </li>
                    </ul>
                </div>

                <!-- Quick Actions -->
                <div class="bg-white rounded-3xl shadow-xl p-6 border border-gray-100">
                    <h4 class="text-lg font-semibold text-gray-800 mb-4">Quick Actions</h4>
                    <div class="grid grid-cols-2 gap-3">
                        <a href="/" class="bg-gray-100 text-gray-700 py-3 px-4 rounded-xl text-center font-medium hover:bg-gray-200 transition-colors duration-300 flex items-center justify-center">
                            <i class="fas fa-home mr-2"></i>Home
                        </a>
                        <a href="/progress" class="bg-blue-500 text-white py-3 px-4 rounded-xl text-center font-medium hover:bg-blue-600 transition-colors duration-300 flex items-center justify-center">
                            <i class="fas fa-chart-line mr-2"></i>Progress
                        </a>
                    </div>
                </div>
            </div>
        </div>

        <!-- Celebration Message -->
        <div class="bg-gradient-to-r from-yellow-400 to-orange-500 rounded-3xl shadow-xl p-8 text-center text-white">
            <h3 class="text-2xl font-bold mb-4">🎊 You're All Set! 🎊</h3>
            <p class="text-lg opacity-90 mb-4">
                Get ready to transform your skills with daily AI-powered lessons delivered straight to your WhatsApp!
            </p>
            <div class="flex justify-center space-x-4">
                <div class="flex items-center bg-white bg-opacity-20 px-4 py-2 rounded-full">
                    <i class="fas fa-brain mr-2"></i>
                    <span>AI-Powered</span>
                </div>
                <div class="flex items-center bg-white bg-opacity-20 px-4 py-2 rounded-full">
                    <i class="fab fa-whatsapp mr-2"></i>
                    <span>WhatsApp Delivery</span>
                </div>
                <div class="flex items-center bg-white bg-opacity-20 px-4 py-2 rounded-full">
                    <i class="fas fa-user-graduate mr-2"></i>
                    <span>Personalized</span>
                </div>
            </div>
        </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Create confetti
            function createConfetti() {
                const container = document.getElementById('confetti-container');
                const colors = ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff'];
                
                for (let i = 0; i < 50; i++) {
                    const confetti = document.createElement('div');
                    confetti.className = 'confetti';
                    confetti.style.left = Math.random() * 100 + 'vw';
                    confetti.style.background = colors[Math.floor(Math.random() * colors.length)];
                    confetti.style.animationDelay = Math.random() * 5 + 's';
                    confetti.style.width = Math.random() * 10 + 5 + 'px';
                    confetti.style.height = Math.random() * 10 + 5 + 'px';
                    container.appendChild(confetti);
                }
            }

            createConfetti();

            // Progress animation
            const progressCircle = document.querySelector('circle[stroke-dashoffset]');
            if (progressCircle) {
                const finalOffset = progressCircle.getAttribute('stroke-dashoffset');
                progressCircle.style.strokeDashoffset = '251.2';
                
                setTimeout(() => {
                    progressCircle.style.transition = 'stroke-dashoffset 2s ease-in-out';
                    progressCircle.style.strokeDashoffset = finalOffset;
                }, 500);
            }

            // Celebration sound effect (optional)
            function playCelebration() {
                // You could add a subtle celebration sound here
                console.log('🎉 Celebration time!');
            }

            playCelebration();
        });
    </script>
</body>
</html>
'''

# Course data
COURSES = [
    {"name": "Python Programming", "emoji": "🐍", "description": "Master Python from basics to advanced concepts with real-world applications"},
    {"name": "Java Development", "emoji": "☕", "description": "Learn Java programming, OOP concepts, and build robust applications"},
    {"name": "JavaScript Mastery", "emoji": "🟨", "description": "From fundamentals to advanced JS concepts including ES6+ features"},
    {"name": "Full-Stack Web Development", "emoji": "🌐", "description": "Build complete web applications with frontend and backend technologies"},
    {"name": "React Framework", "emoji": "⚛️", "description": "Master React.js for building modern, interactive user interfaces"},
    {"name": "Data Science Fundamentals", "emoji": "📊", "description": "Learn data analysis, visualization, and machine learning basics"},
    {"name": "Mobile App Development", "emoji": "📱", "description": "Build cross-platform mobile apps with React Native or Flutter"},
    {"name": "Cloud Computing & DevOps", "emoji": "☁️", "description": "Learn AWS, Docker, Kubernetes and CI/CD pipelines"},
    {"name": "Cybersecurity Essentials", "emoji": "🔒", "description": "Learn to protect systems and networks from digital attacks"},
    {"name": "UI/UX Design", "emoji": "🎨", "description": "Master design principles, tools like Figma, and user experience concepts"},
    {"name": "AI & Machine Learning", "emoji": "🤖", "description": "Introduction to AI concepts and practical machine learning applications"},
    {"name": "Blockchain Development", "emoji": "⛓️", "description": "Learn smart contracts, DApps, and blockchain fundamentals"}
]

@app.route('/', methods=['GET', 'POST'])
def select_course():
    if request.method == "POST":
        return redirect(url_for("schedule_form", course=request.form["course"]))
    return render_template_string(
        COURSE_SELECTION_TEMPLATE,
        courses=COURSES,
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
                
            # Phone validation
            if not phone.startswith('+') or not phone[1:].replace(' ', '').isdigit():
                raise ValueError("Please enter a valid WhatsApp number with country code (e.g., +1234567890)")
            
            if not days.isdigit() or int(days) <= 0:
                raise ValueError("Please enter a valid number of days")
            
            # Schedule the course via WhatsApp
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
            error_message = str(e)
            return render_template_string(
                USER_FORM_TEMPLATE,
                course=course,
                error=error_message,
                csrf_token=generate_csrf()
            )
        except Exception as e:
            error_message = "An error occurred. Please try again."
            return render_template_string(
                USER_FORM_TEMPLATE,
                course=course,
                error=error_message,
                csrf_token=generate_csrf()
            )
    
    return render_template_string(
        USER_FORM_TEMPLATE,
        course=course,
        csrf_token=generate_csrf()
    )

@app.route("/progress")
def progress():
    phone = session.get('phone')
    course = session.get('course')
    total_days = session.get('total_days', 0)
    message_results = session.get('message_results', [])
    
    if not phone or not course or not total_days:
        return redirect(url_for('select_course'))
        
    completed_days = get_progress(phone, course)
    
    return render_template_string(
        CONFIRMATION_TEMPLATE,
        course=course,
        total_days=total_days,
        completed_days=completed_days,
        message_results=message_results
    )

@app.route("/course-agent", methods=["GET", "POST"])
def course_agent():
    return redirect(url_for('select_course'))

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

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "twilio_available": TWILIO_AVAILABLE,
        "users_count": len(user_phone_store),
        "timestamp": datetime.now().isoformat()
    })

if __name__ == "__main__":
    scheduler.start()
    print("🚀 LearnHub Started - Production Mode")
    print("📱 WhatsApp Integration: Active")
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
