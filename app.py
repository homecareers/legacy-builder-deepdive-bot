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

# Core tables (same base / structure you already use)
HQ_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
USERS_TABLE = os.getenv("AIRTABLE_USERS_TABLE") or "Users"

# NEW: Deep Dive table (second survey)
DEEPDIVE_TABLE = os.getenv("AIRTABLE_DEEPDIVE_TABLE") or "Deep Dive Responses"

# GHL
GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

# Redirect after Deep Dive submit
DEEPDIVE_REDIRECT_URL = (
    os.getenv("DEEPDIVE_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/nextstep"
)

# How many Deep Dive questions this bot is collecting (Q07–Q30 = 24)
DEEPDIVE_QUESTION_COUNT = int(os.getenv("DEEPDIVE_QUESTION_COUNT", "24"))


# ---------------------- AIRTABLE HELPERS ---------------------- #

def _h():
    """Standard Airtable headers."""
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }


def _url(table, rec_id=None, params=None):
    """Build Airtable URL for table / record / query."""
    base = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{urllib.parse.quote(table)}"
    if rec_id:
        return f"{base}/{rec_id}"
    if params:
        return f"{base}?{urllib.parse.urlencode(params)}"
    return base


# ---------------------- OPERATOR LOOKUP ---------------------- #

def get_operator_info(ghl_user_id: str):
    """
    Look up the GHL User ID in the Users table and return their Legacy Code AND Email.
    """
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
            print(
                f"Found operator -> GHL User ID: {ghl_user_id}, "
                f"Legacy Code: {op_legacy_code}, Email: {op_email}"
            )
            return op_legacy_code, op_email

    except Exception as e:
        print(f"Error looking up operator info: {e}")

    return None, None


def update_prospect_with_operator_info(prospect_id: str, ghl_user_id: str):
    """
    Update the Prospect with GHL User ID, Assigned Op Legacy Code, and Assigned Op Email.
    """
    try:
        update_fields = {"GHL User ID": ghl_user_id}

        op_legacy_code, op_email = get_operator_info(ghl_user_id)
        if op_legacy_code:
            update_fields["Assigned Op Legacy Code"] = op_legacy_code
        if op_email:
            update_fields["Assigned Op Email"] = op_email

        r = requests.patch(
            _url(HQ_TABLE, prospect_id),
            headers=_h(),
            json={"fields": update_fields},
        )
        r.raise_for_status()
        print(
            f"Updated Prospect {prospect_id} with GHL User ID {ghl_user_id}, "
            f"Op Legacy Code: {op_legacy_code}, Op Email: {op_email}"
        )

    except Exception as e:
        print(f"Error updating prospect with operator info: {e}")


# ---------------------- PROSPECT RECORD HANDLING ---------------------- #

def get_or_create_prospect(email: str):
    """
    Search for Prospect by email. If exists → return it.
    If not → create new + assign Legacy Code.

    This mirrors your existing screening app logic.
    """
    # 1) Look up existing prospect by email
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

        # If somehow Legacy Code is missing, generate it
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

    # 2) No record found → create new prospect
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


# ---------------------- SAVE DEEP DIVE Q07–Q30 ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: list):
    """
    Writes Deep Dive answers into the 'Deep Dive Responses' table.

    NOTE:
    - We generically map answers to DD_Q01..DD_Q24 so you can align column
      names in Airtable EXACTLY to these field names, or adjust below.
    - If you've already created exact question names in Airtable, replace the
      mapping in 'fields.update(...)' with those real names.
    """
    # Base fields
    fields = {
        "Legacy Code": legacy_code,
        "Prospects": [prospect_id],
        "Date Submitted": datetime.datetime.utcnow().isoformat(),
    }

    # Map each answer to a numbered Deep Dive field
    # Example Airtable fields: DD_Q01, DD_Q02, ..., DD_Q24
    for idx, ans in enumerate(answers):
        key = f"DD_Q{str(idx + 1).zfill(2)}"
        fields[key] = ans

    r = requests.post(_url(DEEPDIVE_TABLE), headers=_h(), json={"fields": fields})
    r.raise_for_status()
    record_id = r.json().get("id")
    print(f"Saved Deep Dive record {record_id} for Prospect {prospect_id}")
    return record_id


