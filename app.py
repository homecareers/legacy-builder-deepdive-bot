from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import os
import datetime
import urllib.parse
import requests
import time   # <-- ONLY NEW IMPORT ADDED

from reports import generate_reports_for_email_or_legacy_code

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

SURVEY_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Survey Responses"

# GHL is optional and currently NOT used (no tags, no field updates)
GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

DEEPDIVE_REDIRECT_URL = (
    os.getenv("DEEPDIVE_REDIRECT_URL")
    or os.getenv("NEXTSTEP_URL")
    or "https://poweredbylegacycode.com/activation"
)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # e.g. https://legacy-builder-deepdive-bot.up.railway.app
REPORTS_DIR = os.getenv("REPORTS_DIR") or "reports"


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


# ---------------------- AIRTABLE HELPERS ---------------------- #

def find_survey_row(prospect_email=None, legacy_code=None):
    """
    MUST NOT create a new row. Only find existing by email and/or Legacy Code.
    """
    if not prospect_email and not legacy_code:
        print("❌ DeepDive find_survey_row: no keys provided.")
        return None

    formulas = []
    if prospect_email and legacy_code:
        formulas.append(f"AND({{Prospect Email}} = '{prospect_email}', {{Legacy Code}} = '{legacy_code}')")
    if prospect_email:
        formulas.append(f"{{Prospect Email}} = '{prospect_email}'")
    if legacy_code:
        formulas.append(f"{{Legacy Code}} = '{legacy_code}'")

    for formula in formulas:
        url = _airtable_url(
            SURVEY_TABLE,
            params={"filterByFormula": formula, "maxRecords": 1, "pageSize": 1},
        )
        try:
            r = requests.get(url, headers=_airtable_headers(), timeout=20)
            r.raise_for_status()
            data = r.json()
            records = data.get("records", [])
            if records:
                return records[0]
        except Exception as e:
            print(f"❌ DeepDive Airtable lookup error [{formula}]: {e}")

    print("⚠️ DeepDive: no Survey Responses row found.")
    return None


def save_deepdive_to_airtable(record_id, answers):
    """
    Patch Q7–Q30 + Deep Dive Date Submitted into the existing Survey Responses row.
    """
    fields = {}

    max_questions = 24  # Q7–Q30
    padded = list(answers[:max_questions])
    while len(padded) < max_questions:
        padded.append("No response")

    for idx, answer in enumerate(padded):
        q_number = 7 + idx
        field_name = f"Q{q_number}"  # Airtable fields named Q7, Q8, ..., Q30
        fields[field_name] = answer

    # Timestamp field (Commander-correct version)
    fields["Deep Dive Date Submitted"] = datetime.datetime.utcnow().isoformat()

    try:
        r = requests.patch(
            _airtable_url(SURVEY_TABLE, record_id),
            headers=_airtable_headers(),
            json={"fields": fields},
            timeout=20,
        )
        r.raise_for_status()
        print(f"✅ DeepDive answers written to Airtable record {record_id}")
    except Exception as e:
        print(f"❌ Error writing DeepDive answers to Airtable: {e}")


# ---------------------- OPTIONAL: GHL SYNC (DISABLED) ---------------------- #

def push_deepdive_to_ghl(email, answers):
    """
    Placeholder. Commander order: no tags, and PDF flow must NOT depend on GHL.
    Leaving this as a no-op for now.
    """
    print("ℹ️ GHL DeepDive sync is currently disabled.")
    return


# ---------------------- ROUTES ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/submit", methods=["POST"])
def submit_deepdive():
    try:
        data = request.json or {}
        email = (data.get("email") or "").strip()
        legacy_code = (data.get("legacy_code") or "").strip()
        answers = data.get("answers") or []

        if not email and not legacy_code:
            return jsonify({"error": "Missing email or legacy_code"}), 400

        record = find_survey_row(email, legacy_code)
        if not record:
            print("❌ DeepDive submit: no matching Survey Responses row.")
            return jsonify({"redirect_url": DEEPDIVE_REDIRECT_URL})

        record_id = record["id"]
        fields = record.get("fields", {})
        existing_legacy_code = fields.get("Legacy Code") or legacy_code

        # 1️⃣ Write Q7–Q30 into same row
        save_deepdive_to_airtable(record_id, answers)

        # 🕒 Commander Fix: ENABLE Airtable propagation before generating PDFs
        time.sleep(0.75)

        # 2️⃣ Optional GHL sync (currently no-op)
        push_deepdive_to_ghl(email, answers)

        # 3️⃣ Trigger PDF engine (Airtable-only, safe if it fails)
        try:
            generate_reports_for_email_or_legacy_code(
                prospect_email=email,
                legacy_code=existing_legacy_code,
                public_base_url=PUBLIC_BASE_URL,
            )
        except Exception as e:
            print(f"❌ DeepDive PDF flow error (non-fatal): {e}")

        return jsonify({"redirect_url": DEEPDIVE_REDIRECT_URL})

    except Exception as e:
        print(f"❌ DeepDive submit route error: {e}")
        return jsonify({"redirect_url": DEEPDIVE_REDIRECT_URL})


@app.route("/reports/<path:filename>")
def serve_report(filename):
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
