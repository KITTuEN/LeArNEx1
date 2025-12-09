from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

import re
import json
import random
import string
from youtube_transcript_api import YouTubeTranscriptApi
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from generate_dsa_questions import generate_questions_batch
from email.mime.multipart import MIMEMultipart
import certifi
import tempfile
import threading
import socket
import logging

# Configure logging
# Configure logging
# Configure logging
handlers = [logging.StreamHandler()]
try:
    handlers.append(logging.FileHandler('otp_debug.log'))
except OSError:
    pass  # Read-only file system (e.g., Vercel)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=handlers
)

# Try to import reportlab for PDF generation
try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Try to import pytube for video metadata fallback
try:
    from pytube import YouTube
    PYTUBE_AVAILABLE = True
except ImportError:
    PYTUBE_AVAILABLE = False

# Try to import requests for alternative metadata fetching
try:
    import requests
    from bs4 import BeautifulSoup
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Try to import yt-dlp for robust metadata fetching
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-this-in-production")

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp-relay.brevo.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 2525))
# MongoDB connection
MONGODB_URI = "mongodb+srv://harikothapalli61_db_user:Kothapalli555@cluster0.5nukjmu.mongodb.net/"
DB_NAME = os.environ.get("DB_NAME", "videoquiz_db")
try:
    if not MONGODB_URI:
        raise ValueError("MONGODB_URI environment variable not set")
        
    client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True)
    db = client[DB_NAME]
    users_collection = db.users
    quizzes_collection = db.quizzes  # Store generated quizzes
    quiz_scores_collection = db.quiz_scores  # Store user quiz attempts and scores
    chat_conversations_collection = db.chat_conversations  # Store chatbot conversations
    user_quiz_history_collection = db.user_quiz_history  # Track standard quiz generations
    custom_quizzes_collection = db.custom_quizzes  # Store custom shareable quizzes
    custom_quiz_attempts_collection = db.custom_quiz_attempts  # Store attempts for custom quizzes
    aptitude_questions_collection = db.aptitude_questions  # Store aptitude questions
    aptitude_attempts_collection = db.aptitude_attempts  # Store aptitude quiz attempts
    aptitude_practice_history_collection = db.aptitude_practice_history  # Store individual practice question attempts
    # Test connection
    client.admin.command('ping')
    MONGODB_AVAILABLE = True
except Exception as e:
    print(f"MongoDB connection error: {str(e)}")
    MONGODB_AVAILABLE = False

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_id, username, email, dsa_score=0):
        self.id = str(user_id)
        self.username = username
        self.email = email
        self.dsa_score = dsa_score

@login_manager.user_loader
def load_user(user_id):
    if not MONGODB_AVAILABLE:
        return None
    try:
        user_data = users_collection.find_one({"_id": ObjectId(user_id)})
        if user_data:
            return User(
                user_data["_id"], 
                user_data["username"], 
                user_data["email"],
                user_data.get("dsa_score", 0)
            )
    except Exception as e:
        print(f"Error loading user: {str(e)}")
    return None

# Gemini API config
# Gemini API config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCeY99Oeu2B_jYCgpp24Y5Z0LsrlTBrqKY")
GEMINI_API_KEYS = os.environ.get("GEMINI_API_KEYS")

def get_gemini_model():
    """
    Configures and returns a Gemini model instance using a random API key
    from the available pool to prevent rate limiting.
    """
    api_key = None
    
    # Try to get a random key from the list first
    if GEMINI_API_KEYS:
        keys = [k.strip() for k in GEMINI_API_KEYS.split(',') if k.strip()]
        if keys:
            api_key = random.choice(keys)
            
    # Fallback to single key if no list or list is empty
    if not api_key and GEMINI_API_KEY:
        api_key = GEMINI_API_KEY
        
    if api_key:
        try:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel('gemini-flash-latest')
        except Exception as e:
            print(f"Error configuring Gemini with key: {str(e)}")
            return None
    else:
        print("Warning: No valid GEMINI_API_KEY found. AI features will be disabled.")
        return None

# Initialize model for initial check (optional, but good for startup validation)
model = get_gemini_model()

def _get_gemini_text(response):
    """
    Safely extract plain text from a Gemini generate_content response.
    Avoids using response.text quick accessor which can fail if no Parts exist.
    Returns an empty string if no usable text is found.
    """
    if not response:
        return ""
    try:
        # Newer SDKs expose candidates/content/parts
        if getattr(response, "candidates", None):
            for cand in response.candidates:
                content = getattr(cand, "content", None)
                if not content or not getattr(content, "parts", None):
                    continue
                texts = []
                for part in content.parts:
                    # part could be a dict-like or object with .text
                    text_val = getattr(part, "text", None)
                    if text_val:
                        texts.append(text_val)
                    elif isinstance(part, dict) and part.get("text"):
                        texts.append(part["text"])
                if texts:
                    return "\n".join(texts).strip()
        # Fallback: some SDK versions still provide .text as a best-effort
        if hasattr(response, "text") and response.text:
            return str(response.text).strip()
    except Exception as e:
        print(f"Error extracting Gemini text: {str(e)}")
        # Never crash caller because of parsing issues
        return ""
    return ""

def clean_json_text(text):
    """Clean JSON text from markdown blocks and common errors."""
    if not text:
        return ""
        
    # Remove markdown code blocks
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if end > start:
            # Check if there's a language identifier like ```json
            first_line_end = text.find("\n", start)
            if first_line_end != -1 and first_line_end < end:
                text = text[first_line_end:end]
            else:
                text = text[start+3:end]
    
    text = text.strip()
    
    # Fix common JSON errors
    # 1. Remove trailing commas before closing braces/brackets
    # Matches , followed by whitespace and } or ]
    text = re.sub(r',\s*([\]}])', r'\1', text)
    
    # 2. Fix invalid escape sequences
    # We want to escape backslashes that are NOT part of a valid JSON escape sequence
    # Valid escapes: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
    text = re.sub(r'\\(?!(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))', r'\\\\', text)
    
    # 3. Remove control characters
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    # 4. Extract JSON object if it's embedded in other text
    # Find the first { and the last }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]
        
    return text

def ask_gemini(prompt):
    # Greeting/Non-technical detection
    greeting_keywords = {"hi", "hello", "welcome", "hey", "greetings"}
    cleaned = prompt.strip().lower()
    # Only consider it a greeting if it's an exact match or very short and starts with a greeting
    is_greeting = cleaned in greeting_keywords or (len(cleaned) < 10 and any(cleaned.startswith(w) for w in greeting_keywords))

    if is_greeting:
        html = "<b>Hi there! 👋</b><br>I am your Gemini chatbot. Ask me anything, request code, or type a command to get started!"
        return html

    # For technical/code/commands, enforce a strict HTML answer format and keep answers crisp.
    # The model should answer exactly what the user asked—no extra disclaimers or off-topic text—
    # and return a clean HTML fragment (no <html>, <head>, or <body> tags).
    format_prompt = (
        "You are a helpful AI assistant. Respond to the user's request on ANY topic (coding, general knowledge, creative writing, etc.).\n\n"
        "Answer style:\n"
        "- Be helpful, friendly, and direct.\n"
        "- Keep answers concise and well-structured unless the user asks for a detailed explanation.\n"
        "- If the user says words like 'detailed', 'explain step by step', or 'in depth', then you may give a longer answer.\n\n"
        "Formatting rules:\n"
        "- Output ONLY a valid HTML fragment (no <!DOCTYPE>, <html>, <head>, or <body> tags).\n"
        "- Start with a concise 1–2 sentence summary of the answer.\n"
        "- For any code, ALWAYS wrap it in <pre><code>...</code></pre>.\n"
        "- For any console/sample output, wrap it in <pre>...</pre>.\n"
        "- Use <b>, <ul>, <ol>, <li>, <p>, and <br> for structure and readability.\n"
        "- Do NOT use Markdown code fences like ```; use HTML tags only.\n"
        "- Do NOT restate the user prompt; just answer it.\n\n"
        "User request:\n"
        f"{prompt}"
    )
    model = get_gemini_model()
    if model is None:
        return "<p><b>AI Chatbot Unavailable.</b><br>Please configure the Gemini API key in settings.</p>"

    response = model.generate_content(format_prompt)
    answer = _get_gemini_text(response)
    if not answer:
        # Graceful fallback if Gemini returned no usable content
        return "<p><b>Sorry, I couldn't generate a response right now.</b><br>Please try again in a moment.</p>"
    return answer

def get_video_id(yt_url):
    match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", yt_url)
    return match.group(1) if match else None

def generate_otp(length=6):
    """Generate a random OTP."""
    return ''.join(random.choices(string.digits, k=length))

def _send_otp_email_thread(recipient_email, otp, purpose="signup"):
    """Internal function to send OTP email (runs in thread)."""
    try:
        logging.info(f"Starting email send to {recipient_email} for {purpose}")
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = recipient_email
        
        if purpose == "signup":
            msg['Subject'] = "Verify Your Email - Learnex"
            body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9;">
                    <h2 style="color: #2563eb;">Email Verification</h2>
                    <p>Hello,</p>
                    <p>Thank you for signing up for Learnex! Please use the following OTP to verify your email address and complete your account creation:</p>
                    <div style="background-color: #ffffff; border: 2px solid #2563eb; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
                        <h1 style="color: #2563eb; font-size: 32px; margin: 0; letter-spacing: 5px;">{otp}</h1>
                    </div>
                    <p>This OTP is valid for 10 minutes. Do not share this code with anyone.</p>
                    <p>If you did not create an account, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="color: #666; font-size: 12px;">This is an automated message from Learnex.</p>
                </div>
            </body>
            </html>
            """
        else:  # password reset
            msg['Subject'] = "Password Reset OTP - Learnex"
            body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9;">
                    <h2 style="color: #2563eb;">Password Reset Request</h2>
                    <p>Hello,</p>
                    <p>You have requested to reset your password for your Learnex account. Please use the following OTP to verify your identity:</p>
                    <div style="background-color: #ffffff; border: 2px solid #2563eb; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
                        <h1 style="color: #2563eb; font-size: 32px; margin: 0; letter-spacing: 5px;">{otp}</h1>
                    </div>
                    <p>This OTP is valid for 10 minutes. Do not share this code with anyone.</p>
                    <p>If you did not request a password reset, please ignore this email and your password will remain unchanged.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="color: #666; font-size: 12px;">This is an automated message from Learnex.</p>
                </div>
            </body>
            </html>
            """
        
        msg.attach(MIMEText(body, 'html'))
        text = msg.as_string()
        
        # Brevo uses port 587 with STARTTLS, but sometimes 2525 works better in cloud envs
        ports_to_try = [SMTP_PORT, 587]
        
        for port in ports_to_try:
            try:
                logging.info(f"Connecting to SMTP server {SMTP_SERVER}:{port}")
                print(f"Attempting to send email via STARTTLS (Port {port})...")
                
                server = smtplib.SMTP(SMTP_SERVER, port, timeout=20)
                server.set_debuglevel(1) 
                print(f"Connected to {SMTP_SERVER}:{port}")
                
                server.starttls()
                print("TLS started")
                
                server.login(SMTP_USERNAME, EMAIL_PASSWORD)
                print("Logged in successfully")
                
                server.sendmail(EMAIL_ADDRESS, recipient_email, text)
                server.quit()
                
                print(f"Email sent successfully to {recipient_email} via Port {port}")
                logging.info(f"Email sent successfully to {recipient_email} via Port {port}")
                return True
            except Exception as e:
                print(f"Failed to send email on port {port}: {str(e)}")
                logging.error(f"Failed to send email on port {port}: {str(e)}")
                # Continue to next port if available
                continue
        
        return False
    except Exception as e:
        print(f"Error preparing email: {str(e)}")
        logging.error(f"Error preparing email: {str(e)}")
        return False

