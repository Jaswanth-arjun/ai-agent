import os
import logging
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Simple in-memory storage for demo
user_data = {}
progress_data = {}

# Simple HTML template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>LearnHub - WhatsApp Learning</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        {% if page == 'home' %}
        <!-- Home Page -->
        <div class="text-center mb-12">
            <h1 class="text-4xl font-bold text-blue-600 mb-4">LearnHub</h1>
            <p class="text-lg text-gray-600 mb-8">Free Daily Learning via WhatsApp</p>
        </div>

        <div class="max-w-2xl mx-auto bg-white rounded-lg shadow-lg p-8">
            <h2 class="text-2xl font-bold mb-6 text-center">Start Your Learning Journey</h2>
            
            <form method="POST" action="/schedule" class="space-y-6">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Select Course</label>
                    <select name="course" required class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                        <option value="">Choose a course...</option>
                        <option value="Python Programming">🐍 Python Programming</option>
                        <option value="Web Development">🌐 Web Development</option>
                        <option value="Data Science">📊 Data Science</option>
                        <option value="JavaScript">🟨 JavaScript</option>
                        <option value="AI & Machine Learning">🤖 AI & ML</option>
                    </select>
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Your Email</label>
                    <input type="email" name="email" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500"
                           placeholder="your@email.com">
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">WhatsApp Number</label>
                    <input type="tel" name="phone" required 
                           class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500"
                           placeholder="+1234567890">
                    <p class="text-sm text-gray-500 mt-1">Include country code. We'll send lessons via WhatsApp.</p>
                </div>

                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Duration (Days)</label>
                        <input type="number" name="days" min="1" max="30" value="7" required 
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Preferred Time</label>
                        <select name="time" required 
                                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-blue-500 focus:border-blue-500">
                            <option value="09:00 AM">9:00 AM</option>
                            <option value="10:00 AM">10:00 AM</option>
                            <option value="06:00 PM">6:00 PM</option>
                            <option value="08:00 PM">8:00 PM</option>
                        </select>
                    </div>
                </div>

                <button type="submit" 
                        class="w-full bg-blue-600 text-white py-3 px-4 rounded-lg hover:bg-blue-700 font-medium text-lg">
                    Start Learning via WhatsApp
                </button>
            </form>
        </div>

        {% elif page == 'success' %}
        <!-- Success Page -->
        <div class="max-w-2xl mx-auto bg-white rounded-lg shadow-lg p-8 text-center">
            <div class="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <span class="text-3xl">✅</span>
            </div>
            
            <h2 class="text-2xl font-bold mb-4">Success! Course Scheduled</h2>
            <p class="text-lg mb-6">Your <strong class="text-blue-600">{{ course }}</strong> course will be delivered via WhatsApp.</p>
            
            <div class="bg-blue-50 rounded-lg p-6 mb-6 text-left">
                <h4 class="font-semibold mb-3 text-blue-800">📱 What to expect:</h4>
                <ul class="space-y-2 text-gray-700">
                    <li>• Welcome message on WhatsApp within minutes</li>
                    <li>• Daily lessons starting tomorrow at {{ time }}</li>
                    <li>• {{ days }} days of bite-sized content</li>
                    <li>• Practical exercises and resources</li>
                </ul>
            </div>

            <div class="space-y-3">
                <a href="/" class="inline-block bg-blue-600 text-white py-3 px-6 rounded-lg hover:bg-blue-700 font-medium">
                    Schedule Another Course
                </a>
                <p class="text-sm text-gray-500">Check your WhatsApp for messages!</p>
            </div>
        </div>
        {% endif %}
        
        <footer class="mt-12 text-center text-gray-500 text-sm">
            <p>LearnHub • Learning delivered via WhatsApp</p>
        </footer>
    </div>
</body>
</html>
'''

@app.route('/', methods=['GET'])
def home():
    return render_template_string(HTML_TEMPLATE, page='home')

@app.route('/schedule', methods=['POST'])
def schedule():
    try:
        # Get form data
        course = request.form.get('course', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        days = request.form.get('days', '').strip()
        time = request.form.get('time', '').strip()

        # Validation
        if not all([course, email, phone, days, time]):
            return "All fields are required", 400

        if '@' not in email:
            return "Please enter a valid email address", 400

        if not phone.startswith('+') or not phone[1:].replace(' ', '').isdigit():
            return "Please enter a valid WhatsApp number with country code (e.g., +1234567890)", 400

        if not days.isdigit() or int(days) < 1 or int(days) > 30:
            return "Please enter a valid number of days (1-30)", 400

        # Store user data
        user_key = f"{email}_{phone}"
        user_data[user_key] = {
            'course': course,
            'days': int(days),
            'time': time,
            'joined': datetime.now().isoformat()
        }
        progress_data[user_key] = 0

        # Store in session
        session['user_key'] = user_key
        session['course'] = course
        session['days'] = int(days)

        logger.info(f"New registration: {email} for {course} ({days} days)")

        return render_template_string(
            HTML_TEMPLATE, 
            page='success', 
            course=course, 
            days=days, 
            time=time
        )

    except Exception as e:
        logger.error(f"Error in schedule: {str(e)}")
        return "An error occurred. Please try again.", 500

@app.route('/progress')
def progress():
    user_key = session.get('user_key')
    if not user_key or user_key not in user_data:
        return redirect('/')
    
    user = user_data[user_key]
    completed = progress_data.get(user_key, 0)
    
    return jsonify({
        'course': user['course'],
        'total_days': user['days'],
        'completed_days': completed,
        'progress_percent': round((completed / user['days']) * 100) if user['days'] > 0 else 0
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'users_count': len(user_data)
    })

@app.route('/debug')
def debug():
    return jsonify({
        'user_data': user_data,
        'progress_data': progress_data,
        'session': dict(session)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
