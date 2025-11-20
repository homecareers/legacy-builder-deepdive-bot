from flask import Flask, render_template, request, jsonify
import requests
import datetime
import os
import urllib.parse
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------- Config ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

HQ_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
LEADS_TABLE = os.getenv("AIRTABLE_LEADS_TABLE") or "Leads"

GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"

CALL_PREP_URL_BASE = os.getenv("CALL_PREP_URL_BASE") or "https://poweredbylegacycode.com/call-prep"


def _h():
  return {
      "Authorization": f"Bearer {AIRTABLE_API_KEY}",
      "Content-Type": "application/json",
  }


def _url(table, record_id=None, params=None):
  base = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{urllib.parse.quote(table)}"
  if record_id:
    return f"{base}/{record_id}"
  if params:
    return f"{base}?{urllib.parse.urlencode(params)}"
  return base


# ---------------------- Airtable Helpers ---------------------- #

def get_or_create_prospect(email: str):
  """
  Find existing Prospect by email; if none, create a new record + Legacy Code.
  Returns (legacy_code, record_id).
  """

  # 1) Try to find existing prospect by email
  formula = f"{{Prospect Email}} = '{email}'"
  search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})
  r = requests.get(search_url, headers=_h())
  r.raise_for_status()
  data = r.json()

  if data.get("records"):
    rec = data["records"][0]
    rec_id = rec["id"]
    legacy_code = rec.get("fields", {}).get("Legacy Code")

    # If existing record has no Legacy Code, create one similar to original logic
    if not legacy_code:
      auto = rec.get("fields", {}).get("AutoNum")
      if auto is None:
        # re-fetch to be safe
        r2 = requests.get(_url(HQ_TABLE, rec_id), headers=_h())
        auto = r2.json().get("fields", {}).get("AutoNum")

      legacy_code = f"Legacy-X25-OP{1000 + int(auto)}"
      requests.patch(
          _url(HQ_TABLE, rec_id),
          headers=_h(),
          json={"fields": {"Legacy Code": legacy_code}},
      )

    return legacy_code, rec_id

  # 2) If no existing record, create a new one (fallback)
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


def save_builder_plan_to_airtable(legacy_code: str, prospect_id: str, email: str, phone: str, answers: dict):
  """
  Writes the deep-dive answers (Q7–14) into the Leads table.
  Adjust field names to match your Airtable schema.
  """

  fields = {
      "Legacy Code": legacy_code,
      "Prospects": [prospect_id],
      "Date Submitted": datetime.datetime.utcnow().isoformat(),
      "Best Email": email,
      "Best Phone": phone,
      # Map each question to your columns
      "Q7 Business History": answers.get("q7_history", ""),
      "Q8 Goal Style": answers.get("q8_goal_style", ""),
      "Q8a Style Notes": answers.get("q8a_style_notes", ""),
      "Q9 Past Obstacles": answers.get("q9_obstacles", ""),
      "Q10 Personal Wins": answers.get("q10_personal_wins", ""),
      "Q11 Ideal Coach": answers.get("q11_ideal_coach", ""),
      "Q12 Online Presence": answers.get("q12_online_presence", ""),
      "Q13 Best Phone": answers.get("q13_best_phone", ""),
      "Q14 Best Email": answers.get("q14_best_email", ""),
  }

  r = requests.post(
      _url(LEADS_TABLE),
      headers=_h(),
      json={"fields": fields},
  )
  r.raise_for_status()
  return r.json().get("id")


# ---------------------- GHL Sync ---------------------- #

def push_deepdive_to_ghl(email: str, phone: str, answers: dict, legacy_code: str, prospect_id: str):
  """
  Updates the matching GHL contact with deep-dive fields.
  Adjust customField keys to match your GHL configuration.
  Returns assigned user id (if any).
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
      return None

    ghl_id = contact.get("id")
    assigned = (
        contact.get("assignedUserId")
        or contact.get("userId")
        or contact.get("assignedTo")
    )

    # Update contact primary phone/email if provided
    update_payload = {
        "tags": ["legacy builder deepdive submitted"],
        "customField": {
            # Replace these keys with your actual custom field IDs/keys
            "lb_q7_business_history": answers.get("q7_history", ""),
            "lb_q8_goal_style": answers.get("q8_goal_style", ""),
            "lb_q8a_style_notes": answers.get("q8a_style_notes", ""),
            "lb_q9_past_obstacles": answers.get("q9_obstacles", ""),
            "lb_q10_personal_wins": answers.get("q10_personal_wins", ""),
            "lb_q11_ideal_coach": answers.get("q11_ideal_coach", ""),
            "lb_q12_online_presence": answers.get("q12_online_presence", ""),
            "legacy_code_id": legacy_code,
        },
    }

    if phone:
      update_payload["phone"] = phone

    if email:
      update_payload["email"] = email

    requests.put(
        f"{GHL_BASE_URL}/contacts/{ghl_id}",
        headers=headers,
        json=update_payload,
    )

    # Optionally save Airtable prospect id into a custom field
    try:
      requests.put(
          f"{GHL_BASE_URL}/contacts/{ghl_id}",
          headers=headers,
          json={"customField": {"atrid": prospect_id}},
      )
    except Exception as e:
      print("Optional ATRID save failed:", e)

    return assigned

  except Exception as e:
    print("GHL Deepdive Sync Error:", e)
    return None


# ---------------------- Routes ---------------------- #

@app.route("/submit_plan", methods=["POST"])
def submit_plan():
  try:
    data = request.json or {}
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    answers = data.get("answers") or {}

    if not email:
      return jsonify({"error": "Missing email"}), 400

    # Ensure all expected keys exist
    defaults = [
        "q7_history", "q8_goal_style", "q8a_style_notes",
        "q9_obstacles", "q10_personal_wins", "q11_ideal_coach",
        "q12_online_presence", "q13_best_phone", "q14_best_email"
    ]
    for key in defaults:
      answers.setdefault(key, "")

    # Get or create Prospect + Legacy Code
    legacy_code, prospect_id = get_or_create_prospect(email)

    # Save deep-dive to Airtable Leads table
    save_builder_plan_to_airtable(legacy_code, prospect_id, email, phone, answers)

    # Sync to GHL
    assigned_user_id = push_deepdive_to_ghl(email, phone, answers, legacy_code, prospect_id)

    # Build redirect URL for call prep / next step page
    if assigned_user_id:
      redirect_url = f"{CALL_PREP_URL_BASE}?uid={assigned_user_id}"
    else:
      redirect_url = CALL_PREP_URL_BASE

    return jsonify({"redirect_url": redirect_url})

  except Exception as e:
    print("submit_plan error:", e)
    return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
  # If you want to serve the chat from Flask directly:
  return render_template("chat.html")


@app.route("/health")
def health():
  return jsonify({"status": "healthy"})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
