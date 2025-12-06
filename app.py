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
    or "https://poweredbylegacycode.com/activation"
)

# Deep Dive = 24 questions (Q7–Q30)
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
            USERS_TABLE, params={"filterByFormula": formula, "maxRecords": 1}
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


# ---------------------- PROSPECT HANDLING (FIND BY EMAIL, NO NEW ROW) ---------------------- #
def get_or_create_prospect(email: str):
    formula = f"{{Prospect Email}} = '{email}'"
    search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
    r = requests.get(search_url, headers=_h())
    r.raise_for_status()
    data = r.json()

    # ✔️ Record exists — use this row
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

    # ❗ Record does NOT exist, create it (only if truly missing)
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


# ---------------------- SAVE DEEP DIVE — ORIGINAL AIRTABLE LOGIC ---------------------- #
def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):
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

    for idx, value in enumerate(answers):
        fields[deepdive_fields[idx]] = value

    r = requests.patch(
        _url(HQ_TABLE, prospect_id),
        headers=_h(),
        json={"fields": fields},
    )
    r.raise_for_status()
    return prospect_id


# ---------------------- GHL SYNC — BATCH UPDATE, NO DELAYS ---------------------- #
def push_deepdive_to_ghl(email: str, answers: list, legacy_code: str, prospect_id: str):
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
        }

        # Look up the contact
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

        print(f"Found contact ID: {ghl_id} for email: {email}")

        # Tag for Deep Dive completion
        tag_response = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"tags": ["legacy deep dive submitted"]},
        )
        print(f"Tag Update Status: {tag_response.status_code}")

        # BATCH UPDATE
        all_custom_fields = {
            "07_where_do_you_show_up_online_right_now": str(answers[0]),
            "q8_social_presence_snapshot": str(answers[1]),
            "q9_content_confidence_110": str(answers[2]),
            "q10_90day_definition_of_this_worked": str(answers[3]),
            "q11_desired_outcome": str(answers[4]),
            "q12_why_that_outcome_matters": str(answers[5]),
            "q13_weekly_schedule_reality": str(answers[6]),
            "q14_highest_energy_windows": str(answers[7]),
            "q15_commitments_we_must_build_around": str(answers[8]),
            "q16_what_helps_you_stay_consistent": str(answers[9]),
            "q17_what_usually_pulls_you_off_track": str(answers[10]),
            "q18_stressdiscouragement_response": str(answers[11]),
            "q19_strengths_you_bring": str(answers[12]),
            "q20_skill_you_want_the_most_help_with": str(answers[13]),
            "q21_systemfollowing_confidence_110": str(answers[14]),
            "q22_what_would_300800month_support_right_now": str(answers[15]),
            "q23__biggest_fear_or_hesitation": str(answers[16]),
            "q24__if_nothing_changes_in_6_months_what_worries_you_most": str(answers[17]),
            "q25_who_you_want_to_become_in_12_months": str(answers[18]),
            "q26__one_feeling_you_never_want_again": str(answers[19]),
            "q27__one_feeling_you_want_as_your_baseline": str(answers[20]),
            "q28_preferred_accountability_style": str(answers[21]),
            "q29_preferred_tracking_style": str(answers[22]),
            "q30_why_is_now_the_right_time_to_build_something": str(answers[23]),
        }

        field_response = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"customField": all_custom_fields}
        )

        if field_response.status_code == 200:
            print("✓ Successfully updated all 24 Deep Dive fields in GHL")
        else:
            print(f"Failed to update fields: {field_response.status_code}")
            print(f"Response: {field_response.text}")

        if field_response.status_code == 400:
            print("Batch update failed. Error details:", field_response.json())

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
