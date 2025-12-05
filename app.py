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

# ---------------------- FIELD NAMES MAPPING ---------------------- #
# Map question numbers to ACTUAL Airtable field names
QUESTION_FIELD_MAP = {
    7: "Q7 Where do you see your team growing fastest in the next 90 days?",
    8: "Q8 Social Presence Snapshot",
    9: "Q9 Content Confidence",
    10: "Q10 90-Day Definition of This WORKED",
    11: "Q11 Desired Outcome",
    12: "Q12 Why That Outcome Matters",
    13: "Q13 Weakly Schedule Reality",
    14: "Q14 Highest Energy Windows",
    15: "Q15 Commitments We Must Build Around",
    16: "Q16 What Helps You Stay Consistent?",
    17: "Q17 What Usually Pulls You Off Track?",
    18: "Q18 Stress/Discouragement Response",
    19: "Q19 Strengths You Bring",
    20: "Q20 Skill You want the MOST Help With",
    21: "Q21 System-Following Confidence",
    22: "Q22 What Would $300-$800/month Support Right Now?",
    23: "Q23 Biggest Fear or Hesitation",
    24: "Q24 If Nothing Changes in 6 Months, What Worries You Most?",
    25: "Q25 Who You Want to Become in 12 Months",
    26: "Q26 One Feeling You NEVER Want Again",
    27: "Q27 One Feeling You WANT as Your Baseline",
    28: "Q28 Preferred Accountability Style",
    29: "Q29 Preferred Tracking Style",
    30: "Q30 Why is NOW the right time to build something?"
}


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


# ---------------------- SAVE ANSWERS WITH CORRECT FIELD NAMES ---------------------- #

def save_legacy_survey_to_airtable(record_id, answers):
    """
    Maps answers to the ACTUAL Airtable field names
    """
    
    # Ensure we have exactly 24 answers (Q7-Q30)
    max_questions = 24
    padded = list(answers[:max_questions])
    
    while len(padded) < max_questions:
        padded.append("No response")
    
    # Build payload using ACTUAL field names from Airtable
    fields_payload = {}
    
    for idx, answer in enumerate(padded):
        q_number = 7 + idx  # Q7, Q8, ... Q30
        
        # Get the actual field name from our mapping
        if q_number in QUESTION_FIELD_MAP:
            field_name = QUESTION_FIELD_MAP[q_number]
            fields_payload[field_name] = answer
            print(f"📝 Setting {field_name[:30]}... = {answer[:50]}...")
        else:
            print(f"⚠️ No mapping found for Q{q_number}")
    
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
        updated_count = len(response_data.get('fields', {}))
        print(f"✅ Successfully updated {updated_count} fields")
        
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
