from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import urllib.parse
import requests
import datetime
import time  # REQUIRED for rate-limit safety

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

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

DEEPDIVE_QUESTION_COUNT = 24

# ---------------------- HELPERS ---------------------- #

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
        print(f"Operator lookup error: {e}")

    return None, None


def update_prospect_with_operator_info(prospect_id: str, ghl_user_id: str):
    try:
        update_fields = {"GHL User ID": ghl_user_id}

        op_code, op_email = get_operator_info(ghl_user_id)
        if op_code: update_fields["Assigned Op Legacy Code"] = op_code
        if op_email: update_fields["Assigned Op Email"] = op_email

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

    if data.get("records"):
        rec = data["records"][0]
        rec_id = rec["id"]
        lc = rec.get("fields", {}).get("Legacy Code")

        if not lc:
            auto = rec.get("fields", {}).get("AutoNum")
            if auto is None:
                d = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json()
                auto = d.get("fields", {}).get("AutoNum")

            lc = f"Legacy-X25-OP{1000 + int(auto)}"
            requests.patch(
                _url(HQ_TABLE, rec_id),
                headers=_h(),
                json={"fields": {"Legacy Code": lc}},
            )

        return lc, rec_id

    # Create if missing
    payload = {"fields": {"Prospect Email": email}}
    r = requests.post(_url(HQ_TABLE), headers=_h(), json=payload)
    rec = r.json()
    rec_id = rec["id"]

    auto = rec.get("fields", {}).get("AutoNum")
    if auto is None:
        d = requests.get(_url(HQ_TABLE, rec_id), headers=_h()).json()
        auto = d.get("fields", {}).get("AutoNum")

    lc = f"Legacy-X25-OP{1000 + int(auto)}"
    requests.patch(
        _url(HQ_TABLE, rec_id),
        headers=_h(),
        json={"fields": {"Legacy Code": lc}},
    )

    return lc, rec_id


# ---------------------- AIRTABLE SAVE ---------------------- #

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
    "Q30 Why is NOW the right time to build something?",
]

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):

    fields = {
        "Legacy Code": legacy_code,
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    for idx, val in enumerate(answers):
        fields[DEEPDIVE_FIELDS[idx]] = val

    requests.patch(
        _url(HQ_TABLE, prospect_id),
        headers=_h(),
        json={"fields": fields},
    )


# ---------------------- GHL SYNC — USING YOUR ORIGINAL WORKING KEYS ---------------------- #

REAL_GHL_KEYS = [
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
    "q24__if_nothing_changes_in_6_months_what_worries_you_most",
    "q25_who_you_want_to_become_in_12_months",
    "q26__one_feeling_you_never_want_again",
    "q27__one_feeling_you_want_as_your_baseline",
    "q28_preferred_accountability_style",
    "q29_preferred_tracking_style",
    "q30_why_is_now_the_right_time_to_build_something",
]

def push_deepdive_to_ghl(email: str, answers: list, legacy_code: str, prospect_id: str):

    if not GHL_API_KEY or not GHL_LOCATION_ID:
        print("Missing GHL credentials")
        return

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
        print("No contact found")
        return

    ghl_id = contact["id"]
    print(f"Updating contact ID: {ghl_id}")

    # Normalize answers
    while len(answers) < 24:
        answers.append("No response")
    answers = answers[:24]

    # Field-by-field update (same as working 6Q bot)
    for i, field_key in enumerate(REAL_GHL_KEYS):
        field_value = answers[i]

        # Primary attempt
        r1 = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"customField": {field_key: field_value}},
        )

        if r1.status_code == 200:
            print(f"✓ Updated {field_key}")
        else:
            print(f"⚠️ Primary failed for {field_key} ({r1.status_code})")

            # Fallback attempt
            r2 = requests.put(
                f"{GHL_BASE_URL}/contacts/{ghl_id}",
                headers=headers,
                json={field_key: field_value},
            )

            if r2.status_code == 200:
                print(f"✓ Fallback updated {field_key}")
            else:
                print(f"❌ FAILED {field_key}: {r2.status_code}")
                print(r2.text[:200])

        # REQUIRED — prevents GHL 429/400 rate limits
        time.sleep(0.5)


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit():

    data = request.json or {}
    email = str(data.get("email", "")).strip()
    answers = data.get("answers") or []

    while len(answers) < DEEPDIVE_QUESTION_COUNT:
        answers.append("No response")
    answers = answers[:DEEPDIVE_QUESTION_COUNT]

    legacy_code, prospect_id = get_or_create_prospect(email)

    save_deepdive_to_airtable(legacy_code, prospect_id, answers)

    push_deepdive_to_ghl(email, answers, legacy_code, prospect_id)

    return jsonify({"redirect_url": DEEPDIVE_REDIRECT_URL})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
