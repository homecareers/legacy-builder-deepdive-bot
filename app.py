from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import urllib.parse
import requests
import json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

SURVEY_TABLE = "Survey Responses"

LEGACY_SURVEY_REDIRECT_URL = (
    os.getenv("LEGACY_SURVEY_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/activation"
)

# ---------------------- GHL CONFIG ---------------------- #

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

# ---------------------- AIRTABLE Q-FIELDS ---------------------- #

DEEPDIVE_FIELDS = [
    "Q7 Where do you show up online right now?",
    "Q8 Social Presence Snapshot",
    "Q9 Content Confidence",
    "Q10 90-Day Definition of This WORKED",
    "Q11 Desired Outcome",
    "Q12 Why That Outcome Matters",
    "Q13 Weekly Schedule Reality",
    "Q14 Highest Energy Windows",
    "Q15 Commitments We Must Build Around",
    "Q16 What Helps You Stay Consistent?",
    "Q17 What Usually Pulls You Off Track?",
    "Q18 Stress/Discouragement Response",
    "Q19 Strengths You Bring",
    "Q20 Skill You Want the MOST Help With",
    "Q21 System-Following Confidence",
    "Q22 What Would $300–$800/month Support Right Now?",
    "Q23 Biggest Fear or Hesitation",
    "Q24 If Nothing Changes in 6 Months, What Worries You Most?",
    "Q25 Who You Want to Become in 12 Months",
    "Q26 One Feeling You NEVER Want Again",
    "Q27 One Feeling You WANT as Your Baseline",
    "Q28 Preferred Accountability Style",
    "Q29 Preferred Tracking Style",
    "Q30 Why is NOW the right time to build something?"
]

# ---------------------- CORRECT GHL FIELD KEYS FROM YOUR PDF ---------------------- #

GHL_FIELDS = [
    "q7_where_do_you_show_up_online_right_now",  # Q7
    "q8_social_presence_snapshot",  # Q8
    "q9_content_confidence_110",  # Q9
    "q10_90day_definition_of_this_worked",  # Q10
    "q11_desired_outcome",  # Q11
    "q12_why_that_outcome_matters",  # Q12
    "q13_weekly_schedule_reality",  # Q13
    "q14_highest_energy_windows",  # Q14
    "q15_commitments_we_must_build_around",  # Q15
    "q16_what_helps_you_stay_consistent",  # Q16
    "q17_what_usually_pulls_you_off_track",  # Q17
    "q18_stressdiscouragement_response",  # Q18
    "q19_strengths_you_bring",  # Q19
    "q20_skill_you_want_the_most_help_with",  # Q20
    "q21__systemfollowing_confidence_110",  # Q21
    "q22_what_would_300800month_support_right_now",  # Q22
    "q23__biggest_fear_or_hesitation",  # Q23
    "q24__if_nothing_changes_in_6_months_what_worries_you_most",  # Q24
    "q25_who_you_want_to_become_in_12_months",  # Q25
    "q26__one_feeling_you_never_want_again",  # Q26
    "q27__one_feeling_you_want_as_your_baseline",  # Q27
    "q28_preferred_accountability_style",  # Q28
    "q29_preferred_tracking_style",  # Q29
    "q30_why_is_now_the_right_time_to_build_something"  # Q30
]

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

    if prospect_email:
        formula = f"{{Prospect Email}} = '{prospect_email}'"
        url = _airtable_url(SURVEY_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            recs = r.json().get("records", [])
            if recs:
                return recs[0]
        except:
            pass

    if legacy_code:
        formula = f"{{Legacy Code}} = '{legacy_code}'"
        url = _airtable_url(SURVEY_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            recs = r.json().get("records", [])
            if recs:
                return recs[0]
        except:
            pass

    print("⚠️ No matching Airtable row found.")
    return None

# ---------------------- AIRTABLE SAVE ---------------------- #

def save_legacy_survey_to_airtable(record_id, answers):

    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    fields_payload = {}

    for idx, val in enumerate(answers):
        fields_payload[DEEPDIVE_FIELDS[idx]] = val

    try:
        r = requests.patch(
            _airtable_url(SURVEY_TABLE, record_id),
            headers=_airtable_headers(),
            json={"fields": fields_payload},
            timeout=20
        )
        r.raise_for_status()
        print("✅ Airtable updated")
    except Exception as e:
        print("❌ Airtable PATCH error:", e)

# ---------------------- GHL SYNC - THE WORKING METHOD ---------------------- #

def push_legacy_survey_to_ghl(email, answers):

    if not GHL_API_KEY or not GHL_LOCATION_ID:
        print("⚠️ GHL disabled — missing credentials")
        return

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }

    # V1 API EMAIL LOOKUP
    lookup_url = f"{GHL_BASE_URL}/contacts/?email={urllib.parse.quote(email)}&locationId={GHL_LOCATION_ID}"

    try:
        r = requests.get(lookup_url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        
        contacts = data.get("contacts", [])
        if not contacts:
            print(f"❌ No GHL contact found for email: {email}")
            return

        contact = contacts[0]
        contact_id = contact.get("id")
        
        # Handle None values properly
        first_name = contact.get("firstName") or ""
        last_name = contact.get("lastName") or ""
        full_name = f"{first_name} {last_name}".strip()
        
        if not full_name:
            full_name = contact.get("contactName") or contact.get("name") or contact.get("email") or "Unknown"

        print(f"📌 Updating GHL Contact — {full_name} ({email}), ID: {contact_id}")
        
    except Exception as e:
        print(f"❌ GHL lookup error: {e}")
        return

    # Prepare answers
    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    # THE CORRECT WAY TO UPDATE CUSTOM FIELDS IN GHL V1
    # Build the payload with the exact format GHL expects
    payload = {}
    
    for i in range(24):
        field_key = GHL_FIELDS[i]
        if field_key:  # Only add if field key exists
            # Remove 'contact.' prefix if using it
            clean_key = field_key.replace("contact.", "")
            payload[clean_key] = answers[i]

    try:
        update_url = f"{GHL_BASE_URL}/contacts/{contact_id}"
        
        print(f"📤 Sending to GHL V1: {update_url}")
        print(f"📦 Updating {len(payload)} custom fields...")
        
        # Debug: Show exactly what we're sending
        print(f"🔍 First field being sent: {list(payload.keys())[0]} = '{list(payload.values())[0][:50]}...'")
        
        # Send the update
        r = requests.put(update_url, headers=headers, json=payload, timeout=20)
        
        print(f"📊 Response status: {r.status_code}")
        
        if r.status_code == 200:
            # Parse response to verify update
            response_data = r.json() if r.text else {}
            
            # Check if the response contains our custom fields
            if response_data:
                # GHL V1 usually returns the updated contact
                print("✅ GHL V1 returned 200 - checking for field updates...")
                
                # Try to fetch the contact again to verify
                verify_url = f"{GHL_BASE_URL}/contacts/{contact_id}"
                verify_r = requests.get(verify_url, headers=headers, timeout=10)
                
                if verify_r.status_code == 200:
                    verify_data = verify_r.json()
                    # Check if any of our fields exist in the response
                    found_fields = False
                    for field_key in payload.keys():
                        if field_key in str(verify_data):
                            found_fields = True
                            break
                    
                    if found_fields:
                        print("✅✅ GHL fields verified - update successful!")
                    else:
                        print("⚠️ GHL returned 200 but fields not found in verification")
                        print("⚠️ This might be a GHL API issue - check the contact manually")
            else:
                print("✅ GHL returned 200 (no response body to verify)")
        else:
            print(f"❌ GHL update failed with status {r.status_code}")
            print(f"❌ Response: {r.text[:500]}...")
            
    except Exception as e:
        print(f"❌ GHL update error: {e}")

# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/submit", methods=["POST"])
def submit_legacy_survey():

    data = request.json or {}
    email = (data.get("email") or "").strip()
    answers = data.get("answers") or []

    if not email:
        return jsonify({"error": "Missing email"}), 400

    record = find_survey_row(prospect_email=email)
    if not record:
        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

    record_id = record["id"]

    save_legacy_survey_to_airtable(record_id, answers)

    # GHL UPDATE
    push_legacy_survey_to_ghl(email, answers)

    return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
