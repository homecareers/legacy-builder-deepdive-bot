from flask import Flask, request, jsonify, render_template
import requests
import os
import datetime
import urllib.parse
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

# ✔️ One-table architecture — all survey data lives in Survey Responses
HQ_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Survey Responses"
USERS_TABLE = os.getenv("AIRTABLE_USERS_TABLE") or "Users"

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

DEEPDIVE_REDIRECT_URL = (
    os.getenv("DEEPDIVE_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/nextstep"
)

# Deep Dive 2 = 24 questions (Q7–Q30)
DEEPDIVE_QUESTION_COUNT = int(os.getenv("DEEPDIVE_QUESTION_COUNT", "24"))


# ---------------------- AIRTABLE HELPERS ---------------------- #

def _h():
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }

def _url(table, rec_id=None, params=None):
    base = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{urllib.parse.quote(table)}"
    if rec_id:
        return f"{base}/{rec_id}"
    if params:
        return f"{base}?{urllib.parse.urlencode(params)}"
    return base


# ---------------------- OPERATOR LOOKUP ---------------------- #

def get_operator_info(ghl_user_id: str):
    try:
        formula = f"{{GHL User ID}} = '{ghl_user_id}'"
        search_url = _url(
            USERS_TABLE,
            params={"filterByFormula": formula, "maxRecords": 1}
        )
        r = requests.get(search_url, headers=_h())
        r.raise_for_status()
        data = r.json()

        if data.get("records"):
            fields = data["records"][0].get("fields", {})
            return fields.get("Legacy Code"), fields.get("Email")

    except Exception as e:
        print(f"Error looking up operator info: {e}")

    return None, None


def update_prospect_with_operator_info(prospect_id: str, ghl_user_id: str):
    try:
        update_fields = {"GHL User ID": ghl_user_id}

        op_legacy_code, op_email = get_operator_info(ghl_user_id)
        if op_legacy_code:
            update_fields["Assigned Op Legacy Code"] = op_legacy_code
        if op_email:
            update_fields["Assigned Op Email"] = op_email

        requests.patch(
            _url(HQ_TABLE, prospect_id),
            headers=_h(),
            json={"fields": update_fields},
        )

    except Exception as e:
        print(f"Error updating prospect with operator info: {e}")


# ---------------------- PROSPECT HANDLING ---------------------- #

def get_or_create_prospect(email: str):

    formula = f"{{Prospect Email}} = '{email}'"
    search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})

    r = requests.get(search_url, headers=_h())
    r.raise_for_status()
    data = r.json()

    # ✔️ Record exists
    if data.get("records"):
        rec = data["records"][0]
        rec_id = rec["id"]
        fields = rec.get("fields", {})
        legacy_code = fields.get("Legacy Code")

        if not legacy_code:
            auto = fields.get("AutoNum")
            if auto is None:
                auto_data = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json()
                auto = auto_data.get("fields", {}).get("AutoNum")

            legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
            requests.patch(
                _url(HQ_TABLE, rec_id),
                headers=_h(),
                json={"fields": {"Legacy Code": legacy_code}},
            )

        return legacy_code, rec_id

    # ❗ Record does NOT exist, create it
    payload = {"fields": {"Prospect Email": email}}
    r = requests.post(_url(HQ_TABLE), headers=_h(), json=payload)
    r.raise_for_status()
    rec = r.json()
    rec_id = rec["id"]

    # Assign legacy code
    auto = rec.get("fields", {}).get("AutoNum")
    if auto is None:
        auto_data = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json()
        auto = auto_data.get("fields", {}).get("AutoNum")

    legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
    requests.patch(
        _url(HQ_TABLE, rec_id),
        headers=_h(),
        json={"fields": {"Legacy Code": legacy_code}},
    )

    return legacy_code, rec_id


