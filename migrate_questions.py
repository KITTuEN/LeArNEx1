import google.generativeai as genai
import os
import json
import re
from pymongo import MongoClient
import certifi
from datetime import datetime
from dotenv import load_dotenv
import time
import traceback
import random

# Load environment variables
load_dotenv()

# Configuration
MONGODB_URI = "mongodb+srv://harikothapalli61_db_user:Kothapalli555@cluster0.5nukjmu.mongodb.net/"
DB_NAME = os.environ.get("DB_NAME", "videoquiz_db")
# REPLACE WITH YOUR GEMINI API KEY
# REPLACE WITH YOUR GEMINI API KEY
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
    return genai.GenerativeModel('gemini-2.0-flash-lite-preview-02-05')

def clean_json_text(text):
    if not text: return ""
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
    # Remove "json" label if present at the start
    if text.lower().startswith("json"):
        text = text[4:].strip()
    text = re.sub(r',\s*([\]}])', r'\1', text)
    return text

def upgrade_question(question):
    model = get_gemini_model()
    if not model: return None

    prompt = f"""
    I have a DSA question that needs to be updated to include specific Input/Output format sections in its description and structured test cases.
    
    Current Title: {question.get('title')}
    Current Description: {question.get('description')}
    
    Please generate a JSON object with the following fields:
    1. description: string (The updated description. It MUST include separate HTML sections for "Input Format" and "Output Format". Example: "<p>Problem...</p><h5>Input Format:</h5><p>...</p><h5>Output Format:</h5><p>...</p>")
    2. test_cases: array of objects {{input: string, output: string, hidden: boolean}}
       (Provide at least 3 test cases. Input strings MUST be raw values separated by spaces/newlines.)
    
    Ensure the JSON is valid.
    """
    
    try:
        response = model.generate_content(prompt)
        text = response.text
        cleaned_json = clean_json_text(text)
        data = json.loads(cleaned_json)
        return data
    except json.JSONDecodeError as e:
        print(f"JSON Error upgrading question '{question.get('title')}': {e}")
        # print(f"Raw text: {text}") # Uncomment for debugging
        return None
    except Exception as e:
        print(f"Error upgrading question '{question.get('title')}': {e}")
        traceback.print_exc()
        return None

def main():
    try:
        client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True)
        db = client[DB_NAME]
        collection = db.dsa_questions
        print("Connected to MongoDB.")
        
        # Find questions that need updating:
        # 1. Description missing "Input Format" OR
        # 2. Description missing "Output Format" OR
        # 3. Test cases missing or empty
        query = {
            "$or": [
                {"description": {"$not": {"$regex": "Input Format", "$options": "i"}}},
                {"description": {"$not": {"$regex": "Output Format", "$options": "i"}}},
                {"test_cases": {"$exists": False}},
                {"test_cases": {"$eq": []}}
            ]
        }
        
        questions_to_update = list(collection.find(query).limit(20)) # Process 20 at a time
        
        print(f"Found {len(questions_to_update)} questions to update.")
        
        for q in questions_to_update:
            # Double check in python to be sure
            desc = q.get('description', '')
            has_input = "Input Format" in desc
            has_output = "Output Format" in desc
            has_test_cases = q.get('test_cases') and len(q.get('test_cases')) > 0
            
            if has_input and has_output and has_test_cases:
                print(f"Skipping: {q.get('title')} - already updated.")
                continue

            print(f"Upgrading: {q.get('title')}...")
            try:
                upgraded_data = upgrade_question(q)
                
                if upgraded_data and "description" in upgraded_data:
                    update_fields = {"description": upgraded_data["description"]}
                    if "test_cases" in upgraded_data:
                        update_fields["test_cases"] = upgraded_data["test_cases"]
                        
                    collection.update_one(
                        {"_id": q["_id"]},
                        {"$set": update_fields}
                    )
                    print("  -> Success!")
                else:
                    print("  -> Failed (No data returned).")
            except Exception as e:
                 print(f"  -> Failed with error: {e}")
            
            # Rate limit
            time.sleep(15)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

