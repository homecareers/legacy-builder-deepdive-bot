import os
import datetime
import requests
import urllib.parse
from playwright.sync_api import sync_playwright
import base64

# Try different OpenAI import methods to handle version issues
try:
    from openai import OpenAI
    # Try to initialize with just api_key
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
except TypeError:
    # Fallback for older OpenAI library versions
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    client = None

# -------- CONFIG -------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

PROSPECTS_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
DEEPDIVE_TABLE = os.getenv("AIRTABLE_DEEPDIVE_TABLE") or "Deep Dive Responses"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and OPENAI_API_KEY):
    raise RuntimeError("Missing Airtable or OpenAI environment variables")

# -------- Airtable Helpers -------- #

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

def get_record_by_legacy_code(table: str, legacy_code: str):
    formula = f"{{Legacy Code}} = '{legacy_code}'"
    params = {
        "filterByFormula": formula,
        "maxRecords": 1,
    }

    if table == DEEPDIVE_TABLE:
        params["sort[0][field]"] = "Date Submitted"
        params["sort[0][direction]"] = "desc"

    r = requests.get(_url(table, params=params), headers=_h())
    r.raise_for_status()
    data = r.json()
    records = data.get("records", [])
    return records[0] if records else None

def get_prospect_and_deepdive(legacy_code: str):
    prospect_rec = get_record_by_legacy_code(PROSPECTS_TABLE, legacy_code)
    if not prospect_rec:
        raise ValueError(f"No Prospect found with Legacy Code {legacy_code}")

    deepdive_rec = get_record_by_legacy_code(DEEPDIVE_TABLE, legacy_code)
    if not deepdive_rec:
        raise ValueError(f"No Deep Dive record found for Legacy Code {legacy_code}")

    return prospect_rec["fields"], deepdive_rec["fields"], prospect_rec["id"]

# -------- GPT Content Generation -------- #

def _deepdive_to_bullet_block(fields: dict) -> str:
    lines = []
    for k, v in fields.items():
        if k in ("Legacy Code", "Prospects", "Created", "Date Submitted"):
            continue
        if not v:
            continue
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)

def _call_gpt(system_prompt: str, user_prompt: str) -> str:
    if client:
        # New OpenAI library style
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=1800,
        )
        return resp.choices[0].message.content
    else:
        # Old OpenAI library style
        import openai
        resp = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=1800,
        )
        return resp.choices[0].message.content

def generate_prospect_report_text(prospect: dict, deepdive: dict) -> str:
    name = prospect.get("Prospect Name") or "this client"
    gem = deepdive.get("Q6 Business Style (GEM)") or deepdive.get("GEM Type") or ""
    bullets = _deepdive_to_bullet_block(deepdive)

    system_prompt = """
You are a strategic Herbalife-aligned business coach.
Stay 100% compliant: do not make income guarantees, health claims, or disease language.
Focus on behaviors, routines, DMO, and aligned expectations.
    """.strip()

    user_prompt = f"""
Prospect: {name}
GEM Style: {gem}

Deep Dive:
{bullets}

Create a 90-Day Business Blueprint personalized to their answers and GEM style.
    """

    return _call_gpt(system_prompt, user_prompt)

def generate_consult_report_text(prospect: dict, deepdive: dict) -> str:
    name = prospect.get("Prospect Name") or "this prospect"
    email = prospect.get("Prospect Email") or ""
    gem = deepdive.get("Q6 Business Style (GEM)") or deepdive.get("GEM Type") or ""
    bullets = _deepdive_to_bullet_block(deepdive)

    system_prompt = """
You are advising a sponsor on how to lead a personalized 1:1 consultation.
No hype. No promises. Clear, tactical, GEM-specific coaching.
    """

    user_prompt = f"""
Prospect Name: {name}
Prospect Email: {email}
GEM: {gem}

Deep Dive Data:
{bullets}

Create a sponsor-only Consultation Briefing that guides the call step-by-step.
    """

    return _call_gpt(system_prompt, user_prompt)

# -------- HTML templates -------- #