# ---------------------- SAVE DEEP DIVE — exact Airtable fields ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):

    # EXACT Airtable field names, your structure
    deepdive_fields = [
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

    fields = {
        "Legacy Code": legacy_code,
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    # Map answers → exact Airtable fields
    for idx, value in enumerate(answers):
        fields[deepdive_fields[idx]] = value

    # PATCH the existing row (critical!)
    r = requests.patch(
        _url(HQ_TABLE, prospect_id),
        headers=_h(),
        json={"fields": fields},
    )
    r.raise_for_status()

    return prospect_id


# ---------------------- GHL SYNC — FIXED VERSION ---------------------- #

def push_deepdive_to_ghl(email: str, answers: list, legacy_code: str, prospect_id: str):
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
        }

        # Lookup contact
        lookup = requests.get(
            f"{GHL_BASE_URL}/contacts/lookup",
            headers=headers,
            params={"email": email, "locationId": GHL_LOCATION_ID},
        ).json()

        contact = None
        if "contacts" in lookup and lookup["contacts"]:
            contact = lookup["contacts"][0]
        elif "contact" in lookup:
            contact = lookup["contact"]

        if not contact:
            print(f"No GHL contact found for email: {email}")
            return None

        ghl_id = contact.get("id")
        assigned = (
            contact.get("assignedUserId")
            or contact.get("userId")
            or contact.get("assignedTo")
        )

        # First, apply the tag
        tag_response = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"tags": ["legacy deep dive submitted"]},
        )
        print(f"Tag Update Status: {tag_response.status_code}")

        # GHL custom field keys (without contact. prefix)
        ghl_fields = [
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
            "q24_if_nothing_changes_in_6_months_what_worries_you_most",
            "q25_who_you_want_to_become_in_12_months",
            "q26_one_feeling_you_never_want_again",
            "q27__one_feeling_you_want_as_your_baseline",
            "q28_preferred_accountability_style",
            "q29_preferred_tracking_style",
            "q30_why_is_now_the_right_time_to_build_something"
        ]

        # Build custom fields object - GHL v1 uses customField (singular) format
        custom_field_data = {}
        for idx, field_key in enumerate(ghl_fields):
            # Use contact. prefix in the customField object
            custom_field_data[f"contact.{field_key}"] = answers[idx]
        
        # Add legacy code and ATRID with contact. prefix
        custom_field_data["contact.legacy_code_id"] = legacy_code
        custom_field_data["contact.atrid"] = prospect_id

        # Update with customField (singular) structure
        update_payload = {
            "customField": custom_field_data  # singular, with contact. prefix in keys
        }

        update_response = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json=update_payload
        )
        
        print(f"GHL Update Status: {update_response.status_code}")
        print(f"GHL Response: {update_response.text[:500]}")  # First 500 chars of response
        
        # Alternative: Try updating fields one by one if bulk update fails
        if update_response.status_code != 200 or "error" in update_response.text.lower():
            print("Bulk update may have failed, trying individual updates...")
            
            # Update critical fields individually
            for field_key, value in [
                ("contact.legacy_code_id", legacy_code),
                ("contact.atrid", prospect_id),
                ("contact.q7_where_do_you_show_up_online_right_now", answers[0]),
                ("contact.q11_desired_outcome", answers[4]),
                ("contact.q22_what_would_300800month_support_right_now", answers[15])
            ]:
                individual_response = requests.put(
                    f"{GHL_BASE_URL}/contacts/{ghl_id}",
                    headers=headers,
                    json={"customField": {field_key: value}}
                )
                print(f"Updated {field_key}: Status {individual_response.status_code}")

        if assigned:
            update_prospect_with_operator_info(prospect_id, assigned)

        return assigned

    except Exception as e:
        print(f"GHL Deep Dive Sync Error: {e}")
        print(f"Full error details: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return None


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.json or {}

        email = str(data.get("email", "")).strip()
        answers = data.get("answers")

        if not isinstance(answers, list):
            answers = []

        while len(answers) < DEEPDIVE_QUESTION_COUNT:
            answers.append("No response")

        answers = answers[:DEEPDIVE_QUESTION_COUNT]

        legacy_code, prospect_id = get_or_create_prospect(email)

        save_deepdive_to_airtable(legacy_code, prospect_id, answers)

        assigned_user_id = push_deepdive_to_ghl(
            email=email,
            answers=answers,
            legacy_code=legacy_code,
            prospect_id=prospect_id
        )

        if assigned_user_id:
            redirect_url = f"{DEEPDIVE_REDIRECT_URL}?uid={assigned_user_id}"
        else:
            redirect_url = DEEPDIVE_REDIRECT_URL

        return jsonify({"redirect_url": redirect_url})

    except Exception as e:
        print(f"Deep Dive Submit Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
