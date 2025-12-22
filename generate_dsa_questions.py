import google.generativeai as genai
import os
import json
import re
from pymongo import MongoClient
import certifi
from datetime import datetime
from dotenv import load_dotenv
import time
import random

# Load environment variables
load_dotenv()

# Configuration
MONGODB_URI = "mongodb+srv://harikothapalli61_db_user:Kothapalli555@cluster0.5nukjmu.mongodb.net/"
DB_NAME = os.environ.get("DB_NAME", "videoquiz_db")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_API_KEYS = os.environ.get("GEMINI_API_KEYS")

def get_gemini_model():
    api_key = GEMINI_API_KEY
    if not api_key and GEMINI_API_KEYS:
        keys = [k.strip() for k in GEMINI_API_KEYS.split(',') if k.strip()]
        if keys:
            api_key = random.choice(keys)

    if not api_key:
        print("Error: GEMINI_API_KEY not found.")
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-flash-latest')

def clean_json_text(text):
    """Clean JSON text from markdown blocks and common errors."""
    if not text:
        return ""
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if end > start:
            first_line_end = text.find("\n", start)
            if first_line_end != -1 and first_line_end < end:
                text = text[first_line_end:end]
            else:
                text = text[start+3:end]
    text = text.strip()
    text = re.sub(r',\s*([\]}])', r'\1', text)
    return text

def generate_questions_batch(batch_size=5):
    model = get_gemini_model()
    if not model:
        return []

    prompt = f"""
    Generate {batch_size} unique Data Structures and Algorithms (DSA) coding problems.
    Topics: Arrays, Number Sequences, Strings.
    Difficulty: Mix of Easy and Medium.
    
    Format: JSON Array of objects with these fields:
    - title: string
    - description: string (html allowed for formatting). 
      IMPORTANT: The description MUST include separate sections for "Input Format" and "Output Format".
      Example format for description:
      "<p>Problem statement...</p><h5>Input Format:</h5><p>...</p><h5>Output Format:</h5><p>...</p>"
    - difficulty: "Easy" or "Medium"
    - topic: string
    - test_cases: array of objects {{input: string, output: string, hidden: boolean}}
      (Provide at least 3 test cases per problem, 1 hidden)
      IMPORTANT: Input should be raw values separated by spaces/newlines, suitable for stdin reading.
    
    Ensure the JSON is valid.
    """
    try:
        print("Requesting Gemini...")
        response = model.generate_content(prompt)
        text = response.text
        cleaned_json = clean_json_text(text)
        questions = json.loads(cleaned_json)
        return questions
    except Exception as e:
        print(f"Error generating questions: {e}")
        return []

def main():
    # Connect to MongoDB
    try:
        client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True)
        db = client[DB_NAME]
        collection = db.dsa_questions
        print("Connected to MongoDB Atlas.")
    except Exception as e:
        print(f"MongoDB connection error: {e}")
        return

    target_count = 1000
    batch_size = 5
    
    # Check existing count
    current_count = collection.count_documents({})
    print(f"Found {current_count} existing questions in database.")
    
    if current_count >= target_count:
        print(f"Target of {target_count} questions already reached!")
        return

    print(f"Resuming generation to reach {target_count} questions...")

    while current_count < target_count:
        questions = generate_questions_batch(batch_size)
        if not questions:
            print("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        inserted_count = 0
        for q in questions:
            # Check for duplicates
            if not collection.find_one({"title": q["title"]}):
                q["created_at"] = datetime.utcnow()
                collection.insert_one(q)
                inserted_count += 1
        
        current_count += inserted_count
        print(f"Batch complete. Inserted: {inserted_count}. Total: {current_count}/{target_count}")
        
        # Rate limit protection
        time.sleep(2)

if __name__ == "__main__":
    main()
