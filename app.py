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

SURVEY_TABLE = "Survey Responses"  # Hard-coded for stability

LEGACY_SURVEY_REDIRECT_URL = (
    os.getenv("LEGACY_SURVEY_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/activation"
)

# ---------------------- GHL CONFIG ---------------------- #

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

# ---------------------- EXACT FIELD NAMES (UNCHANGED) ---------------------- #

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

# ---------------------- GHL FIELD KEYS (RESTORED FROM YOUR PROJECT FILES) ---------------------- #

GHL_FIELDS = [
    "q7__where_do_you_show_up_online_right_now_",
    "q8__social_presence_snapshot",
    "q9__content_confidence",
    "q10__90_day_definition_of_this_worked",
    "q11__desired_outcome",
    "q12__why_that_outcome_matters",
    "q13__weekly_schedule_reality",
    "q14__highest_energy_windows",
    "q15__commitments_we_must_build_around",
    "q16__what_helps_you_stay_consistent",
    "q17__what_usually_pulls_you_off_track",
    "q18__stress_discouragement_response",
    "q19__strengths_you_bring",
    "q20__skill_you_want_the_most_help_with",
    "q21__system_following_confidence",
    "q22__what_would_300_800_month_support_right_now_",
    "q23__biggest_fear_or_hesitation",
    "q24__if_nothing_changes_in_6_months_what_worries_you_most_",
    "q25__who_you_want_to_become_in_12_months",
    "q26__one_feeling_you_never_want_again",
    "q27__one_feeling_you_want_as_your_baseline",
    "q28__preferred_accountability_style",
    "q29__preferred_tracking_style",
    "q30__why_is_now_the_right_time_to_build_something_"
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

# ---------------------- AIRTABLE LOOKUP (UNCHANGED) ---------------------- #

def find_survey_row(prospect_email=None, legacy_code=None):

    if prospect_email:
        formula = f"{{Prospect Email}} = '{prospect_email}'"
        url = _airtable_url(SURVEY_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            recs = r.json().get("records", [])
            if recs:
                print("✅ Email lookup success")
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
                print("✅ Legacy Code lookup success")
                return recs[0]
        except:
            pass

    print("⚠️ No matching Airtable row found.")
    return None


# ---------------------- SAVE ANSWERS (UNCHANGED) ---------------------- #

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


# ---------------------- GHL SYNC (RESTORED PROPERLY) ---------------------- #

def push_legacy_survey_to_ghl(email, answers):

    if not GHL_API_KEY or not GHL_LOCATION_ID:
        print("⚠️ GHL disabled — missing env vars")
        return

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }

    # 1) Lookup contact
    lookup_url = f"{GHL_BASE_URL}/contacts/?email={urllib.parse.quote(email)}&locationId={GHL_LOCATION_ID}"

    try:
        r = requests.get(lookup_url, headers=headers, timeout=20)
        r.raise_for_status()
        contacts = r.json().get("contacts", [])
        if not contacts:
            print("⚠️ No GHL contact found")
            return
        contact_id = contacts[0]["id"]
        print("📌 GHL contact:", contact_id)

    except Exception as e:
        print("❌ GHL lookup error:", e)
        return

    # 2) Ensure answer count
    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    # 3) Build update payload
    custom_fields = []

    for i in range(24):
        custom_fields.append({
            "id": GHL_FIELDS[i],
            "value": answers[i]
        })

    payload = {"customField": custom_fields}

    # 4) Update GHL contact
    try:
        update_url = f"{GHL_BASE_URL}/contacts/{contact_id}"
        r = requests.put(update_url, headers=headers, json=payload, timeout=20)
        r.raise_for_status()
        print("✅ GHL updated")
    except Exception as e:
        print("❌ GHL update error:", e)


# ---------------------- ROUTES (UNCHANGED EXCEPT ONE CALL) ---------------------- #

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

    # ⚡ ONLY NEW LINE ADDED — NOTHING ELSE TOUCHED
    push_legacy_survey_to_ghl(email, answers)

    return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