def send_otp_email(recipient_email, otp, purpose="signup"):
    """Send OTP email synchronously (required for Vercel/Serverless)."""
    # In serverless environments like Vercel, background threads are killed
    # when the main request finishes. We must send synchronously.
    logging.info(f"Queueing email to {recipient_email}")
    return _send_otp_email_thread(recipient_email, otp, purpose)


def generate_quiz_code(length=6):
    """Generate a unique alphanumeric quiz code."""
    if not MONGODB_AVAILABLE:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        existing = custom_quizzes_collection.find_one({"code": code})
        if not existing:
            return code

def is_educational_content(content):
    """Check if the video content is educational using Gemini AI."""
    if not content or len(content.strip()) < 50:
        return False, "Insufficient content to determine if video is educational."
    
    # Sample first 2000 characters for quick analysis
    sample_content = content[:2000] if len(content) > 2000 else content
    
    educational_check_prompt = (
        "Analyze the following YouTube video content and determine if it is educational. "
        "Educational videos include: tutorials, lectures, courses, how-to guides, explanations, "
        "documentaries, science videos, history, language learning, programming tutorials, "
        "academic content, skill-building content, etc.\n\n"
        "Non-educational videos include: entertainment, music videos, vlogs, gaming streams, "
        "pranks, challenges, reaction videos, pure entertainment content, etc.\n\n"
        "Respond with ONLY a JSON object in this exact format:\n"
        '{"is_educational": true or false, "reason": "brief explanation"}\n\n'
        f"Video Content:\n{sample_content}\n\n"
        "Remember: Return ONLY the JSON object, nothing else."
    )
    
    try:
        model = get_gemini_model()
        if model is None:
            return True, "AI educational check unavailable (API key missing); allowing video."

        response = model.generate_content(educational_check_prompt)
        response_text = _get_gemini_text(response)
        if not response_text:
            # If AI returned nothing usable, fall back to heuristic check
            raise json.JSONDecodeError("empty AI response", "", 0)
        
        # Clean up response if it has markdown code blocks
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()
        elif response_text.startswith("```json"):
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        
        # Parse JSON response
        result = json.loads(response_text)
        is_educational = result.get("is_educational", False)
        reason = result.get("reason", "Unable to determine")
        
        return is_educational, reason
    except json.JSONDecodeError:
        # If JSON parsing fails, do a simple keyword check as fallback
        educational_keywords = [
            "tutorial", "learn", "course", "lesson", "explain", "how to", "guide",
            "education", "academic", "study", "teaching", "instruction", "lecture",
            "documentary", "science", "history", "mathematics", "programming", "coding",
            "skill", "knowledge", "concept", "theory", "practice", "training"
        ]
        content_lower = content.lower()
        has_educational_keywords = any(keyword in content_lower for keyword in educational_keywords)
        # Don't surface low-level errors to the user
        return has_educational_keywords, "Heuristic check based on keywords (AI analysis unavailable)."
    except Exception as e:
        error_text = str(e).lower()
        # If Gemini quota is exceeded or API unavailable, don't block the user
        if "quota" in error_text or "429" in error_text:
            # Allow the video and mark reason generically
            return True, "AI educational check temporarily unavailable due to rate limits; allowing video."

        # Generic fallback to keyword check on any other error
        educational_keywords = [
            "tutorial", "learn", "course", "lesson", "explain", "how to", "guide",
            "education", "academic", "study", "teaching", "instruction", "lecture",
            "documentary", "science", "history", "mathematics", "programming", "coding",
            "skill", "knowledge", "concept", "theory", "practice", "training"
        ]
        content_lower = content.lower()
        has_educational_keywords = any(keyword in content_lower for keyword in educational_keywords)
        return has_educational_keywords, "Heuristic check based on keywords (AI analysis unavailable)."

