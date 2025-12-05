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
# These are the Deep Dive custom field slugs from GHL (no `contact.` prefix)

GHL_FIELDS = [
    "q7_where_do_you_show_up_online_right_now",        # Q7
    "q8_social_presence_snapshot",                     # Q8
    "q9_content_confidence_110",                       # Q9
    "q10_90day_definition_of_this_worked",             # Q10
    "q11_desired_outcome",                             # Q11
    "q12_why_that_outcome_matters",                    # Q12
    "q13_weekly_schedule_reality",                     # Q13
    "q14_highest_energy_windows",                      # Q14
    "q15_commitments_we_must_build_around",            # Q15
    "q16_what_helps_you_stay_consistent",              # Q16
    "q17_what_usually_pulls_you_off_track",            # Q17
    "q18_stressdiscouragement_response",               # Q18
    "q19_strengths_you_bring",                         # Q19
    "q20_skill_you_want_the_most_help_with",           # Q20
    "q21_systemfollowing_confidence_110",              # Q21
    "q22_what_would_300800month_support_right_now",    # Q22
    "q23_biggest_fear_or_hesitation",                  # Q23
    "q24_if_nothing_changes_in_6_months_what_worrie",  # Q24 (truncated in GHL)
    "q25_who_you_want_to_become_in_12_months",         # Q25
    "q26_one_feeling_you_never_want_again",            # Q26
    "q27_one_feeling_you_want_as_your_baseline",       # Q27
    "q28_preferred_accountability_style",              # Q28
    "q29_preferred_tracking_style",                    # Q29
    "q30_why_is_now_the_right_time_to_build_somethi"   # Q30 (truncated in GHL)
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
    # 1) Try by Prospect Email
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
            print("❌ Airtable lookup by email error:", e)

    # 2) Fallback by Legacy Code if provided
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
            print("❌ Airtable lookup by Legacy Code error:", e)

    print("⚠️ No matching Airtable row found.")
    return None

# ---------------------- AIRTABLE SAVE ---------------------- #

def save_legacy_survey_to_airtable(record_id, answers):
    # Normalize answer length to 24
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

# ---------------------- GHL SYNC — FIELD-BY-FIELD (WORKING PATTERN) ---------------------- #

def push_legacy_survey_to_ghl(email, answers):
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        print("⚠️ GHL disabled — missing credentials")
        return

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }

    # --- Lookup contact by email (same pattern as before) --- #
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

        first_name = contact.get("firstName") or ""
        last_name = contact.get("lastName") or ""
        full_name = f"{first_name} {last_name}".strip() or contact.get("email") or "Unknown"

        print(f"📌 Updating GHL Contact — {full_name} ({email}), ID: {contact_id}")

    except Exception as e:
        print(f"❌ GHL lookup error: {e}")
        return

    # --- Normalize answers --- #
    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    # --- Build list of {field_key: value} dicts (one per field) --- #
    field_updates = []
    for i, field_key in enumerate(GHL_FIELDS):
        if not field_key:
            continue
        field_updates.append({field_key: answers[i]})

    # --- Update each field individually, with fallback --- #
    for field_update in field_updates:
        try:
            field_name = list(field_update.keys())[0]
            field_value = field_update[field_name]

            # Primary attempt: official customField wrapper
            response = requests.put(
                f"{GHL_BASE_URL}/contacts/{contact_id}",
                headers=headers,
                json={"customField": field_update},
                timeout=20,
            )

            if response.status_code == 200:
                short_val = str(field_value)
                if len(short_val) > 30:
                    short_val = short_val[:30] + "..."
                print(f"✅ Updated {field_name}: {short_val}")
            else:
                print(f"❌ Failed to update {field_name} (status {response.status_code})")
                print(response.text[:200])

                # Fallback attempt: legacy direct payload
                alt_response = requests.put(
                    f"{GHL_BASE_URL}/contacts/{contact_id}",
                    headers=headers,
                    json=field_update,
                    timeout=20,
                )
                if alt_response.status_code == 200:
                    print(f"✅ Updated {field_name} with fallback format")
                else:
                    print(f"❌ Fallback also failed for {field_name}: {alt_response.status_code}")
                    print(alt_response.text[:200])

        except Exception as e:
            print(f"Error updating field {field_update}: {e}")
            continue

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

    # Find the existing Survey Responses row for this prospect
    record = find_survey_row(prospect_email=email)
    if not record:
        # No row found — still redirect, but nothing to update
        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

    record_id = record["id"]

    # Update Airtable Deep Dive answers
    save_legacy_survey_to_airtable(record_id, answers)

    # Update GHL Deep Dive custom fields
    push_legacy_survey_to_ghl(email, answers)

    return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
