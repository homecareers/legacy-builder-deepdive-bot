from flask import Flask, request, jsonify
import requests
import os
import datetime
import urllib.parse
from flask_cors import CORS

# 🔥 PDF + GPT Report System
from reports import generate_and_email_reports_for_legacy_code


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

HQ_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
DEEPDIVE_TABLE = os.getenv("AIRTABLE_DEEPDIVE_TABLE") or "Deep Dive Responses"

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

NEXTSTEP_URL = os.getenv("NEXTSTEP_URL") or "https://poweredbylegacycode.com/nextstep"


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


# ---------------------- PROSPECT LOGIC ---------------------- #

def get_or_create_prospect(email: str):
    """
    - Look up prospect by email
    - If doesn't exist → create
    - Generate / ensure Legacy Code
    """

    formula = f"{{Prospect Email}} = '{email}'"
    search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})

    r = requests.get(search_url, headers=_h())
    r.raise_for_status()
    data = r.json()

    # ---- If found ----
    if data.get("records"):
        rec = data["records"][0]
        rec_id = rec["id"]
        legacy_code = rec.get("fields", {}).get("Legacy Code")

        # Generate LC if missing
        if not legacy_code:
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

    # ---- If not found → create ----

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

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: dict):
    """
    Save 30 deep dive answers exactly as they appear in your Deep Dive table.
    answers = dict of {field_name: value}
    """

    fields = {
        "Legacy Code": legacy_code,
        "Prospects": [prospect_id],
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    # merge answers into Airtable fields
    fields.update(answers)

    r = requests.post(_url(DEEPDIVE_TABLE), headers=_h(), json={"fields": fields})
    r.raise_for_status()

    return r.json().get("id")


# ---------------------- GHL SYNC ---------------------- #

def sync_to_ghl(email: str, legacy_code: str):
    """
    Attach LC + tag to GHL contact.
    Returns assigned_user_id for routing.
    """

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
            print("No GHL contact found.")
            return None

        ghl_id = contact.get("id")
        assigned = (
            contact.get("assignedUserId")
            or contact.get("userId")
            or contact.get("assignedTo")
        )

        # Update LC + tag
        requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={
                "tags": ["deepdive-submitted"],
                "customField": {"legacy_code_id": legacy_code},
            },
        )

        return assigned

    except Exception as e:
        print("GHL Sync Error:", e)
        return None


# ---------------------- MAIN ROUTE: /submit ---------------------- #

@app.route("/submit", methods=["POST"])
def submit():
    """
    This is the Deep Dive submit endpoint.
    It expects:
    {
        "email": "person@example.com",
        "answers": {
            "Q1 Field Name": "value",
            "Q2 Field Name": "value",
            ...
        }
    }
    """

    try:
        data = request.json or {}

        email = (data.get("email") or "").strip()
        answers = data.get("answers") or {}

        if not email:
            return jsonify({"error": "Missing email"}), 400

        # Create/find Prospect + Legacy Code
        legacy_code, prospect_id = get_or_create_prospect(email)

        # Save Deep Dive answers
        save_deepdive_to_airtable(legacy_code, prospect_id, answers)

        # Sync to GHL
        assigned_user_id = sync_to_ghl(email, legacy_code)

        # ------------------- FIRE PDF GENERATION ------------------- #
        try:
            generate_and_email_reports_for_legacy_code(legacy_code)
        except Exception as e:
            print("PDF/Report Generation Error:", e)
        # ------------------------------------------------------------ #

        # Redirect
        if assigned_user_id:
            redirect_url = f"{NEXTSTEP_URL}?uid={assigned_user_id}"
        else:
            redirect_url = NEXTSTEP_URL

        return jsonify({"redirect_url": redirect_url})

    except Exception as e:
        print(f"Submit Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
