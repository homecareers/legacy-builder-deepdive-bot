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

# Hard-coded to eliminate naming mismatch
SURVEY_TABLE = "Survey Responses"

LEGACY_SURVEY_REDIRECT_URL = (
    os.getenv("LEGACY_SURVEY_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/activation"
)


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


# ---------------------- FIELD ID MAP ---------------------- #
# This is the KEY FIX: we fetch Airtable’s true internal field IDs

def get_field_id_map():
    """Fetch Airtable field metadata and return {label: fieldId} mapping."""
    url = _airtable_url(SURVEY_TABLE, params={"maxRecords": 1})

    try:
        r = requests.get(url, headers=_airtable_headers(), timeout=20)
        r.raise_for_status()

        # Airtable returns field info ONLY inside 'records'
        # but internal API always exposes fields under 'fields'
        sample = r.json().get("records", [])
        if not sample:
            print("⚠️ No sample record found for field extraction.")
            return {}

        fields = sample[0].get("fields", {})
        # OPTIONAL: If fields are empty (new record), no problem.
        # But this only gives us field NAMES, not IDs — so we use the metadata API:
        pass

    except Exception as e:
        print(f"❌ Error fetching field names: {e}")

    # REAL FIX: use Airtable Metadata API v2 (unofficial but stable)
    meta_url = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables"
    try:
        r = requests.get(meta_url, headers=_airtable_headers(), timeout=20)
        r.raise_for_status()
        tables = r.json().get("tables", [])
        for t in tables:
            if t["name"] == SURVEY_TABLE:
                field_map = {f["name"]: f["id"] for f in t["fields"]}
                print("🔐 FIELD ID MAP LOADED:", field_map)
                return field_map

    except Exception as e:
        print(f"❌ Error pulling metadata API: {e}")

    return {}


# ---------------------- AIRTABLE LOOKUP ---------------------- #

def find_survey_row(prospect_email=None, legacy_code=None):

    # 1️⃣ ALWAYS lookup by email first
    if prospect_email:
        formula = f"{{Prospect Email}} = '{prospect_email}'"
        print(f"🔍 Attempting email lookup: {formula}")

        url = _airtable_url(SURVEY_TABLE, params={
            "filterByFormula": formula,
            "maxRecords": 1,
            "pageSize": 1,
        })

        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            records = r.json().get("records", [])
            if records:
                print("✅ Email lookup successful")
                return records[0]
        except Exception as e:
            print(f"❌ Airtable email lookup error: {e}")

    # 2️⃣ Optional fallback by Legacy Code (just in case)
    if legacy_code:
        formula = f"{{Legacy Code}} = '{legacy_code}'"
        url = _airtable_url(SURVEY_TABLE, params={
            "filterByFormula": formula,
            "maxRecords": 1,
            "pageSize": 1,
        })

        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            records = r.json().get("records", [])
            if records:
                print("✅ Legacy Code lookup successful")
                return records[0]
        except Exception as e:
            print(f"❌ Airtable legacy_code lookup error: {e}")

    print("⚠️ No matching Survey Responses row found.")
    return None


# ---------------------- SAVE ANSWERS (WITH FIELD IDS) ---------------------- #

def save_legacy_survey_to_airtable(record_id, answers):

    # Get Airtable internal field IDs
    field_ids = get_field_id_map()

    # MUST map Q7–Q30 → 24 answers
    max_questions = 24
    padded = list(answers[:max_questions])

    while len(padded) < max_questions:
        padded.append("No response")

    fields_payload = {}

    for idx, answer in enumerate(padded):
        q_number = 7 + idx
        label = f"Q{q_number}"

        if label not in field_ids:
            print(f"❌ Field label missing in ID map: {label}")
            continue

        real_field_id = field_ids[label]
        fields_payload[real_field_id] = answer

    print("📡 FINAL PATCH PAYLOAD:", fields_payload)

    try:
        r = requests.patch(
            _airtable_url(SURVEY_TABLE, record_id),
            headers=_airtable_headers(),
            json={"fields": fields_payload},
            timeout=20,
        )
        r.raise_for_status()
        print(f"✅ Airtable PATCH success for record {record_id}")

    except Exception as e:
        print(f"❌ ERROR: Airtable PATCH failed: {e}")


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit_legacy_survey():

    try:
        data = request.json or {}
        email = (data.get("email") or "").strip()
        answers = data.get("answers") or []

        if not email:
            return jsonify({"error": "Missing email"}), 400

        record = find_survey_row(prospect_email=email)
        if not record:
            print("❌ No row found — redirecting anyway.")
            return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

        record_id = record["id"]

        save_legacy_survey_to_airtable(record_id, answers)

        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})

    except Exception as e:
        print(f"❌ Legacy Survey submit route error: {e}")
        return jsonify({"redirect_url": LEGACY_SURVEY_REDIRECT_URL})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
