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
RESPONSES_TABLE = os.getenv("AIRTABLE_SCREENING_TABLE") or "Survey Responses"

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

NEXTSTEP_URL = os.getenv("NEXTSTEP_URL") or "https://poweredbylegacycode.com/nextstep"


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


# ---------------------- SERVE HTML ---------------------- #

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/test")
def test():
    return f"""
    <html>
    <body style="font-family: Arial; padding: 40px;">
        <h1>✅ Server is Running!</h1>
        <p>Legacy Builder Deep-Dive Bot is active.</p>
        <p><a href="/">Go to Chat Interface</a></p>
        <hr>
        <h3>Environment Status:</h3>
        <ul>
            <li>AIRTABLE_API_KEY: {'✅ Set' if AIRTABLE_API_KEY else '❌ Missing'}</li>
            <li>AIRTABLE_BASE_ID: {'✅ Set' if AIRTABLE_BASE_ID else '❌ Missing'}</li>
            <li>GHL_API_KEY: {'✅ Set' if GHL_API_KEY else '⚠️ Optional - Not Set'}</li>
            <li>GHL_LOCATION_ID: {'✅ Set' if GHL_LOCATION_ID else '⚠️ Optional - Not Set'}</li>
        </ul>
    </body>
    </html>
    """


# ---------------------- PROSPECT RECORD HANDLING ---------------------- #

def get_or_create_prospect(email: str):
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        print("Warning: Airtable credentials not configured")
        return "TEST-LC-001", "TEST-ID-001"

    formula = f"{{Prospect Email}} = '{email}'"
    search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})

    try:
        r = requests.get(search_url, headers=_h())
        r.raise_for_status()
        data = r.json()

        if data.get("records"):
            rec = data["records"][0]
            rec_id = rec["id"]
            legacy_code = rec.get("fields", {}).get("Legacy Code")

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

    except Exception as e:
        print(f"Airtable error: {str(e)}")
        return "ERROR-LC", "ERROR-ID"


# ---------------------- SAVE DEEP-DIVE (Q7–Q14, NO PHONE) ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, answers: dict):
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        print("Warning: Airtable credentials not configured")
        return "TEST-DEEPDIVE-ID"

    fields = {
        "Q7 Business History": answers.get("q7_history", ""),
        "Q8 Goal Style": answers.get("q8_goal_style", ""),
        "Q8a Style Notes": answers.get("q8a_style_notes", ""),
        "Q9 Obstacles": answers.get("q9_obstacles", ""),
        "Q10 Personal Wins": answers.get("q10_personal_wins", ""),
        "Q11 Ideal Coach": answers.get("q11_ideal_coach", ""),
        "Q12 Online Presence": answers.get("q12_online_presence", ""),
        "Deep Dive Completed": True,
        "Deep Dive Date": datetime.datetime.utcnow().isoformat()
    }

    try:
        r = requests.patch(
            _url(HQ_TABLE, prospect_id),
            headers=_h(),
            json={"fields": fields}
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        print(f"Airtable deep-dive save error: {str(e)}")
        return "ERROR-DEEPDIVE"


# ---------------------- SYNC TO GHL (NO PHONE) ---------------------- #

def push_deepdive_to_ghl(email: str, answers: dict, legacy_code: str):
    try:
        if not GHL_API_KEY or not GHL_LOCATION_ID:
            return None

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
            return None

        ghl_id = contact.get("id")

        update_payload = {
            "tags": ["legacy deepdive completed"],
            "customField": {
                "q7_business_history": answers.get("q7_history", ""),
                "q8_goal_style": answers.get("q8_goal_style", ""),
                "q8a_style_notes": answers.get("q8a_style_notes", ""),
                "q9_obstacles": answers.get("q9_obstacles", ""),
                "q10_personal_wins": answers.get("q10_personal_wins", ""),
                "q11_ideal_coach": answers.get("q11_ideal_coach", ""),
                "q12_online_presence": answers.get("q12_online_presence", ""),
                "deepdive_completed": "true",
                "legacy_code_id": legacy_code
            },
        }

        requests.put(
            f"{GHL_BASE_URL}/contacts/{ghl_id}",
            headers=headers,
            json=update_payload,
        )

        return contact.get("assignedUserId") or contact.get("userId")

    except Exception as e:
        print("GHL Deep-Dive Sync Error:", e)
        return None


# ---------------------- ROUTE: /submit ---------------------- #

@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.json or {}
        email = (data.get("email") or "").strip()
        answers_raw = data.get("answers") or {}
        survey_type = data.get("survey_type") or "deepdive"

        if not email:
            return jsonify({"error": "Missing email"}), 400

        print(f"Processing deep-dive for {email} ({survey_type})")

        legacy_code, prospect_id = get_or_create_prospect(email)

        save_deepdive_to_airtable(legacy_code, prospect_id, answers_raw)

        assigned_user_id = push_deepdive_to_ghl(email, answers_raw, legacy_code)

        if assigned_user_id:
            redirect_url = f"{NEXTSTEP_URL}?uid={assigned_user_id}&deepdive=true"
        else:
            redirect_url = f"{NEXTSTEP_URL}?deepdive=true"

        return jsonify({"redirect_url": redirect_url, "legacy_code": legacy_code})

    except Exception as e:
        print("Submit Error:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "legacy-builder-deepdive-bot",
        "airtable_configured": bool(AIRTABLE_API_KEY and AIRTABLE_BASE_ID),
        "ghl_configured": bool(GHL_API_KEY and GHL_LOCATION_ID)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Legacy Builder Deep-Dive Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