# ---------------------- SYNC DEEP DIVE TO GHL ---------------------- #

def push_deepdive_to_ghl(email: str, answers: list, legacy_code: str, prospect_id: str):
    """
    Updates the GHL contact record with Deep Dive answers + legacy code.

    - Adds tag: 'legacy deep dive submitted'
    - Updates custom fields: dd_q01..dd_q24 (you must create/match these in GHL)
    - Also saves legacy_code_id + atrid
    - Returns assigned user ID (coach) for routing.
    """
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
        }

        # Lookup contact by email
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

        # 1) Add Deep Dive tag
        tag_response = requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json={"tags": ["legacy deep dive submitted"]},
        )
        print(f"Deep Dive tag update status: {tag_response.status_code}")

        # 2) Build all custom field updates
        field_updates = []

        # Map answers to dd_q01..dd_qNN
        for idx, ans in enumerate(answers):
            field_updates.append({f"dd_q{str(idx + 1).zfill(2)}": ans})

        # Always push legacy code + Airtable record id
        field_updates.append({"legacy_code_id": legacy_code})
        field_updates.append({"atrid": prospect_id})

        # 3) Send each field update (robust format)
        for field_update in field_updates:
            try:
                field_name = list(field_update.keys())[0]
                field_value = field_update[field_name]

                # Primary attempt (wrapped in 'customField')
                resp = requests.put(
                    f"{GHL_BASE_URL}/contacts/{ghl_id}",
                    headers=headers,
                    json={"customField": field_update},
                )

                if resp.status_code == 200:
                    print(
                        f"✅ Deep Dive field updated {field_name}: "
                        f"{str(field_value)[:40]}"
                    )
                else:
                    print(
                        f"❌ Failed Deep Dive field {field_name} with status "
                        f"{resp.status_code}, trying alt format..."
                    )
                    # Alternate format (in case location expects raw body)
                    alt_resp = requests.put(
                        f"{GHL_BASE_URL}/contacts/{ghl_id}",
                        headers=headers,
                        json=field_update,
                    )
                    if alt_resp.status_code == 200:
                        print(f"✅ Alt format succeeded for {field_name}")
                    else:
                        print(
                            f"❌ Alt format also failed for {field_name} "
                            f"status {alt_resp.status_code}"
                        )

            except Exception as e:
                print(f"Error updating Deep Dive field {field_update}: {e}")
                continue

        # 4) Update Prospect record with operator info if assigned exists
        if assigned:
            update_prospect_with_operator_info(prospect_id, assigned)

        return assigned

    except Exception as e:
        print(f"GHL Deep Dive Sync Error: {e}")
        return None


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    """
    Serve the Deep Dive chat HTML.
    - Place your deep-dive chat.html in templates/chat.html (or rename + adjust here).
    """
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit():
    """
    Main submission endpoint for Deep Dive #2 bot.

    Expects JSON:
    {
        "email": "someone@example.com",
        "answers": [ "...", "...", ... ]    # Q07–Q30 in order
    }

    Returns:
    { "redirect_url": "https://..." }
    """
    try:
        data = request.json or {}
        email = (data.get("email") or "").strip()
        answers = data.get("answers") or []

        if not email:
            return jsonify({"error": "Missing email"}), 400

        # Normalize answers length (pad to DEEPDIVE_QUESTION_COUNT)
        # so backend never explodes if frontend skips one
        while len(answers) < DEEPDIVE_QUESTION_COUNT:
            answers.append("No response")

        # Keep only the expected number of answers (if somehow more were sent)
        answers = answers[:DEEPDIVE_QUESTION_COUNT]

        # 1) Create or find Prospect record
        legacy_code, prospect_id = get_or_create_prospect(email)

        # 2) Save Deep Dive answers in Airtable
        save_deepdive_to_airtable(legacy_code, prospect_id, answers)

        # 3) Sync Deep Dive data into GHL
        assigned_user_id = push_deepdive_to_ghl(
            email=email,
            answers=answers,
            legacy_code=legacy_code,
            prospect_id=prospect_id,
        )

        # 4) Build redirect URL (optionally coach-specific via ?uid=)
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
