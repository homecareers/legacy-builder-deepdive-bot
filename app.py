from flask import Flask, request, jsonify, render_template
import requests
import os
import datetime
import urllib.parse
import time
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

# Deep Dive writes into Survey Responses table (per your setup)
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
                auto = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json().get("fields", {}).get("AutoNum")

            legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
            requests.patch(
                _url(HQ_TABLE, rec_id),
                headers=_h(),
                json={"fields": {"Legacy Code": legacy_code}},
            )

        return legacy_code, rec_id

    # ❗ Create new record if not found
    payload = {"fields": {"Prospect Email": email}}
    r = requests.post(_url(HQ_TABLE), headers=_h(), json=payload)
    r.raise_for_status()
    rec = r.json()
    rec_id = rec["id"]

    auto = rec.get("fields", {}).get("AutoNum")
    if auto is None:
        auto = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json().get("fields", {}).get("AutoNum")

    legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
    requests.patch(
        _url(HQ_TABLE, rec_id),
        headers=_h(),
        json={"fields": {"Legacy Code": legacy_code}},
    )

    return legacy_code, rec_id

# ---------------------- SAVE DEEP DIVE TO AIRTABLE ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):

    deepdive_fields = [
        "07_where_do_you_show_up_online_right_now",
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
        "q21__systemfollowing_confidence_110",
        "q22_what_would_300800month_support_right_now",
        "q23__biggest_fear_or_hesitation",
        "q24__if_nothing_changes_in_6_months_what_worries_you",
        "q25_who_you_want_to_become_in_12_months",
        "q26__one_feeling_you_never_want_again",
        "q27__one_feeling_you_want_as_your_baseline",
        "q28_preferred_accountability_style",
        "q29_preferred_tracking_style",
        "q30_why_is_now_the_right_time_to_build_something"
    ]

    # REQUIRED VARIABLE — this was missing before
    fields = {
        "Legacy Code": legacy_code,
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    for idx, value in enumerate(answers):
        fields[deepdive_fields[idx]] = value

    r = requests.patch(
        _url(HQ_TABLE, prospect_id),
        headers=_h(),
        json={"fields": fields},
    )
    r.raise_for_status()

    return prospect_id

# ---------------------- GHL SYNC ---------------------- #

def push_deepdive_to_ghl(email: str, answers: list, legacy_code: str, prospect_id: str):
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
        }

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

        requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"tags": ["legacy deep dive submitted"]},
        )

        field_updates = [
            ("07_where_do_you_show_up_online_right_now", answers[0]),
            ("q8_social_presence_snapshot", answers[1]),
            ("q9_content_confidence_110", answers[2]),
            ("q10_90day_definition_of_this_worked", answers[3]),
            ("q11_desired_outcome", answers[4]),
            ("q12_why_that_outcome_matters", answers[5]),
            ("q13_weekly_schedule_reality", answers[6]),
            ("q14_highest_energy_windows", answers[7]),
            ("q15_commitments_we_must_build_around", answers[8]),
            ("q16_what_helps_you_stay_consistent", answers[9]),
            ("q17_what_usually_pulls_you_off_track", answers[10]),
            ("q18_stressdiscouragement_response", answers[11]),
            ("q19_strengths_you_bring", answers[12]),
            ("q20_skill_you_want_the_most_help_with", answers[13]),
            ("q21__systemfollowing_confidence_110", answers[14]),
            ("q22_what_would_300800month_support_right_now", answers[15]),
            ("q23__biggest_fear_or_hesitation", answers[16]),
            ("q24__if_nothing_changes_in_6_months_what_worries_you", answers[17]),
            ("q25_who_you_want_to_become_in_12_months", answers[18]),
            ("q26__one_feeling_you_never_want_again", answers[19]),
            ("q27__one_feeling_you_want_as_your_baseline", answers[20]),
            ("q28_preferred_accountability_style", answers[21]),
            ("q29_preferred_tracking_style", answers[22]),
            ("q30_why_is_now_the_right_time_to_build_something", answers[23])
        ]

        for field_key, value in field_updates:
            requests.put(
                f"{GHL_BASE_URL}/contacts/{ghl_id}",
                headers=headers,
                json={"customField": {field_key: str(value)}}
            )
            time.sleep(0.4)

        if assigned:
            update_prospect_with_operator_info(prospect_id, assigned)

        return assigned

    except Exception as e:
        print(f"GHL Deep Dive Sync Error: {e}")
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
        answers = data.get("answers") or []

        while len(answers) < DEEPDIVE_QUESTION_COUNT:
            answers.append("No response")

        answers = answers[:DEEPDIVE_QUESTION_COUNT]

        legacy_code, prospect_id = get_or_create_prospect(email)
        save_deepdive_to_airtable(legacy_code, prospect_id, answers)
        assigned_user_id = push_deepdive_to_ghl(email, answers, legacy_code, prospect_id)

        redirect_url = (
            f"{DEEPDIVE_REDIRECT_URL}?uid={assigned_user_id}"
            if assigned_user_id else DEEPDIVE_REDIRECT_URL
        )

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
