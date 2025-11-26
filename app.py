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

HQ_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
USERS_TABLE = os.getenv("AIRTABLE_USERS_TABLE") or "Users"
DEEPDIVE_TABLE = os.getenv("AIRTABLE_DEEPDIVE_TABLE") or "Deep Dive Responses"

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
            user_record = data["records"][0]
            fields = user_record.get("fields", {})
            op_legacy_code = fields.get("Legacy Code")
            op_email = fields.get("Email")
            return op_legacy_code, op_email

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

    if data.get("records"):
        rec = data["records"][0]
        rec_id = rec["id"]
        fields = rec.get("fields", {})
        legacy_code = fields.get("Legacy Code")

        if not legacy_code:
            auto = fields.get("AutoNum")
            if auto is None:
                r2 = requests.get(_url(HQ_TABLE, rec_id), headers=_h())
                auto = r2.json().get("fields", {}).get("AutoNum")

            legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
            requests.patch(
                _url(HQ_TABLE, rec_id),
                headers=_h(),
                json={"fields": {"Legacy Code": legacy_code}},
            )

        return legacy_code, rec_id

    payload = {"fields": {"Prospect Email": email}}
    r = requests.post(_url(HQ_TABLE), headers=_h(), json=payload)
    r.raise_for_status()
    rec = r.json()
    rec_id = rec["id"]

    auto = rec.get("fields", {}).get("AutoNum")
    if auto is None:
        r2 = requests.get(_url(HQ_TABLE, rec_id), headers=_h())
        auto = r2.json().get("fields", {}).get("AutoNum")

    legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
    requests.patch(
        _url(HQ_TABLE, rec_id),
        headers=_h(),
        json={"fields": {"Legacy Code": legacy_code}},
    )

    return legacy_code, rec_id


# ---------------------- SAVE DEEP DIVE ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):

    fields = {
        "Legacy Code": legacy_code,
        "Prospects": [prospect_id],
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    for idx, ans in enumerate(answers):
        key = f"DD_Q{str(idx + 1).zfill(2)}"
        fields[key] = ans

    r = requests.post(_url(DEEPDIVE_TABLE), headers=_h(), json={"fields": fields})
    r.raise_for_status()
    return r.json().get("id")


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

        for idx, ans in enumerate(answers):
            field_name = f"dd_q{str(idx + 1).zfill(2)}"
            requests.put(
                f"{GHL_BASE_URL}/contacts/{ghl_id}",
                headers=headers,
                json={"customField": {field_name: ans}},
            )

        requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"customField": {"legacy_code_id": legacy_code}},
        )

        requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"customField": {"atrid": prospect_id}},
        )

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
        
        # ----------------------
        # 🔥 FIX APPLIED HERE
        # Changed from .trim() to .strip() (Python syntax)
        # Also simplified the logic
        # ----------------------
        email = str(data.get("email", "")).strip()
        
        answers = data.get("answers")

        # Ensure answers is a list
        if not isinstance(answers, list):
            answers = []

        # Pad answers to match DEEPDIVE_QUESTION_COUNT
        while len(answers) < DEEPDIVE_QUESTION_COUNT:
            answers.append("No response")

        # Trim excess answers if needed
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