def build_prospect_html(prospect: dict, legacy_code: str, body: str) -> str:
    name = prospect.get("Prospect Name") or "Your 90-Day Blueprint"
    today = datetime.date.today().strftime("%b %d, %Y")

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>90-Day Blueprint — {name}</title>
<style>
body {{
    font-family: Inter, sans-serif;
    background: #050608;
    color: white;
    padding: 30px;
}}
.card {{
    background: #0f0f0f;
    padding: 30px;
    border-radius: 12px;
    border: 1px solid #222;
    max-width: 760px;
    margin: auto;
}}
h1 {{ color: #D4A72C; }}
</style>
</head>
<body>
<div class="card">
<h1>90-Day Business Blueprint</h1>
<p>Prospect: {name}<br>
Legacy Code: {legacy_code}<br>
Generated: {today}</p>
<div>{body.replace('\n', '<br>')}</div>
</div>
</body>
</html>
"""

def build_consult_html(prospect: dict, legacy_code: str, body: str) -> str:
    name = prospect.get("Prospect Name") or "Prospect"
    email = prospect.get("Prospect Email") or ""
    today = datetime.date.today().strftime("%b %d, %Y")

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Consultation Briefing — {name}</title>
<style>
body {{
    font-family: Inter, sans-serif;
    background: #050608;
    color: white;
    padding: 30px;
}}
.card {{
    background: #0f0f0f;
    padding: 30px;
    border-radius: 12px;
    border: 1px solid #222;
    max-width: 760px;
    margin: auto;
}}
h1 {{ color: #D4A72C; }}
</style>
</head>
<body>
<div class="card">
<h1>Consultation Briefing</h1>
<p>Prospect: {name} ({email})<br>
Legacy Code: {legacy_code}<br>
Generated: {today}</p>
<div>{body.replace('\n', '<br>')}</div>
</div>
</body>
</html>
"""

# -------- PDF Engine -------- #

def html_to_pdf(html: str, out_path: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=out_path,
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"},
        )
        browser.close()

# -------- Upload to Airtable -------- #

def attach_pdf_to_airtable(record_id: str, field_name: str, file_path: str):
    """
    Airtable accepts a URL OR base64 file upload.
    Railway gives us local files → we upload via base64.
    """

    # Read PDF bytes and base64 encode
    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    upload_url = "https://api.airtable.com/v0/batch-upload"

    payload = {
        "upload": {
            "fields": {
                field_name: [
                    {
                        "filename": os.path.basename(file_path),
                        "bytes": content,
                        "contentType": "application/pdf",
                    }
                ]
            },
            "tableIdOrName": PROSPECTS_TABLE,
            "recordId": record_id,
        }
    }

    r = requests.post(upload_url, headers=_h(), json=payload)
    r.raise_for_status()
    return True

# -------- Main Orchestrator -------- #

def generate_and_email_reports_for_legacy_code(legacy_code: str):
    """
    NEW VERSION:
    ✔ Fetch Prospect + Deep Dive
    ✔ Generate both PDFs
    ✔ Upload BOTH PDFs to Airtable as attachments
    ✔ NO EMAIL
    """

    prospect, deepdive, record_id = get_prospect_and_deepdive(legacy_code)

    # Generate content
    prospect_text = generate_prospect_report_text(prospect, deepdive)
    consult_text = generate_consult_report_text(prospect, deepdive)

    # Build HTML
    prospect_html = build_prospect_html(prospect, legacy_code, prospect_text)
    consult_html = build_consult_html(prospect, legacy_code, consult_text)

    # Output paths
    base = f"/tmp/{legacy_code.replace(' ', '_')}"
    prospect_pdf_path = base + "_prospect_90_day_blueprint.pdf"
    consult_pdf_path = base + "_consultation_briefing.pdf"

    # Render PDFs
    html_to_pdf(prospect_html, prospect_pdf_path)
    html_to_pdf(consult_html, consult_pdf_path)

    # Upload PDFs into Airtable
    attach_pdf_to_airtable(record_id, "90 Day Blueprint PDF", prospect_pdf_path)
    attach_pdf_to_airtable(record_id, "Consultation Briefing PDF", consult_pdf_path)

    return {
        "prospect_pdf": prospect_pdf_path,
        "consult_pdf": consult_pdf_path,
        "airtable_record": record_id,
    }