def get_video_metadata(video_id, yt_url):
    """Get video metadata (title, description) as fallback when transcript is not available."""
    metadata = None
    error_log = []
    print(f"get_video_metadata called for {video_id}")
    
    # Method 1: Try requests + BeautifulSoup first (more reliable, doesn't have API issues)
    if REQUESTS_AVAILABLE:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            response = requests.get(yt_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Try to get title
                title = ""
                title_tag = soup.find('meta', property='og:title')
                if title_tag:
                    title = title_tag.get('content', '').strip()
                if not title:
                    title_tag = soup.find('title')
                    if title_tag:
                        title = title_tag.text.replace(' - YouTube', '').strip()
                
                # Try to get description - multiple methods
                description = ""
                # Method 1: og:description meta tag
                desc_tag = soup.find('meta', property='og:description')
                if desc_tag:
                    description = desc_tag.get('content', '').strip()
                
                # Method 2: Find in script tags (YouTube stores it in JSON-LD or ytInitialData)
                if not description:
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if script.string:
                            script_text = script.string
                            # Try to find shortDescription
                            if 'shortDescription' in script_text:
                                # Try multiple regex patterns
                                patterns = [
                                    r'"shortDescription":"([^"]+)"',
                                    r'"shortDescription":"(.*?)"',
                                    r'shortDescription["\']?\s*:\s*["\']([^"\']+)["\']',
                                ]
                                for pattern in patterns:
                                    match = re.search(pattern, script_text, re.DOTALL)
                                    if match:
                                        desc_text = match.group(1)
                                        # Unescape common escape sequences
                                        desc_text = desc_text.replace('\\n', '\n').replace('\\"', '"').replace("\\'", "'")
                                        if len(desc_text) > 20:  # Only use if substantial
                                            description = desc_text
                                            break
                                if description:
                                    break
                            
                            # Try to find description in ytInitialData
                            if not description and 'ytInitialData' in script_text:
                                try:
                                    # Extract the JSON part
                                    start = script_text.find('var ytInitialData = ') + len('var ytInitialData = ')
                                    end = script_text.find('};', start) + 1
                                    if end > start:
                                        json_str = script_text[start:end]
                                        data = json.loads(json_str)
                                        # Navigate through the nested structure to find description
                                        if 'contents' in data:
                                            contents = data['contents']
                                            if 'twoColumnWatchNextResults' in contents:
                                                results = contents['twoColumnWatchNextResults']
                                                if 'results' in results and 'results' in results['results']:
                                                    results2 = results['results']['results']
                                                    if 'contents' in results2:
                                                        for item in results2['contents']:
                                                            if 'videoSecondaryInfoRenderer' in item:
                                                                renderer = item['videoSecondaryInfoRenderer']
                                                                if 'description' in renderer:
                                                                    desc_renderer = renderer['description']
                                                                    if 'runs' in desc_renderer:
                                                                        desc_parts = []
                                                                        for run in desc_renderer['runs']:
                                                                            if 'text' in run:
                                                                                desc_parts.append(run['text'])
                                                                        if desc_parts:
                                                                            description = '\n'.join(desc_parts)
                                                                            break
                                except:
                                    pass
                
                if title or description:
                    metadata = {
                        "title": title,
                        "description": description,
                        "length": None
                    }
            else:
                error_log.append(f"Requests: Status code {response.status_code}")
        except Exception as e:
            error_log.append(f"Requests error: {str(e)}")
            pass
    
    # Method 1.2: Try YouTube oEmbed (Very reliable for Title)
    if not metadata and REQUESTS_AVAILABLE:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={yt_url}&format=json"
            response = requests.get(oembed_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                title = data.get("title", "")
                if title:
                    metadata = {
                        "title": title,
                        "description": "Description unavailable (fetched via oEmbed)",
                        "length": None
                    }
        except Exception as e:
            error_log.append(f"oEmbed error: {str(e)}")
            pass

    # Method 1.5: Try Invidious API (good for bypassing IP blocks)
    if not metadata and REQUESTS_AVAILABLE:
        invidious_instances = [
            "https://inv.nadeko.net",
            "https://invidious.jing.rocks",
            "https://invidious.nerdvpn.de",
            "https://yt.artemislena.eu",
            "https://invidious.privacyredirect.com",
            "https://inv.tux.pizza",
            "https://vid.puffyan.us",
        ]
        for instance in invidious_instances:
            try:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                response = requests.get(api_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    title = data.get("title", "")
                    description = data.get("description", "")
                    if title or description:
                        metadata = {
                            "title": title,
                            "description": description,
                            "length": data.get("lengthSeconds")
                        }
                        break
            except Exception as e:
                error_log.append(f"Invidious ({instance}) error: {str(e)}")
                continue
        if not metadata:
            error_log.append("All Invidious instances failed")

    # Method 2: Try yt-dlp (most robust)
    if not metadata and YT_DLP_AVAILABLE:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'ignoreerrors': True,  # Don't stop on error
                'extract_flat': True,  # Faster, just get metadata
                'socket_timeout': 10,  # Prevent hanging
            }
            
            # Check for cookies env var to bypass bot detection
            cookies_content = os.environ.get("YOUTUBE_COOKIES_CONTENT")
            cookies_file = None
            if cookies_content:
                # Create a temporary file for cookies
                cookies_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt')
                cookies_file.write(cookies_content)
                cookies_file.close()
                ydl_opts['cookiefile'] = cookies_file.name
                
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(yt_url, download=False)
                if info:
                    title = info.get('title', '')
                    description = info.get('description', '')
                    if title or description:
                        metadata = {
                            "title": title,
                            "description": description,
                            "length": info.get('duration')
                        }
                else:
                    error_log.append("yt-dlp returned no info")
            
            # Clean up temp cookies file
            if cookies_file and os.path.exists(cookies_file.name):
                os.unlink(cookies_file.name)
                
        except Exception as e:
            error_log.append(f"yt-dlp error: {str(e)}")
            # Clean up temp cookies file in case of error
            if 'cookies_file' in locals() and cookies_file and os.path.exists(cookies_file.name):
                os.unlink(cookies_file.name)
            pass

    # Method 3: Try pytube as fallback (may have API issues but worth trying)
    if not metadata and PYTUBE_AVAILABLE:
        try:
            yt = YouTube(yt_url)
            title = yt.title or ""
            description = yt.description or ""
            if title or description:
                metadata = {
                    "title": title,
                    "description": description,
                    "length": yt.length if hasattr(yt, 'length') else None
                }
        except Exception as e:
            error_log.append(f"Pytube error: {str(e)}")
            pass
    
    return metadata, error_log

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route("/compiler")
@login_required
def compiler():
    return render_template("compiler.html")

@app.route("/api/execute_code", methods=["POST"])
@login_required
def execute_code():
    if not REQUESTS_AVAILABLE:
        return jsonify({"error": "Requests library not available"}), 500
        
    data = request.json
    source_code = data.get("source_code", "")
    language = data.get("language", "java")
    stdin = data.get("stdin", "")
    
    if not source_code:
        return jsonify({"error": "Source code is required"}), 400

    # Piston API Configuration (Free, No Key Required)
    PISTON_URL = "https://emkc.org/api/v2/piston/execute"
    
    # Map frontend language to Piston config
    lang_config = {
        "java": {"language": "java", "version": "15.0.2", "filename": "Main.java"},
        "python": {"language": "python", "version": "3.10.0", "filename": "main.py"},
        "c": {"language": "gcc", "version": "10.2.0", "filename": "main.c"}
    }
    
    config = lang_config.get(language, lang_config["java"])
    
    question_id = data.get("question_id")
    
    # Check for driver code if question_id is provided
    # Check for driver code if question_id is provided
    # REVERTED: User requested full code submission
    # if question_id:
    #     try:
    #         question = db.dsa_questions.find_one({"_id": ObjectId(question_id)})
    #         if question and "driver_code" in question:
    #             driver_template = question["driver_code"]
    #             # Replace placeholder with user code
    #             source_code = driver_template.replace("{{USER_CODE}}", source_code)
    #     except Exception as e:
    #         print(f"Error fetching driver code: {e}")

    payload = {
        "language": config["language"],
        "version": config["version"],
        "files": [
            {
                "name": config["filename"],
                "content": source_code
            }
        ],
        "stdin": stdin,
        "args": [],
        "compile_timeout": 10000,
        "run_timeout": 3000,
        "compile_memory_limit": -1,
        "run_memory_limit": -1
    }
    
    try:
        response = requests.post(PISTON_URL, json=payload)
        response.raise_for_status()
        result = response.json()
        
        # Piston response structure is different from Judge0
        run_stage = result.get("run", {})
        compile_stage = result.get("compile", {})
        
        # Combine output
        stdout = run_stage.get("stdout", "")
        stderr = run_stage.get("stderr", "")
        compile_output = compile_stage.get("output", "") if compile_stage.get("code", 0) != 0 else ""
        
        return jsonify({
            "stdout": stdout,
            "stderr": stderr,
            "compile_output": compile_output,
            "status": {"description": "Accepted" if run_stage.get("code") == 0 else "Error"}
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/submit_code", methods=["POST"])
@login_required
def submit_code():
    """Validate solution against test cases and award points."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
        
    data = request.json
    source_code = data.get("source_code", "")
    language = data.get("language", "java")
    question_id = data.get("question_id")
    
    if not source_code or not question_id:
        return jsonify({"error": "Missing code or question ID"}), 400
        
    # Check if already solved
    user_data = users_collection.find_one({"_id": ObjectId(current_user.id)})
    solved_questions = [str(qid) for qid in user_data.get("solved_questions", [])] if user_data else []
    
    if str(question_id) in solved_questions:
        return jsonify({"error": "You have already solved this question."}), 400
        
    try:
        # Fetch question
        question = db.dsa_questions.find_one({"_id": ObjectId(question_id)})
        if not question:
            return jsonify({"error": "Question not found"}), 404
            
        test_cases = question.get("test_cases", [])
        if not test_cases:
            return jsonify({"error": "No test cases found"}), 400
            
        # Piston Config
        PISTON_URL = "https://emkc.org/api/v2/piston/execute"
        lang_config = {
            "java": {"language": "java", "version": "15.0.2", "filename": "Main.java"},
            "python": {"language": "python", "version": "3.10.0", "filename": "main.py"},
            "c": {"language": "gcc", "version": "10.2.0", "filename": "main.c"}
        }
        config = lang_config.get(language, lang_config["java"])
        
        # Check for driver code
        # REVERTED: User requested full code submission
        # driver_code = question.get("driver_code")
        final_source_code = source_code
        # if driver_code:
        #     final_source_code = driver_code.replace("{{USER_CODE}}", source_code)
        
        results = []
        all_passed = True
        
        for i, case in enumerate(test_cases):
            payload = {
                "language": config["language"],
                "version": config["version"],
                "files": [{"name": config["filename"], "content": final_source_code}],
                "stdin": case["input"],
                "args": [],
                "compile_timeout": 10000,
                "run_timeout": 3000,
                "compile_memory_limit": -1,
                "run_memory_limit": -1
            }
            
            response = requests.post(PISTON_URL, json=payload)
            result = response.json()
            
            run_stage = result.get("run", {})
            stdout = run_stage.get("stdout", "").strip()
            expected = case["output"].strip()
            
            # Simple string comparison (can be improved)
            passed = (stdout == expected)
            if not passed:
                all_passed = False
                
            results.append({
                "case_index": i + 1,
                "passed": passed,
                "input": case["input"] if not case.get("hidden") else "Hidden",
                "expected": expected if not case.get("hidden") else "Hidden",
                "actual": stdout if not case.get("hidden") else "Hidden Output",
                "hidden": case.get("hidden", False)
            })
            
        points_awarded = 0
        new_total_score = current_user.dsa_score
        
        if all_passed:
            # Check if already solved
            user_data = users_collection.find_one({"_id": ObjectId(current_user.id)})
            solved_questions = user_data.get("solved_questions", [])
            
            if ObjectId(question_id) not in solved_questions:
                # Award points
                points = 1 if question.get("difficulty") == "Easy" else 4
                print(f"DEBUG: Awarding {points} points to {current_user.username} for question {question_id}")
                
                users_collection.update_one(
                    {"_id": ObjectId(current_user.id)},
                    {
                        "$inc": {"dsa_score": points},
                        "$push": {"solved_questions": ObjectId(question_id)}
                    }
                )
                points_awarded = points
                new_total_score += points
            else:
                print(f"DEBUG: User {current_user.username} already solved question {question_id}")
                
        return jsonify({
            "all_passed": all_passed,
            "results": results,
            "points_awarded": points_awarded,
            "new_total_score": new_total_score
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/questions", methods=["GET"])
@login_required
def get_questions():
    """Fetch all generated DSA questions."""
    if not MONGODB_AVAILABLE:
        return jsonify([]), 200
        
    try:
        # Fetch all questions including _id
        questions = list(db.dsa_questions.find({}).sort("created_at", -1))
        
        # Get user's solved questions
        user_data = users_collection.find_one({"_id": ObjectId(current_user.id)})
        solved_ids = [str(qid) for qid in user_data.get("solved_questions", [])] if user_data else []
        
        # Convert ObjectId to string for JSON serialization and add solved status
        for q in questions:
            q["_id"] = str(q["_id"])
            q["solved"] = q["_id"] in solved_ids
            
        return jsonify(questions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/login", methods=["GET", "POST"])
def login():
    if not MONGODB_AVAILABLE:
        flash("Database connection unavailable. Please check MongoDB.", "error")
        return render_template("login.html")
    
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username or not password:
            flash("Please fill in all fields.", "error")
            return render_template("login.html")
        
        # Find user by username or email (case-insensitive for email)
        user_data = users_collection.find_one({
            "$or": [
                {"username": username},
                {"email": {"$regex": f"^{re.escape(username)}$", "$options": "i"}}
            ]
        })
        
        if user_data and check_password_hash(user_data["password"], password):
            user = User(user_data["_id"], user_data["username"], user_data["email"])
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash("Invalid username/email or password.", "error")
    
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not MONGODB_AVAILABLE:
        flash("Database connection unavailable. Please check MongoDB.", "error")
        return render_template("signup.html")
    
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        # Validation
        if not username or not email or not password:
            flash("Please fill in all fields.", "error")
            return render_template("signup.html")
        
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("signup.html")
        
        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("signup.html")
        
        # Check if user already exists
        existing_user = users_collection.find_one({
            "$or": [
                {"username": username},
                {"email": email}
            ]
        })
        
        if existing_user:
            if existing_user.get("username") == username:
                flash("Username already exists.", "error")
            else:
                flash("Email already registered.", "error")
            return render_template("signup.html")
        
        # Generate OTP and store in session
        otp = generate_otp()
        hashed_password = generate_password_hash(password)
        
        session['signup_data'] = {
            "username": username,
            "email": email,
            "password": hashed_password,
            "otp": otp,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Send OTP
        if send_otp_email(email, otp, purpose="signup"):
            flash("OTP sent to your email. Please verify to complete signup.", "info")
            return redirect(url_for('verify_signup'))
        else:
            flash("Failed to send OTP email. Please try again later.", "error")
            return render_template("signup.html")
    
    return render_template("signup.html")

@app.route("/verify-signup", methods=["GET", "POST"])
def verify_signup():
    if "signup_data" not in session:
        flash("Session expired. Please sign up again.", "error")
        return redirect(url_for('signup'))
    
    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        signup_data = session.get("signup_data")
        
        if entered_otp == signup_data.get("otp"):
            # Create user
            user_data = {
                "username": signup_data["username"],
                "email": signup_data["email"],
                "password": signup_data["password"],
                "created_at": datetime.utcnow()
            }
            users_collection.insert_one(user_data)
            
            # Clear session
            session.pop("signup_data", None)
            
            flash("Account created successfully! Please log in.", "success")
            return redirect(url_for('login'))
        else:
            flash("Invalid OTP. Please try again.", "error")
            
    return render_template("verify_signup.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """Delete user account and all associated data."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        # Get user ID (handle both ObjectId and string)
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        
        user_id_str = str(current_user.id)
        
        # Delete all user data from all collections
        # 1. Delete user account
        users_collection.delete_one({"_id": user_id_obj})
        
        # 2. Delete chat conversations
        chat_conversations_collection.delete_many({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": user_id_str}
            ]
        })
        
        # 3. Delete quiz scores
        quiz_scores_collection.delete_many({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": user_id_str}
            ]
        })
        
        # 4. Delete user quiz history
        user_quiz_history_collection.delete_many({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": user_id_str}
            ]
        })
        
        # 5. Delete custom quiz attempts
        custom_quiz_attempts_collection.delete_many({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": user_id_str}
            ]
        })
        
        # 6. Delete custom quizzes owned by user
        custom_quizzes_collection.delete_many({
            "$or": [
                {"owner_id": user_id_obj},
                {"owner_id": user_id_str}
            ]
        })
        
        # 7. Delete quizzes created by user (optional - you may want to keep these)
        quizzes_collection.delete_many({
            "$or": [
                {"created_by": user_id_obj},
                {"created_by": user_id_str}
            ]
        })

        # 8. Delete aptitude attempts
        aptitude_attempts_collection.delete_many({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": user_id_str}
            ]
        })
        
        # Logout user
        logout_user()
        
        return jsonify({
            "success": True,
            "message": "Account and all associated data have been deleted successfully."
        })
    except Exception as e:
        print(f"Error deleting account: {str(e)}")
        return jsonify({"error": f"Error deleting account: {str(e)}"}), 500

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Password reset request - send OTP to email."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        
        if not email:
            flash("Please enter your email address.", "error")
            return render_template("forgot_password.html")
        
        # Check if user exists
        user_data = users_collection.find_one({"email": email})
        
        if not user_data:
            # Don't reveal if email exists or not for security
            flash("If an account exists with this email, you can reset your password.", "info")
            return render_template("forgot_password.html")
        
        # Generate OTP
        otp = generate_otp()
        
        # Store email and OTP in session
        session["reset_data"] = {
            "email": email,
            "otp": otp,
            "verified": False
        }
        
        # Send OTP
        if send_otp_email(email, otp, purpose="reset"):
            flash("OTP sent to your email. Please verify to reset password.", "info")
            return redirect(url_for('verify_reset_otp'))
        else:
            flash("Failed to send OTP email. Please try again later.", "error")
            return render_template("forgot_password.html")
    
    return render_template("forgot_password.html")

