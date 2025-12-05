from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import urllib.parse
import requests
import time  # 🔥 REQUIRED for GHL throttling

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

# ---------------------- CORRECT GHL FIELD KEYS ---------------------- #

GHL_FIELDS = [
    "q7_where_do_you_show_up_online_right_now",
    "q8_social_presence_snapshot",
    "q9_content_confidence_110",
    "q10_90day_definition_of_this_worked",
    "q11_desired_outcome",
    "q12_why_that_outcome_matters",
    "q13_weekly_schedule_reality",
    "q14_highest_energy_windows",
    "q15_commitments_we_must_build_around",
    "q16_what_helps_you_stay_consistent",
    "q17_what_usually_pulls_you_off_track",
    "q18_stressdiscouragement_response",
    "q19_strengths_you_bring",
    "q20_skill_you_want_the_most_help_with",
    "q21_systemfollowing_confidence_110",
    "q22_what_would_300800month_support_right_now",
    "q23_biggest_fear_or_hesitation",
    "q24_if_nothing_changes_in_6_months_what_worrie",
    "q25_who_you_want_to_become_in_12_months",
    "q26_one_feeling_you_never_want_again",
    "q27_one_feeling_you_want_as_your_baseline",
    "q28_preferred_accountability_style",
    "q29_preferred_tracking_style",
    "q30_why_is_now_the_right_time_to_build_somethi"
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
    # 1) Try by email
    if prospect_email:
        formula = f"{{Prospect Email}} = '{prospect_email}'"
        url = _airtable_url(SURVEY_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            recs = r.json().get("records", [])
            if recs:
                return recs[0]
        except Exception as e:
            print("❌ Airtable lookup error:", e)

    # 2) Try by Legacy Code
    if legacy_code:
        formula = f"{{Legacy Code}} = '{legacy_code}'"
        url = _airtable_url(SURVEY_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            recs = r.json().get("records", [])
            if recs:
                return recs[0]
        except Exception as e:
            print("❌ Airtable lookup error:", e)

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

# ---------------------- GHL SYNC — FIELD-BY-FIELD WITH THROTTLING ---------------------- #

def push_legacy_survey_to_ghl(email, answers):
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        print("⚠️ Missing GHL credentials")
        return

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }

    # --- Lookup contact --- #
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

        print(f"📌 Updating GHL contact ID: {contact_id}")

    except Exception as e:
        print(f"❌ GHL lookup error: {e}")
        return

    # Normalize Deep Dive answers
    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    # Prepare individual field updates
    field_updates = []
    for i, field_key in enumerate(GHL_FIELDS):
        field_updates.append({field_key: answers[i]})

    # --- Update each field with throttle --- #
    for field_update in field_updates:
        try:
            field_name = list(field_update.keys())[0]
            field_value = field_update[field_name]

            # Primary attempt
            response = requests.put(
                f"{GHL_BASE_URL}/contacts/{contact_id}",
                headers=headers,
                json={"customField": field_update},
                timeout=20,
            )

            if response.status_code == 200:
                print(f"✅ Updated {field_name}")
            else:
                print(f"❌ Failed primary update for {field_name} (status {response.status_code})")

                # Fallback attempt
                alt_response = requests.put(
                    f"{GHL_BASE_URL}/contacts/{contact_id}",
                    headers=headers,
                    json=field_update,
                    timeout=20,
                )

                if alt_response.status_code == 200:
                    print(f"✅ Fallback succeeded for {field_name}")
                else:
                    print(f"❌ Fallback failed for {field_name} (status {alt_response.status_code})")

            # 🔥 REQUIRED: prevent GHL 429 rate-limit failures
            time.sleep(0.5)

        except Exception as e:
            print(f"❌ Error updating field {field_update}: {e}")

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
    push_legacy_survey_to_ghl(email, answers)

    return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
