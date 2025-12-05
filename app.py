from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import urllib.parse
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

# Hard-coded to eliminate naming mismatch
SURVEY_TABLE = "Survey Responses"

LEGACY_SURVEY_REDIRECT_URL = (
    os.getenv("LEGACY_SURVEY_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/activation"
)


# ---------------------- HELPERS ---------------------- #

def _airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }


def _airtable_url(table, record_id=None, params=None):
    base = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{urllib.parse.quote(table)}"
    if record_id:
        return f"{base}/{record_id}"
    if params:
        return f"{base}?{urllib.parse.urlencode(params)}"
    return base


# ---------------------- AIRTABLE LOOKUP ---------------------- #

def find_survey_row(prospect_email=None, legacy_code=None):

    # 1️⃣ ALWAYS lookup by email first
    if prospect_email:
        formula = f"{{Prospect Email}} = '{prospect_email}'"
        print(f"🔍 Attempting email lookup: {formula}")

        url = _airtable_url(SURVEY_TABLE, params={
            "filterByFormula": formula,
            "maxRecords": 1,
            "pageSize": 1,
        })

        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            records = r.json().get("records", [])
            if records:
                print("✅ Email lookup successful")
                return records[0]
        except Exception as e:
            print(f"❌ Airtable email lookup error: {e}")

    # 2️⃣ Optional fallback by Legacy Code (just in case)
    if legacy_code:
        formula = f"{{Legacy Code}} = '{legacy_code}'"
        url = _airtable_url(SURVEY_TABLE, params={
            "filterByFormula": formula,
            "maxRecords": 1,
            "pageSize": 1,
        })

        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            records = r.json().get("records", [])
            if records:
                print("✅ Legacy Code lookup successful")
                return records[0]
        except Exception as e:
            print(f"❌ Airtable legacy_code lookup error: {e}")

    print("⚠️ No matching Survey Responses row found.")
    return None


# ---------------------- SAVE ANSWERS (SIMPLIFIED) ---------------------- #

def save_legacy_survey_to_airtable(record_id, answers):
    """
    SIMPLIFIED VERSION: Uses field names directly instead of field IDs
    """
    
    # Ensure we have exactly 24 answers (Q7-Q30)
    max_questions = 24
    padded = list(answers[:max_questions])
    
    while len(padded) < max_questions:
        padded.append("No response")
    
    # Build payload using field names directly
    fields_payload = {}
    
    for idx, answer in enumerate(padded):
        q_number = 7 + idx  # Q7, Q8, ... Q30
        field_name = f"Q{q_number}"
        fields_payload[field_name] = answer
        print(f"📝 Setting {field_name} = {answer[:50]}...")  # Log first 50 chars
    
    print(f"📡 FINAL PAYLOAD: {len(fields_payload)} fields")
    
    try:
        r = requests.patch(
            _airtable_url(SURVEY_TABLE, record_id),
            headers=_airtable_headers(),
            json={"fields": fields_payload},
            timeout=20,
        )
        r.raise_for_status()
        print(f"✅ Airtable PATCH success for record {record_id}")
        
        # Log the response for debugging
        response_data = r.json()
        print(f"✅ Updated fields: {list(response_data.get('fields', {}).keys())}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP ERROR: {e}")
        print(f"❌ Response: {e.response.text if e.response else 'No response'}")
    except Exception as e:
        print(f"❌ ERROR: Airtable PATCH failed: {e}")


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit_legacy_survey():

    try:
        data = request.json or {}
        email = (data.get("email") or "").strip()
        answers = data.get("answers") or []
        
        print(f"📨 Received submission: email={email}, answers count={len(answers)}")

        if not email:
            return jsonify({"error": "Missing email"}), 400

        record = find_survey_row(prospect_email=email)
        if not record:
            print("❌ No row found — redirecting anyway.")
            return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

        record_id = record["id"]
        print(f"📌 Found record ID: {record_id}")

        save_legacy_survey_to_airtable(record_id, answers)

        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

    except Exception as e:
        print(f"❌ Legacy Survey submit route error: {e}")
        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