@app.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    if "reset_data" not in session:
        flash("Session expired. Please request password reset again.", "error")
        return redirect(url_for('forgot_password'))
    
    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        reset_data = session.get("reset_data")
        
        if entered_otp == reset_data.get("otp"):
            # Mark as verified
            reset_data["verified"] = True
            session["reset_data"] = reset_data
            
            flash("OTP verified. Please set your new password.", "success")
            return redirect(url_for('reset_password'))
        else:
            flash("Invalid OTP. Please try again.", "error")
            
    return render_template("verify_reset_otp.html")

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Password reset - allow password change directly."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    # Check if user has requested password reset
    # Check if user has requested password reset and verified OTP
    if "reset_data" not in session or not session["reset_data"].get("verified"):
        flash("Please verify OTP first.", "error")
        return redirect(url_for('forgot_password'))
    
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        email = session["reset_data"]["email"]
        
        if not new_password or not confirm_password:
            flash("Please fill in all fields.", "error")
            return render_template("reset_password.html")
        
        # Verify passwords match
        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html")
        
        if len(new_password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("reset_password.html")
        
        # Update password
        hashed_password = generate_password_hash(new_password)
        users_collection.update_one(
            {"email": email},
            {"$set": {"password": hashed_password}}
        )
        
        # Clear session
        # Clear session
        session.pop("reset_data", None)
        
        flash("Password has been reset successfully! Please login with your new password.", "success")
        return redirect(url_for('login'))
    
    return render_template("reset_password.html")

@app.route("/home")
@login_required
def home():
    return render_template("home.html")

@app.route("/dashboard")
@login_required
def dashboard():
    """Display user's past chats and quiz history."""
    if not MONGODB_AVAILABLE:
        flash("Database unavailable. Dashboard may be empty.", "error")
        return render_template("dashboard.html")

    try:
        # Handle user_id (ObjectId vs String)
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        
        user_id_str = str(current_user.id)
        
        # 1. Fetch Chat History
        chats = list(chat_conversations_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("timestamp", -1).limit(20))
        
        # 2. Fetch Video Quiz History (Scores)
        quizzes = list(quiz_scores_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("completed_at", -1).limit(20))
        
        # 3. Fetch Custom Quizzes Created
        custom_quizzes = list(custom_quizzes_collection.find(
            {"owner_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("created_at", -1).limit(20))
        
        # 4. Fetch Aptitude Attempts
        aptitude_quizzes = list(aptitude_attempts_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("timestamp", -1).limit(20))

        # Calculate Stats
        total_chats = chat_conversations_collection.count_documents({"user_id": {"$in": [user_id_obj, user_id_str]}})
        
        # Combine video quiz scores and aptitude scores for "Quizzes Taken" and "Avg Score"
        # Or keep them separate? The dashboard has one "Quizzes Taken" card.
        # Let's count video quizzes + aptitude attempts + custom quiz attempts (if we tracked them for the user)
        # For now, let's stick to video quizzes + aptitude for the main stats to be simple, or just video quizzes.
        # The template shows "Quizzes Taken".
        
        total_video_quizzes = quiz_scores_collection.count_documents({"user_id": {"$in": [user_id_obj, user_id_str]}})
        total_aptitude = aptitude_attempts_collection.count_documents({"user_id": {"$in": [user_id_obj, user_id_str]}})
        total_quizzes_taken = total_video_quizzes + total_aptitude
        
        # Calculate Average Score (across video quizzes and aptitude)
        total_score_pct = 0
        count_for_avg = 0
        
        for q in quizzes:
            if q.get("percentage") is not None:
                total_score_pct += q.get("percentage")
                count_for_avg += 1
                
        for a in aptitude_quizzes:
            if a.get("percentage") is not None:
                total_score_pct += a.get("percentage")
                count_for_avg += 1
                
        avg_score = round(total_score_pct / count_for_avg, 1) if count_for_avg > 0 else 0
        
        total_created_custom = custom_quizzes_collection.count_documents({"owner_id": {"$in": [user_id_obj, user_id_str]}})

        return render_template("dashboard.html", 
                             chats=chats,
                             quizzes=quizzes,
                             custom_quizzes=custom_quizzes,
                             aptitude_quizzes=aptitude_quizzes,
                             total_chats=total_chats,
                             total_quizzes=total_quizzes_taken,
                             avg_score=avg_score,
                             total_custom_quizzes=total_created_custom)
                             
    except Exception as e:
        print(f"Error loading dashboard: {str(e)}")
        flash("Error loading dashboard data.", "error")
        return render_template("dashboard.html")

@app.route("/aptitude")
@login_required
def aptitude_quiz():
    """Aptitude quiz page."""
    return render_template("aptitude.html")

@app.route("/customquiz")
@login_required
def custom_quiz_builder():
    """Custom quiz builder page (manual or AI-assisted)."""
    return render_template("customquiz.html")

@app.route("/customexam")
@login_required
def custom_quiz_exam():
    """Custom quiz exam page for students (enter code and attempt in fullscreen)."""
    return render_template("custom_exam.html")

@app.route("/api/user-chats", methods=["GET"])
@login_required
def get_user_chats():
    """Get user's past chatbot conversations."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        limit = int(request.args.get("limit", 50))
        # Try both ObjectId and string format for user_id
        try:
            user_id_obj = ObjectId(current_user.id)
            conversations = list(chat_conversations_collection.find(
                {"user_id": user_id_obj}
            ).sort("timestamp", -1).limit(limit))
        except:
            # Fallback to string if ObjectId conversion fails
            conversations = list(chat_conversations_collection.find(
                {"user_id": current_user.id}
            ).sort("timestamp", -1).limit(limit))
        
        # Convert ObjectId to string and format dates
        for conv in conversations:
            conv["_id"] = str(conv["_id"])
            if "user_id" in conv:
                conv["user_id"] = str(conv["user_id"])
            if isinstance(conv.get("timestamp"), datetime):
                conv["timestamp"] = conv["timestamp"].isoformat()
        
        print(f"Found {len(conversations)} conversations for user {current_user.id}")
        return jsonify({"conversations": conversations})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-chats/<chat_id>", methods=["DELETE"])
@login_required
def delete_user_chat(chat_id):
    """Delete a single chat conversation for the current user."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    try:
        try:
            chat_obj_id = ObjectId(chat_id)
        except Exception:
            return jsonify({"error": "Invalid chat id"}), 400

        # Support both ObjectId and string user_id formats
        try:
            user_id_obj = ObjectId(current_user.id)
        except Exception:
            user_id_obj = current_user.id

        result = chat_conversations_collection.delete_one({
            "_id": chat_obj_id,
            "user_id": {"$in": [user_id_obj, current_user.id]}
        })

        if result.deleted_count == 0:
            return jsonify({"error": "Chat not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-quizzes", methods=["GET"])
@login_required
def get_user_quizzes():
    """Get user's generated quiz history."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        limit = int(request.args.get("limit", 50))
        # Try both ObjectId and string format for user_id
        try:
            user_id_obj = ObjectId(current_user.id)
            history = list(user_quiz_history_collection.find(
                {"user_id": user_id_obj}
            ).sort("generated_at", -1).limit(limit))
        except:
            # Fallback to string if ObjectId conversion fails
            history = list(user_quiz_history_collection.find(
                {"user_id": current_user.id}
            ).sort("generated_at", -1).limit(limit))
        
        # Convert ObjectId to string and format dates
        for item in history:
            item["_id"] = str(item["_id"])
            if "quiz_id" in item:
                item["quiz_id"] = str(item["quiz_id"])
            if "user_id" in item:
                item["user_id"] = str(item["user_id"])
            if isinstance(item.get("generated_at"), datetime):
                item["generated_at"] = item["generated_at"].isoformat()
            
            # Get score if available
            try:
                user_id_obj = ObjectId(current_user.id)
                score_data = quiz_scores_collection.find_one({
                    "user_id": user_id_obj,
                    "video_id": item["video_id"],
                    "num_questions": item["num_questions"],
                    "difficulty": item["difficulty"]
                })
            except:
                score_data = quiz_scores_collection.find_one({
                    "user_id": current_user.id,
                    "video_id": item["video_id"],
                    "num_questions": item["num_questions"],
                    "difficulty": item["difficulty"]
                })
            if score_data:
                item["score"] = score_data.get("score")
                item["total_questions"] = score_data.get("total_questions")
                item["percentage"] = score_data.get("percentage")
                item["completed_at"] = score_data.get("completed_at").isoformat() if isinstance(score_data.get("completed_at"), datetime) else None
            else:
                item["score"] = None
                item["completed"] = False
        
        print(f"Found {len(history)} quizzes for user {current_user.id}")
        return jsonify({"quizzes": history})
    except Exception as e:
        print(f"Error getting user quizzes: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-custom-attempts", methods=["GET"])
@login_required
def get_user_custom_attempts():
    """Get custom quiz exam attempts made by the current user."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    try:
        try:
            user_id = ObjectId(current_user.id)
        except Exception:
            user_id = current_user.id

        attempts = list(custom_quiz_attempts_collection.find(
            {"user_id": user_id}
        ).sort("submitted_at", -1))

        payload = []
        for att in attempts:
            # Lookup quiz for title if needed
            quiz_code = att.get("quiz_code")
            quiz_title = None
            if quiz_code:
                quiz = custom_quizzes_collection.find_one({"code": quiz_code})
                if quiz:
                    quiz_title = quiz.get("title")

            submitted_at = att.get("submitted_at")
            if isinstance(submitted_at, datetime):
                submitted_at = submitted_at.isoformat()

            payload.append({
                "quiz_code": quiz_code,
                "title": quiz_title,
                "score": att.get("score"),
                "total_questions": att.get("total_questions"),
                "percentage": att.get("percentage"),
                "submitted_at": submitted_at
            })

        return jsonify({"attempts": payload})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/custom-quizzes", methods=["POST"])
@login_required
def create_custom_quiz():
    """Create a shareable custom quiz from existing quiz data or manually defined questions."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.get_json()
    quiz_data = data.get("quiz_data")
    title = data.get("title") or "Custom Quiz"
    video_url = data.get("video_url")
    num_questions = data.get("num_questions")
    difficulty = data.get("difficulty")

    if not quiz_data or not quiz_data.get("questions"):
        return jsonify({"error": "No quiz data provided"}), 400

    code = generate_quiz_code()
    try:
        owner_id = ObjectId(current_user.id)
    except Exception:
        owner_id = current_user.id

    doc = {
        "code": code,
        "owner_id": owner_id,
        "owner_username": current_user.username,
        "title": title,
        "video_url": video_url,
        "num_questions": num_questions or len(quiz_data.get("questions", [])),
        "difficulty": difficulty or "custom",
        "quiz_data": quiz_data,
        "created_at": datetime.utcnow(),
        "active": True,
        "source": "custom"  # manual or AI topic based
    }
    custom_quizzes_collection.insert_one(doc)
    return jsonify({"code": code})

@app.route("/api/customquiz/generate", methods=["POST"])
@login_required
def ai_generate_custom_quiz():
    """Use Gemini to generate quiz questions for a given topic and count."""
    data = request.get_json()
    topic = data.get("topic", "").strip()
    num_questions = data.get("num_questions", 5)
    difficulty = data.get("difficulty", "medium")

    if not topic:
        return jsonify({"error": "Please provide a topic for the quiz."}), 400

    try:
        num_questions = int(num_questions)
        if num_questions < 3 or num_questions > 20:
            return jsonify({"error": "Number of questions must be between 3 and 20."}), 400
    except (ValueError, TypeError):
        num_questions = 5

    difficulty_descriptions = {
        "easy": "Easy: Simple questions focusing on basic facts and definitions.",
        "medium": "Medium: Mix of factual and conceptual questions requiring understanding.",
        "hard": "Hard: Challenging questions requiring deep understanding and application."
    }
    difficulty_instruction = difficulty_descriptions.get(difficulty, difficulty_descriptions["medium"])

    prompt = (
        f"Create a multiple-choice quiz on the topic: '{topic}'. "
        f"Generate exactly {num_questions} questions.\n\n"
        f"Difficulty Level: {difficulty_instruction}\n\n"
        "Return ONLY valid JSON (no markdown, no prose, just JSON) in this exact format:\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "question": "Question text",\n'
        '      "options": ["Option A", "Option B", "Option C", "Option D"],\n'
        '      "correct": 0,\n'
        '      "explanation": "Short explanation of the correct answer"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        f"- Create exactly {num_questions} questions.\n"
        "- Each question MUST have 4 options.\n"
        "- 'correct' must be the index (0-3) of the correct option.\n"
        "- Questions must be directly relevant to the topic.\n"
        "- Use clear, simple language.\n"
    )

    try:
        response = model.generate_content(prompt)
        response_text = _get_gemini_text(response)
        if not response_text:
            raise json.JSONDecodeError("empty AI response", "", 0)

        response_text = clean_json_text(response_text)

        quiz_data = json.loads(response_text)
        if "questions" not in quiz_data or not quiz_data["questions"]:
            return jsonify({"error": "AI did not return any questions."}), 500

        # Trim to num_questions if extra
        quiz_data["questions"] = quiz_data["questions"][:num_questions]
        return jsonify({"quiz_data": quiz_data})
    except json.JSONDecodeError as e:
        return jsonify({
            "error": f"Failed to parse AI-generated quiz JSON: {str(e)}",
        }), 500
    except Exception as e:
        error_text = str(e)
        # If quota or similar error, surface a friendly message
        if "quota" in error_text.lower() or "429" in error_text:
            return jsonify({
                "error": "AI generation is temporarily unavailable due to rate limits. Please try again later."
            }), 503
        return jsonify({"error": str(e)}), 500

@app.route("/api/custom-quizzes/<code>", methods=["GET"])
@login_required
def fetch_custom_quiz(code):
    """Retrieve a custom quiz by code for attempting."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    quiz = custom_quizzes_collection.find_one({"code": code.upper()})
    if not quiz:
        return jsonify({"error": "Quiz code not found"}), 404

    # Only owner can access inactive quizzes
    if not quiz.get("active", True):
        try:
            owner_id = ObjectId(current_user.id)
        except Exception:
            owner_id = current_user.id
        if quiz["owner_id"] != owner_id and str(quiz["owner_id"]) != str(owner_id):
            return jsonify({"error": "This quiz is no longer accepting attempts."}), 403

    # Check if user has already attempted this quiz
    try:
        user_id = ObjectId(current_user.id)
    except Exception:
        user_id = current_user.id

    existing_attempt = custom_quiz_attempts_collection.find_one({
        "quiz_code": quiz["code"],
        "user_id": user_id
    })
    
    if existing_attempt:
        return jsonify({"error": "You have already attempted this quiz. Please ask the creator to reset your attempt if you wish to try again."}), 403

    # Return quiz data; include full questions so UI can highlight correct answers
    response = {
        "code": quiz["code"],
        "title": quiz.get("title"),
        "video_url": quiz.get("video_url"),
        "num_questions": quiz.get("num_questions"),
        "difficulty": quiz.get("difficulty"),
        "quiz_data": quiz.get("quiz_data", {})
    }
    return jsonify(response)

@app.route("/api/custom-quizzes/<code>/submit", methods=["POST"])
@login_required
def submit_custom_quiz(code):
    """Submit answers for a custom quiz attempt."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    quiz = custom_quizzes_collection.find_one({"code": code.upper()})
    if not quiz:
        return jsonify({"error": "Quiz code not found"}), 404

    # Check if quiz is active
    if not quiz.get("active", True):
        return jsonify({"error": "This quiz is not currently accepting attempts."}), 403

    data = request.get_json()
    user_answers = data.get("user_answers", {})

    questions = quiz.get("quiz_data", {}).get("questions", [])
    total_questions = len(questions)
    score = 0
    correct_answers = {}
    for idx, question in enumerate(questions):
        correct_index = question.get("correct", 0)
        correct_answers[str(idx)] = correct_index
        chosen = user_answers.get(str(idx))
        if chosen is not None and int(chosen) == int(correct_index):
            score += 1

    try:
        user_id = ObjectId(current_user.id)
    except Exception:
        user_id = current_user.id

    # Enforce single attempt per user per quiz
    existing_attempt = custom_quiz_attempts_collection.find_one({
        "quiz_code": quiz["code"],
        "user_id": user_id
    })
    if existing_attempt:
        return jsonify({"error": "You have already attempted this quiz. Only one attempt is allowed."}), 403

    attempt = {
        "quiz_code": quiz["code"],
        "quiz_id": quiz["_id"],
        "owner_id": quiz["owner_id"],
        "owner_username": quiz.get("owner_username"),
        "user_id": user_id,
        "username": current_user.username,
        "score": score,
        "total_questions": total_questions,
        "percentage": round((score / total_questions) * 100, 2) if total_questions else 0,
        "user_answers": user_answers,
        "correct_answers": correct_answers,
        "submitted_at": datetime.utcnow()
    }
    custom_quiz_attempts_collection.insert_one(attempt)

    return jsonify({
        "score": score,
        "total_questions": total_questions,
        "percentage": attempt["percentage"]
    })

@app.route("/api/custom-quizzes/<code>/attempts", methods=["GET"])
@login_required
def get_custom_quiz_attempts(code):
    """Get attempts for a custom quiz (owner only)."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    quiz = custom_quizzes_collection.find_one({"code": code.upper()})
    if not quiz:
        return jsonify({"error": "Quiz code not found"}), 404

    try:
        owner_id = ObjectId(current_user.id)
    except Exception:
        owner_id = current_user.id

    if quiz["owner_id"] != owner_id:
        # Compare stringified ObjectIds
        if str(quiz["owner_id"]) != str(owner_id):
            return jsonify({"error": "Not authorized to view attempts"}), 403

    attempts = list(custom_quiz_attempts_collection.find({"quiz_code": quiz["code"]}).sort("submitted_at", -1))
    for attempt in attempts:
        attempt["_id"] = str(attempt["_id"])
        if isinstance(attempt.get("submitted_at"), datetime):
            attempt["submitted_at"] = attempt["submitted_at"].isoformat()
        if "user_id" in attempt:
            attempt["user_id"] = str(attempt["user_id"])
    return jsonify({"attempts": attempts})

@app.route("/api/my-custom-quizzes", methods=["GET"])
@login_required
def get_my_custom_quizzes():
    """List custom quizzes created by the current user with summary stats."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    try:
        owner_id = ObjectId(current_user.id)
    except Exception:
        owner_id = current_user.id

    quizzes = list(custom_quizzes_collection.find({"owner_id": owner_id}).sort("created_at", -1))
    for quiz in quizzes:
        quiz["_id"] = str(quiz["_id"])
        quiz["owner_id"] = str(quiz["owner_id"])
        if isinstance(quiz.get("created_at"), datetime):
            quiz["created_at"] = quiz["created_at"].isoformat()
        quiz["active"] = quiz.get("active", True)

        attempts = list(custom_quiz_attempts_collection.find({"quiz_code": quiz["code"]}).sort("submitted_at", -1))
        quiz["attempts_count"] = len(attempts)
        quiz_attempts_payload = []
        for attempt in attempts[:25]:
            quiz_attempts_payload.append({
                "attempt_id": str(attempt.get("_id")),
                "username": attempt.get("username"),
                "score": attempt.get("score"),
                "total_questions": attempt.get("total_questions"),
                "percentage": attempt.get("percentage"),
                "submitted_at": attempt.get("submitted_at").isoformat() if isinstance(attempt.get("submitted_at"), datetime) else attempt.get("submitted_at")
            })
        quiz["attempts"] = quiz_attempts_payload

    return jsonify({"quizzes": quizzes})

@app.route("/api/custom-quizzes/<code>/attempts/<attempt_id>", methods=["DELETE"])
@login_required
def delete_custom_quiz_attempt(code, attempt_id):
    """Allow quiz owner to delete a student's attempt so they can re-attempt."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    quiz = custom_quizzes_collection.find_one({"code": code.upper()})
    if not quiz:
        return jsonify({"error": "Quiz code not found"}), 404

    # Only the owner of the quiz can delete attempts
    try:
        owner_id = ObjectId(current_user.id)
    except Exception:
        owner_id = current_user.id

    if quiz["owner_id"] != owner_id and str(quiz["owner_id"]) != str(owner_id):
        return jsonify({"error": "Not authorized to modify attempts for this quiz"}), 403

    # Validate attempt_id and delete the attempt
    try:
        attempt_obj_id = ObjectId(attempt_id)
    except Exception:
        return jsonify({"error": "Invalid attempt id"}), 400

    result = custom_quiz_attempts_collection.delete_one({
        "_id": attempt_obj_id,
        "quiz_code": quiz["code"]
    })

    if result.deleted_count == 0:
        return jsonify({"error": "Attempt not found"}), 404

    return jsonify({"success": True})

@app.route("/api/custom-quizzes/<code>/toggle-active", methods=["POST"])
@login_required
def toggle_custom_quiz_active(code):
    """Toggle active flag for a custom quiz (owner only)."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500

    quiz = custom_quizzes_collection.find_one({"code": code.upper()})
    if not quiz:
        return jsonify({"error": "Quiz code not found"}), 404

    try:
        owner_id = ObjectId(current_user.id)
    except Exception:
        owner_id = current_user.id

    if quiz["owner_id"] != owner_id and str(quiz["owner_id"]) != str(owner_id):
        return jsonify({"error": "Not authorized to modify this quiz"}), 403

    new_active = not quiz.get("active", True)
    custom_quizzes_collection.update_one({"_id": quiz["_id"]}, {"$set": {"active": new_active}})
    return jsonify({"active": new_active})

@app.route("/chat")
@login_required
def chat_main():
    return render_template("index.html")

@app.route("/videoquiz")
@login_required
def videoquiz():
    return render_template("videoquiz.html")

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json()
    user_message = data.get("message", "")
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    try:
        answer = ask_gemini(user_message)
        
        # Store conversation in MongoDB
        if MONGODB_AVAILABLE:
            try:
                # Store user_id as ObjectId for consistency
                try:
                    user_id_obj = ObjectId(current_user.id)
                except:
                    user_id_obj = current_user.id
                
                conversation = {
                    "user_id": user_id_obj,
                    "username": current_user.username,
                    "user_message": user_message,
                    "bot_response": answer,
                    "timestamp": datetime.utcnow()
                }
                result = chat_conversations_collection.insert_one(conversation)
                print(f"Stored conversation for user {current_user.id}, inserted_id: {result.inserted_id}")
            except Exception as e:
                print(f"Error storing conversation: {str(e)}")
        
        return jsonify({"response": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _api_videoquiz_logic():
    data = request.get_json()
    yt_url = data.get("yt_url", "").strip()
    num_questions = data.get("num_questions", 5)
    difficulty = data.get("difficulty", "medium")
    
    if not yt_url.startswith("https://www.youtube.com/watch?v=") and not yt_url.startswith("https://youtu.be/"):
        return jsonify({"error": "Please paste a full, valid YouTube video link."}), 400

    # Validate num_questions
    try:
        num_questions = int(num_questions)
        if num_questions < 3 or num_questions > 20:
            return jsonify({"error": "Number of questions must be between 3 and 20."}), 400
    except (ValueError, TypeError):
        num_questions = 5
    
    # Validate difficulty
    if difficulty not in ["easy", "medium", "hard"]:
        difficulty = "medium"

    video_id = get_video_id(yt_url)
    if not video_id:
        return jsonify({"error": "Could not extract video ID from the URL."}), 400

    # Try to get transcript first
    transcript = None
    content_source = "transcript"
    
    print(f"Attempting to fetch transcript for video_id: {video_id}")
    try:
        # Try standard static method first
        if hasattr(YouTubeTranscriptApi, 'get_transcript'):
            fetched_transcript = YouTubeTranscriptApi.get_transcript(video_id)
        else:
            # Fallback for non-standard/older versions that require instantiation
            print("YouTubeTranscriptApi.get_transcript not found. Trying instantiation...")
            yt = YouTubeTranscriptApi()
            if hasattr(yt, 'fetch'):
                fetched_transcript = yt.fetch(video_id)
            elif hasattr(yt, 'list'):
                # If list returns a TranscriptList, we might need to iterate. 
                # But based on tests, let's try to use it if fetch is missing.
                # However, fetch is more likely to be the direct equivalent.
                fetched_transcript = yt.list(video_id)
            else:
                raise ImportError("YouTubeTranscriptApi has no get_transcript, fetch, or list methods.")

        # Extract text from snippets (returns list of dicts with 'text' key)
        # Check if it's a list of dicts or something else
        if isinstance(fetched_transcript, list):
             transcript = "\n".join([item['text'] for item in fetched_transcript if 'text' in item])
        else:
             # If it's not a list of dicts, maybe it's already text or a different object?
             # Let's assume it behaves like the standard one if it returned a list
             print(f"Unexpected transcript format: {type(fetched_transcript)}")
             transcript = str(fetched_transcript)

        if transcript and transcript.strip():
            content_source = "transcript"
            print("Transcript fetched successfully.")
    except ImportError as e:
        print(f"ImportError: {str(e)}")
        # Fallthrough to metadata fallback
    except Exception as e:
        error_msg = str(e)
        print(f"Transcript fetch failed: {error_msg}")
        # Fallthrough to metadata fallback
        if "No transcripts were found" in error_msg or "TranscriptsDisabled" in error_msg or "Could not retrieve a transcript" in error_msg:
            # Fallback to video metadata
            print("Falling back to video metadata...")
            metadata, error_log = get_video_metadata(video_id, yt_url)
            if metadata and metadata.get("description"):
                # Use description and title as content
                transcript = f"Video Title: {metadata.get('title', '')}\n\nVideo Description:\n{metadata.get('description', '')}"
                content_source = "metadata"
                print("Metadata fallback successful.")
            else:
                print("Metadata fallback failed.")
                if not YT_DLP_AVAILABLE and not PYTUBE_AVAILABLE:
                    return jsonify({
                        "error": "No transcript/captions found for this video. To use videos without subtitles, please install yt-dlp or pytube."
                    }), 404
                
                # Format error log for display
                error_details = "<br>".join(error_log) if error_log else "Unknown error"
                return jsonify({
                    "error": f"No transcript/captions found, and unable to retrieve video metadata.<br>Debug details:<br>{error_details}"
                }), 404
        else:
            # Other errors - try metadata fallback
            metadata, error_log = get_video_metadata(video_id, yt_url)
            if metadata and (metadata.get("description") or metadata.get("title")):
                title = metadata.get('title', 'Video')
                description = metadata.get('description', '')
                transcript = f"Video Title: {title}\n\nVideo Description:\n{description}" if description else f"Video Title: {title}"
                content_source = "metadata"
            else:
                # Try one more time with a different approach
                metadata = get_video_metadata(video_id, yt_url)
                if metadata and (metadata.get("description") or metadata.get("title")):
                    title = metadata.get('title', 'Video')
                    description = metadata.get('description', '')
                    transcript = f"Video Title: {title}\n\nVideo Description:\n{description}" if description else f"Video Title: {title}"
                    content_source = "metadata"
                else:
                    return jsonify({
                        "error": f"Error processing video: {error_msg}<br>Unable to retrieve transcript or video metadata. Please ensure the video is public, not age-restricted, and accessible. You can try a different video or check if the video has captions enabled."
                    }), 500
    # Final check - if we still don't have content
    if not transcript or transcript.strip() == "":
        print("Transcript still empty, trying metadata again...")
        metadata, error_log = get_video_metadata(video_id, yt_url)
        if metadata and (metadata.get("description") or metadata.get("title")):
            title = metadata.get('title', 'Video')
            description = metadata.get('description', '')
            transcript = f"Video Title: {title}\n\nVideo Description:\n{description}" if description else f"Video Title: {title}"
            content_source = "metadata"
        else:
            return jsonify({
                "error": "Unable to retrieve video content. The video may be private, age-restricted, or have no available information. Please try a different video or ensure the video is public and accessible."
            }), 404

    # Check if the video is educational
    print("Checking if content is educational...")
    is_educational, reason = is_educational_content(transcript)
    if not is_educational:
        print(f"Video rejected: {reason}")
        return jsonify({
            "error": f"This video does not appear to be educational content. {reason}<br><br>Please use educational videos such as tutorials, courses, lectures, how-to guides, documentaries, or academic content."
        }), 400

    # Check if quiz already exists in MongoDB (caching)
    if MONGODB_AVAILABLE:
        cached_quiz = quizzes_collection.find_one({
            "video_id": video_id,
            "num_questions": num_questions,
            "difficulty": difficulty
        })
        if cached_quiz:
            # Return cached quiz
            quiz_data = {
                "questions": cached_quiz.get("questions", []),
                "notes": cached_quiz.get("notes", "")
            }
            return jsonify({"response": quiz_data, "cached": True})

    # Difficulty level descriptions
    difficulty_descriptions = {
        "easy": "Easy: Create simple, straightforward questions that test basic understanding and recall of key facts from the video. Use simple language and focus on main concepts.",
        "medium": "Medium: Create moderately challenging questions that require understanding of concepts, relationships, and some analysis. Mix factual recall with conceptual understanding.",
        "hard": "Hard: Create challenging questions that require deep understanding, critical thinking, analysis, and application of concepts. Include questions that test synthesis and evaluation skills."
    }
    
    difficulty_instruction = difficulty_descriptions.get(difficulty, difficulty_descriptions["medium"])
    
    # Adjust prompt based on content source
    content_description = "transcript/subtitles" if content_source == "transcript" else "video title and description"
    
    quiz_prompt = (
        f"The following is content from an educational YouTube video ({content_description}). Create a quiz with exactly {num_questions} multiple choice questions."
        f"\n\nDifficulty Level: {difficulty_instruction}"
        "\n\nIMPORTANT: Return ONLY valid JSON in this exact format (no markdown, no code blocks, just pure JSON):"
        "\n{"
        '\n  "questions": ['
        '\n    {'
        '\n      "question": "Question text here",'
        '\n      "options": ["Option A", "Option B", "Option C", "Option D"],'
        '\n      "correct": 0,'
        '\n      "explanation": "Explanation of why this answer is correct"'
        '\n    }'
        '\n  ],'
        '\n  "notes": "Study notes content (can include HTML formatting for clarity)"'
        '\n}'
        "\n\nRequirements for Questions:"
        f"\n- Create exactly {num_questions} multiple choice questions (no more, no less)"
        "\n- Each question must have exactly 4 options (A, B, C, D)"
        "\n- 'correct' is the index (0-3) of the correct option in the options array"
        "\n- Provide clear, detailed explanations for each correct answer"
        "\n- Base questions on the actual content provided below"
        "\n- Match the difficulty level specified above"
        "\n\nRequirements for Study Notes (VERY IMPORTANT - Make them EXTREMELY CLEAR):"
        "\n- The 'notes' field should contain well-organized, clear study notes"
        "\n- Use HTML formatting for better structure: <b>bold</b>, <ul><li>bullet points</li></ul>, <h3>headings</h3>, <br> for line breaks"
        "\n- Organize notes into clear sections with headings"
        "\n- Use bullet points or numbered lists for key concepts"
        "\n- Highlight important terms and definitions"
        "\n- Make it easy to scan and understand quickly"
        "\n- Include all major topics covered in the video"
        "\n- Use clear, concise language"
        "\n- Structure: Main Topic → Key Points → Important Details"
        "\n- Example structure: '<h3>Topic Name</h3><ul><li><b>Key Point:</b> Explanation</li></ul>'"
        "\n- Return ONLY the JSON object, nothing else"
        f"\n\n--- Video Content Start ---\n{transcript[:15000]}\n--- Video Content End ---"
    )
    try:
        print("Sending prompt to Gemini...")
        response = model.generate_content(quiz_prompt)
        print("Gemini response received.")
        response_text = _get_gemini_text(response)
        if not response_text:
            raise json.JSONDecodeError("empty AI response", "", 0)

        response_text = clean_json_text(response_text)
        
        # Parse JSON to validate it
        try:
            quiz_data = json.loads(response_text)
        except json.JSONDecodeError:
            # Iterative repair for "Invalid \escape" errors
            print("Initial JSON parse failed. Attempting iterative repair...")
            current_text = response_text
            repaired = False
            for attempt in range(5): # Try up to 5 repairs
                try:
                    quiz_data = json.loads(current_text)
                    repaired = True
                    print(f"JSON repaired successfully on attempt {attempt+1}")
                    break
                except json.JSONDecodeError as e:
                    if "Invalid \\escape" in str(e):
                        # e.pos points to the invalid char (e.g. 'z' in '\z')
                        # We need to escape the backslash before it (at e.pos-1)
                        # Check if e.pos-1 is indeed a backslash
                        if e.pos > 0 and current_text[e.pos-1] == '\\':
                            print(f"Repairing invalid escape at pos {e.pos-1}")
                            # Replace \ with \\
                            current_text = current_text[:e.pos-1] + "\\\\" + current_text[e.pos:]
                        else:
                            print(f"Cannot repair: char at {e.pos-1} is not backslash. Char is '{current_text[e.pos-1]}'")
                            break
                    else:
                        print(f"Cannot repair: Error is not invalid escape. {str(e)}")
                        break
            
            if not repaired:
                # One last try: just load it to raise the error for the outer block
                quiz_data = json.loads(current_text)
        
        # Store quiz in MongoDB for caching
        if MONGODB_AVAILABLE:
            try:
                quiz_doc = {
                    "video_id": video_id,
                    "video_url": yt_url,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "questions": quiz_data.get("questions", []),
                    "notes": quiz_data.get("notes", ""),
                    "created_at": datetime.utcnow(),
                    "created_by": current_user.id
                }
                result = quizzes_collection.insert_one(quiz_doc)
                quiz_id = result.inserted_id
                
                # Also store in user quiz history
                # Store user_id as ObjectId for consistency
                try:
                    user_id_obj = ObjectId(current_user.id)
                except:
                    user_id_obj = current_user.id
                
                history_doc = {
                    "user_id": user_id_obj,
                    "username": current_user.username,
                    "quiz_id": quiz_id,
                    "video_id": video_id,
                    "video_url": yt_url,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "generated_at": datetime.utcnow()
                }
                result = user_quiz_history_collection.insert_one(history_doc)
                print(f"Stored quiz history for user {current_user.id}, inserted_id: {result.inserted_id}")
            except Exception as e:
                print(f"Error storing quiz in MongoDB: {str(e)}")
        
        return jsonify({"response": quiz_data, "cached": False})
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {str(e)}")
        print(f"Raw Response Text: {response_text if 'response_text' in locals() else 'None'}")
        # If JSON parsing fails, return error with the raw response for debugging
        return jsonify({
            "error": f"Failed to parse quiz JSON. The AI response was not in valid JSON format. Error: {str(e)}",
            "raw_response": response_text[:500] if 'response_text' in locals() else "No response"
        }), 500
    except Exception as e:
        print(f"General Error in _api_videoquiz_logic: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/videoquiz", methods=["POST"])
@login_required
def api_videoquiz():
    """Wrapper for video quiz generation to ensure JSON response on crash."""
    try:
        print("Starting video quiz generation...")
        return _api_videoquiz_logic()
    except Exception as e:
        print(f"CRITICAL ERROR in api_videoquiz: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": f"Unexpected server error: {str(e)}",
            "details": "The server encountered a crash while processing the video."
        }), 500

def generate_quiz_pdf(quiz_data, video_title=None):
    """Generate a PDF with questions first, then answers and explanations at the end."""
    if not REPORTLAB_AVAILABLE:
        raise Exception("reportlab is not installed. Please install it with: pip install reportlab")
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.75*inch, bottomMargin=0.75*inch)
    
    # Container for the 'Flowable' objects
    elements = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#19977e'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#19977e'),
        spaceAfter=12,
        spaceBefore=12
    )
    
    question_style = ParagraphStyle(
        'QuestionStyle',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=8,
        spaceBefore=8,
        leftIndent=0,
        textColor=colors.black
    )
    
    option_style = ParagraphStyle(
        'OptionStyle',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=4,
        leftIndent=20,
        textColor=colors.black
    )
    
    answer_style = ParagraphStyle(
        'AnswerStyle',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6,
        leftIndent=0,
        textColor=colors.black
    )
    
    # Title
    title_text = video_title if video_title else "Quiz Questions"
    elements.append(Paragraph(title_text, title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    # Questions Section
    elements.append(Paragraph("QUESTIONS", heading_style))
    elements.append(Spacer(1, 0.1*inch))
    
    questions = quiz_data.get('questions', [])
    for i, q in enumerate(questions, 1):
        # Question text
        question_text = f"<b>Question {i}:</b> {q.get('question', '')}"
        elements.append(Paragraph(question_text, question_style))
        
        # Options
        options = q.get('options', [])
        for j, option in enumerate(options):
            option_letter = chr(65 + j)  # A, B, C, D
            option_text = f"{option_letter}. {option}"
            elements.append(Paragraph(option_text, option_style))
        
        elements.append(Spacer(1, 0.15*inch))
    
    # Page break before answers
    elements.append(PageBreak())
    
    # Answers and Explanations Section
    elements.append(Paragraph("ANSWERS AND EXPLANATIONS", heading_style))
    elements.append(Spacer(1, 0.1*inch))
    
    for i, q in enumerate(questions, 1):
        correct_index = q.get('correct', 0)
        correct_option = chr(65 + correct_index)
        correct_answer = q.get('options', [])[correct_index] if correct_index < len(q.get('options', [])) else ""
        explanation = q.get('explanation', '')
        
        # Answer
        answer_text = f"<b>Question {i}:</b> {q.get('question', '')}"
        elements.append(Paragraph(answer_text, question_style))
        
        answer_option_text = f"<b>Correct Answer: {correct_option}. {correct_answer}</b>"
        elements.append(Paragraph(answer_option_text, answer_style))
        
        # Explanation
        if explanation:
            explanation_text = f"<b>Explanation:</b> {explanation}"
            elements.append(Paragraph(explanation_text, answer_style))
        
        elements.append(Spacer(1, 0.15*inch))
    
    # Study Notes (if available)
    notes = quiz_data.get('notes', '')
    if notes:
        elements.append(PageBreak())
        elements.append(Paragraph("STUDY NOTES", heading_style))
        elements.append(Spacer(1, 0.1*inch))
        
        # Convert HTML to plain text for PDF
        import re
        from html import unescape
        
        # Remove HTML tags but preserve structure
        notes_clean = re.sub(r'<br\s*/?>', '\n', notes, flags=re.IGNORECASE)  # Convert <br> to newlines
        notes_clean = re.sub(r'</(p|div|h[1-6])>', '\n', notes_clean, flags=re.IGNORECASE)  # Convert closing tags to newlines
        notes_clean = re.sub(r'<[^>]+>', '', notes_clean)  # Remove remaining HTML tags
        notes_clean = unescape(notes_clean)  # Decode HTML entities
        notes_clean = re.sub(r'\n{3,}', '\n\n', notes_clean)  # Remove excessive newlines
        
        # Split by lines and add as paragraphs
        for line in notes_clean.split('\n'):
            if line.strip():
                # Handle bullet points
                if line.strip().startswith('•') or line.strip().startswith('-') or line.strip().startswith('*'):
                    line = '  ' + line.strip()
                elements.append(Paragraph(line.strip(), answer_style))
                elements.append(Spacer(1, 0.05*inch))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer

@app.route("/api/save-quiz-score", methods=["POST"])
@login_required
def save_quiz_score():
    """Save user's quiz score and answers to MongoDB."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        data = request.get_json()
        video_id = data.get("video_id")
        video_url = data.get("video_url")
        num_questions = data.get("num_questions")
        difficulty = data.get("difficulty")
        score = data.get("score")
        total_questions = data.get("total_questions")
        user_answers = data.get("user_answers", {})  # {question_index: selected_option_index}
        
        if not all([video_id, num_questions, difficulty, score is not None, total_questions]):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Find the quiz to get question details
        quiz = quizzes_collection.find_one({
            "video_id": video_id,
            "num_questions": num_questions,
            "difficulty": difficulty
        })
        
        if not quiz:
            return jsonify({"error": "Quiz not found"}), 404
        
        # Calculate which questions were answered correctly
        questions = quiz.get("questions", [])
        correct_answers = {}
        for idx, q in enumerate(questions):
            correct_answers[str(idx)] = q.get("correct", 0)
        
        # Save score to database
        score_doc = {
            "user_id": ObjectId(current_user.id),
            "username": current_user.username,
            "video_id": video_id,
            "video_url": video_url,
            "num_questions": num_questions,
            "difficulty": difficulty,
            "score": score,
            "total_questions": total_questions,
            "percentage": round((score / total_questions) * 100, 2) if total_questions > 0 else 0,
            "user_answers": user_answers,
            "correct_answers": correct_answers,
            "completed_at": datetime.utcnow()
        }
        
        quiz_scores_collection.insert_one(score_doc)
        
        return jsonify({
            "success": True,
            "message": "Score saved successfully",
            "score": score,
            "total": total_questions,
            "percentage": score_doc["percentage"]
        })
    except Exception as e:
        return jsonify({"error": f"Error saving score: {str(e)}"}), 500

@app.route("/api/download-quiz-pdf", methods=["POST"])
@login_required
def download_quiz_pdf():
    """Generate and download quiz as PDF."""
    if not REPORTLAB_AVAILABLE:
        return jsonify({"error": "PDF generation is not available. Please install reportlab: pip install reportlab"}), 500
    
    try:
        data = request.get_json()
        quiz_data = data.get("quiz_data")
        video_title = data.get("video_title", "Quiz")
        
        if not quiz_data or not quiz_data.get('questions'):
            return jsonify({"error": "No quiz data provided"}), 400
        
        # Generate PDF
        pdf_buffer = generate_quiz_pdf(quiz_data, video_title)
        
        # Return PDF as response
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'quiz_{video_title.replace(" ", "_")[:50]}.pdf'
        )
    except Exception as e:
        return jsonify({"error": f"Error generating PDF: {str(e)}"}), 500

@app.route("/api/aptitude/questions", methods=["GET"])
@login_required
def get_aptitude_questions():
    """Get aptitude questions by difficulty level."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        difficulty = request.args.get("difficulty", "easy")
        num_questions = int(request.args.get("num_questions", 10))
        
        if difficulty not in ["easy", "medium"]:
            difficulty = "easy"
        
        if num_questions < 1 or num_questions > 50:
            num_questions = 10
        
        # Get random questions of specified difficulty
        questions = list(aptitude_questions_collection.aggregate([
            {"$match": {"difficulty": difficulty}},
            {"$sample": {"size": num_questions}}
        ]))
        
        if not questions:
            return jsonify({
                "error": f"No {difficulty} questions available. Please generate questions first.",
                "questions": []
            }), 404
        
        # Convert ObjectId to string
        for q in questions:
            q["_id"] = str(q["_id"])
        
        return jsonify({
            "questions": questions,
            "difficulty": difficulty,
            "count": len(questions)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/attempts", methods=["GET"])
@login_required
def get_aptitude_attempts():
    """Get user's aptitude quiz attempts."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        
        attempts = list(aptitude_attempts_collection.find({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": current_user.id}
            ]
        }).sort("completed_at", -1).limit(50))
        
        for att in attempts:
            att["_id"] = str(att["_id"])
            if "user_id" in att:
                att["user_id"] = str(att["user_id"])
            if isinstance(att.get("completed_at"), datetime):
                att["completed_at"] = att["completed_at"].isoformat()
        
        return jsonify({"attempts": attempts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/stats", methods=["GET"])
@login_required
def get_aptitude_stats():
    """Get user's aptitude quiz statistics."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        
        # Get all practice attempts
        attempts = list(aptitude_practice_history_collection.find({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": current_user.id}
            ]
        }))
        
        # Calculate stats
        total_attempts = len(attempts) # Each attempt is one question
        total_questions_answered = total_attempts
        total_correct = sum(1 for att in attempts if att.get("is_correct"))
        
        # Stats by difficulty
        easy_attempts = [a for a in attempts if a.get("difficulty") == "easy"]
        medium_attempts = [a for a in attempts if a.get("difficulty") == "medium"]
        
        easy_correct = sum(1 for a in easy_attempts if a.get("is_correct"))
        easy_total = len(easy_attempts)
        easy_avg = (easy_correct / easy_total * 100) if easy_total > 0 else 0
        
        medium_correct = sum(1 for a in medium_attempts if a.get("is_correct"))
        medium_total = len(medium_attempts)
        medium_avg = (medium_correct / medium_total * 100) if medium_total > 0 else 0
        
        overall_avg = (total_correct / total_questions_answered * 100) if total_questions_answered > 0 else 0
        
        # Get total questions available
        total_questions = aptitude_questions_collection.count_documents({})
        easy_count = aptitude_questions_collection.count_documents({"difficulty": "easy"})
        medium_count = aptitude_questions_collection.count_documents({"difficulty": "medium"})
        
        # Get user data for DSA stats
        user_data = users_collection.find_one({"_id": user_id_obj})
        dsa_score = user_data.get("dsa_score", 0) if user_data else 0
        solved_questions = user_data.get("solved_questions", []) if user_data else []
        dsa_solved_count = len(solved_questions)

        return jsonify({
            "total_attempts": total_attempts,
            "total_questions_answered": total_questions_answered,
            "total_correct": total_correct,
            "overall_average": round(overall_avg, 2),
            "easy": {
                "attempts": easy_total,
                "correct": easy_correct,
                "total": easy_total,
                "average": round(easy_avg, 2),
                "available": easy_count
            },
            "medium": {
                "attempts": medium_total,
                "correct": medium_correct,
                "total": medium_total,
                "average": round(medium_avg, 2),
                "available": medium_count
            },
            "total_available": total_questions,
            "dsa_score": dsa_score,
            "dsa_solved_count": dsa_solved_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/submit", methods=["POST"])
@login_required
def submit_aptitude_quiz():
    """Submit aptitude quiz attempt."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        data = request.get_json()
        difficulty = data.get("difficulty")
        user_answers = data.get("user_answers", {})
        question_ids = data.get("question_ids", [])
        
        if not difficulty or difficulty not in ["easy", "medium", "hard"]:
            return jsonify({"error": "Invalid difficulty level"}), 400
        
        # Get questions to verify answers
        questions = list(aptitude_questions_collection.find({
            "_id": {"$in": [ObjectId(qid) for qid in question_ids]}
        }))
        
        if len(questions) != len(question_ids):
            return jsonify({"error": "Some questions not found"}), 404
        
        # Calculate score
        score = 0
        total_questions = len(questions)
        correct_answers = {}
        
        for q in questions:
            qid = str(q["_id"])
            correct_index = q.get("correct", 0)
            correct_answers[qid] = correct_index
            user_answer = user_answers.get(qid)
            if user_answer is not None and int(user_answer) == int(correct_index):
                score += 1
        
        percentage = round((score / total_questions) * 100, 2) if total_questions > 0 else 0
        
        # Save attempt
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        
        attempt = {
            "user_id": user_id_obj,
            "username": current_user.username,
            "difficulty": difficulty,
            "score": score,
            "total_questions": total_questions,
            "percentage": percentage,
            "user_answers": user_answers,
            "correct_answers": correct_answers,
            "question_ids": question_ids,
            "completed_at": datetime.utcnow()
        }
        
        aptitude_attempts_collection.insert_one(attempt)
        
        return jsonify({
            "score": score,
            "total_questions": total_questions,
            "percentage": percentage
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/generate-questions", methods=["POST"])
@login_required
def generate_aptitude_questions():
    """Generate aptitude questions using AI (admin function to seed database)."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        data = request.get_json()
        count = int(data.get("count", 100))
        difficulty = data.get("difficulty", "easy")
        
        if difficulty not in ["easy", "medium"]:
            return jsonify({"error": "Invalid difficulty"}), 400
        
        if count < 1 or count > 1000:
            return jsonify({"error": "Count must be between 1 and 1000"}), 400
        
        # Check existing count
        existing = aptitude_questions_collection.count_documents({"difficulty": difficulty})
        if existing >= 10000:
            return jsonify({"error": f"Already have {existing} {difficulty} questions. Maximum is 10000."}), 400
        
        # Generate questions in batches
        generated = []
        batch_size = 10
        
        for i in range(0, count, batch_size):
            current_batch = min(batch_size, count - i)
            
            difficulty_prompt = {
                "easy": "Easy: Simple arithmetic, basic logic, straightforward reasoning questions suitable for beginners.",
                "medium": "Medium: Moderate complexity involving problem-solving, data interpretation, and analytical thinking.",
                "hard": "Hard: Complex problems requiring advanced reasoning, multiple steps, and deep analytical skills."
            }
            
            prompt = (
                f"Generate {current_batch} aptitude test questions. "
                f"Difficulty: {difficulty_prompt.get(difficulty, difficulty_prompt['easy'])}\n\n"
                "Return ONLY valid JSON (no markdown, no prose) in this exact format:\n"
                "{\n"
                '  "questions": [\n'
                "    {\n"
                '      "question": "Question text here",\n'
                '      "options": ["Option A", "Option B", "Option C", "Option D"],\n'
                '      "correct": 0,\n'
                '      "explanation": "Brief explanation"\n'
                "    }\n"
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                f"- Generate exactly {current_batch} questions.\n"
                "- Each question must have exactly 4 options.\n"
                "- 'correct' must be index 0-3.\n"
                "- Questions should cover: quantitative aptitude, logical reasoning, verbal ability, data interpretation.\n"
            )
            
            try:
                response = model.generate_content(prompt)
                response_text = _get_gemini_text(response)
                
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                    response_text = response_text.strip()
                elif response_text.startswith("```json"):
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                
                quiz_data = json.loads(response_text)
                questions = quiz_data.get("questions", [])
                
                for q in questions:
                    q["difficulty"] = difficulty
                    q["created_at"] = datetime.utcnow()
                
                if questions:
                    aptitude_questions_collection.insert_many(questions)
                    generated.extend(questions)
                
            except Exception as e:
                print(f"Error generating batch: {str(e)}")
                continue
        
        return jsonify({
            "success": True,
            "generated": len(generated),
            "difficulty": difficulty,
            "total_in_db": aptitude_questions_collection.count_documents({"difficulty": difficulty})
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/submit-answer", methods=["POST"])
@login_required
def submit_aptitude_answer():
    """Submit a single aptitude answer."""
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    
    try:
        data = request.get_json()
        question_id = data.get("question_id")
        selected_option = data.get("selected_option")
        
        if not question_id or selected_option is None:
            return jsonify({"error": "Missing data"}), 400
            
        question = aptitude_questions_collection.find_one({"_id": ObjectId(question_id)})
        if not question:
            return jsonify({"error": "Question not found"}), 404
            
        correct_option = question.get("correct", 0)
        is_correct = int(selected_option) == int(correct_option)
        
        # Save attempt
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
            
        attempt = {
            "user_id": user_id_obj,
            "username": current_user.username,
            "question_id": ObjectId(question_id),
            "difficulty": question.get("difficulty", "easy"),
            "selected_option": int(selected_option),
            "correct_option": int(correct_option),
            "is_correct": is_correct,
            "timestamp": datetime.utcnow()
        }
        
        aptitude_practice_history_collection.insert_one(attempt)
        
        return jsonify({
            "success": True,
            "is_correct": is_correct,
            "correct_option": correct_option,
            "explanation": question.get("explanation", "")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Dashboard APIs ---

@app.route("/api/user-chats")
@login_required
def api_user_chats():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        chats = list(chat_conversations_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("timestamp", -1))
        
        for chat in chats:
            chat["_id"] = str(chat["_id"])
            chat["user_id"] = str(chat["user_id"])
            
        return jsonify({"conversations": chats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-chats/<chat_id>", methods=["DELETE"])
@login_required
def api_delete_chat(chat_id):
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        result = chat_conversations_collection.delete_one({
            "_id": ObjectId(chat_id),
            "user_id": {"$in": [user_id_obj, user_id_str]}
        })
        
        if result.deleted_count > 0:
            return jsonify({"success": True})
        return jsonify({"error": "Chat not found or unauthorized"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-quizzes")
@login_required
def api_user_quizzes():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        quizzes = list(quiz_scores_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("completed_at", -1))
        
        for q in quizzes:
            q["_id"] = str(q["_id"])
            q["user_id"] = str(q["user_id"])
            
        return jsonify({"quizzes": quizzes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-custom-attempts")
@login_required
def api_user_custom_attempts():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        attempts = list(custom_quiz_attempts_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("submitted_at", -1))
        
        for a in attempts:
            a["_id"] = str(a["_id"])
            if "user_id" in a: a["user_id"] = str(a["user_id"])
            
        return jsonify({"attempts": attempts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/my-custom-quizzes")
@login_required
def api_my_custom_quizzes():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        quizzes = list(custom_quizzes_collection.find(
            {"owner_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("created_at", -1))
        
        for q in quizzes:
            q["_id"] = str(q["_id"])
            q["owner_id"] = str(q["owner_id"])
            
            attempts = list(custom_quiz_attempts_collection.find({"quiz_code": q["code"]}))
            q["attempts_count"] = len(attempts)
            
            formatted_attempts = []
            for att in attempts:
                att["_id"] = str(att["_id"])
                formatted_attempts.append({
                    "attempt_id": str(att["_id"]),
                    "username": att.get("username", "Anonymous"),
                    "score": att.get("score", 0),
                    "total_questions": att.get("total_questions", 0),
                    "percentage": att.get("percentage", 0)
                })
            q["attempts"] = formatted_attempts
            
        return jsonify({"quizzes": quizzes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/custom-quizzes/<code_val>/toggle-active", methods=["POST"])
@login_required
def api_toggle_quiz_active(code_val):
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        quiz = custom_quizzes_collection.find_one({
            "code": code_val,
            "owner_id": {"$in": [user_id_obj, user_id_str]}
        })
        
        if not quiz:
            return jsonify({"error": "Quiz not found or unauthorized"}), 404
            
        new_status = not quiz.get("active", True)
        custom_quizzes_collection.update_one(
            {"_id": quiz["_id"]},
            {"$set": {"active": new_status}}
        )
        
        return jsonify({"success": True, "active": new_status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/custom-quizzes/<code_val>/attempts/<attempt_id>", methods=["DELETE"])
@login_required
def api_delete_custom_attempt(code_val, attempt_id):
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        quiz = custom_quizzes_collection.find_one({
            "code": code_val,
            "owner_id": {"$in": [user_id_obj, user_id_str]}
        })
        
        if not quiz:
            return jsonify({"error": "Quiz not found or unauthorized"}), 404
            
        result = custom_quiz_attempts_collection.delete_one({"_id": ObjectId(attempt_id)})
        
        if result.deleted_count > 0:
            return jsonify({"success": True})
        return jsonify({"error": "Attempt not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/stats")
@login_required
def api_aptitude_stats():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        # Get user data for DSA stats
        try:
            uid = ObjectId(current_user.id)
            user_data = users_collection.find_one({"_id": uid})
        except Exception:
            user_data = None

        solved_questions = user_data.get("solved_questions", []) if user_data else []
        
        # Get all practice attempts
        attempts = list(aptitude_practice_history_collection.find({
            "$or": [
                {"user_id": user_id_obj},
                {"user_id": current_user.id}
            ]
        }))
        
        # Calculate stats
        total_attempts = len(attempts) # Each attempt is one question
        total_questions_answered = total_attempts
        total_correct = sum(1 for att in attempts if att.get("is_correct"))
        
        # Stats by difficulty
        easy_attempts = [a for a in attempts if a.get("difficulty") == "easy"]
        medium_attempts = [a for a in attempts if a.get("difficulty") == "medium"]
        
        easy_correct = sum(1 for a in easy_attempts if a.get("is_correct"))
        easy_total = len(easy_attempts)
        easy_avg = (easy_correct / easy_total * 100) if easy_total > 0 else 0
        
        medium_correct = sum(1 for a in medium_attempts if a.get("is_correct"))
        medium_total = len(medium_attempts)
        medium_avg = (medium_correct / medium_total * 100) if medium_total > 0 else 0
        
        overall_avg = (total_correct / total_questions_answered * 100) if total_questions_answered > 0 else 0
        
        # Get total questions available
        total_questions = aptitude_questions_collection.count_documents({})
        easy_count = aptitude_questions_collection.count_documents({"difficulty": "easy"})
        medium_count = aptitude_questions_collection.count_documents({"difficulty": "medium"})
        
        print(f"DEBUG: User {current_user.username} DSA Score: {current_user.dsa_score}")
        print(f"DEBUG: User {current_user.username} Solved Questions: {solved_questions}")
        
        return jsonify({
            "total_attempts": total_attempts,
            "total_questions_answered": total_questions_answered,
            "total_correct": total_correct,
            "overall_average": round(overall_avg, 2),
            "easy": {
                "attempts": easy_total,
                "correct": easy_correct,
                "total": easy_total,
                "average": round(easy_avg, 2),
                "available": easy_count
            },
            "medium": {
                "attempts": medium_total,
                "correct": medium_correct,
                "total": medium_total,
                "average": round(medium_avg, 2),
                "available": medium_count
            },
            "total_available": total_questions,
            "dsa_score": user_data.get("dsa_score", 0) if user_data else 0,
            "dsa_solved_count": len(solved_questions)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/aptitude/attempts")
@login_required
def api_aptitude_attempts():
    if not MONGODB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        try:
            user_id_obj = ObjectId(current_user.id)
        except:
            user_id_obj = current_user.id
        user_id_str = str(current_user.id)
        
        attempts = list(aptitude_attempts_collection.find(
            {"user_id": {"$in": [user_id_obj, user_id_str]}}
        ).sort("completed_at", -1))
        
        for a in attempts:
            a["_id"] = str(a["_id"])
            a["user_id"] = str(a["user_id"])
            
        return jsonify({"attempts": attempts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500




@app.route("/test-email")
def test_email():
    """Debug route to test email sending synchronously with network diagnostics."""
    recipient_email = request.args.get("email")
    if not recipient_email:
        if current_user.is_authenticated:
            recipient_email = current_user.email
        else:
            return "Please provide email parameter: /test-email?email=your@email.com"
    
    diagnostics = {}
    
    # 1. DNS Resolution Check
    try:
        ip_list = socket.getaddrinfo(SMTP_SERVER, None)
        diagnostics["dns_resolution"] = [ip[4][0] for ip in ip_list]
    except Exception as e:
        diagnostics["dns_resolution"] = f"Failed: {str(e)}"

    # 2. Connectivity Check (Raw Socket)
    for port in [465, 587]:
        try:
            sock = socket.create_connection((SMTP_SERVER, port), timeout=5)
            diagnostics[f"port_{port}_connectivity"] = "Success"
            sock.close()
        except Exception as e:
            diagnostics[f"port_{port}_connectivity"] = f"Failed: {str(e)}"

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = recipient_email
        msg['Subject'] = "Test Email - Learnex Debug"
        body = f"This is a test email to verify SMTP configuration.\n\nDiagnostics:\n{json.dumps(diagnostics, indent=2)}"
        msg.attach(MIMEText(body, 'plain'))
        text = msg.as_string()
        
        # Attempt 1: Try SMTP_SSL on port 465
        try:
            server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=10)
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, recipient_email, text)
            server.quit()
            return jsonify({
                "success": True, 
                "message": f"Email sent successfully via Port 465 to {recipient_email}",
                "diagnostics": diagnostics
            })
        except Exception as e1:
            error1 = str(e1)
            
            # Attempt 2: Fallback to STARTTLS on port 587
            try:
                server = smtplib.SMTP(SMTP_SERVER, 587, timeout=10)
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS, recipient_email, text)
                server.quit()
                return jsonify({
                    "success": True, 
                    "message": f"Email sent successfully via Port 587 (Fallback) to {recipient_email}. Error on 465: {error1}",
                    "diagnostics": diagnostics
                })
            except Exception as e2:
                return jsonify({
                    "success": False, 
                    "error": f"Failed on both ports. Port 465 error: {error1}. Port 587 error: {str(e2)}",
                    "diagnostics": diagnostics
                }), 500
    except Exception as e:
        return jsonify({"success": False, "error": f"Setup error: {str(e)}", "diagnostics": diagnostics}), 500



@app.route("/api/generate_questions", methods=["POST"])
@login_required
def generate_questions():
    try:
        # Generate a small batch of 3 questions
        questions = generate_questions_batch(batch_size=3)
        
        if not questions:
            return jsonify({"error": "Failed to generate questions. API quota might be exceeded."}), 500
            
        count = 0
        for q in questions:
            # Check for duplicates
            if not db.dsa_questions.find_one({"title": q["title"]}):
                q["created_at"] = datetime.utcnow()
                db.dsa_questions.insert_one(q)
                count += 1
                
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
