# reports.py
import os
import datetime
import base64
import urllib.parse
import requests

from playwright.sync_api import sync_playwright
from openai import OpenAI

# -------- CONFIG -------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

PROSPECTS_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Prospects"
DEEPDIVE_TABLE = os.getenv("AIRTABLE_DEEPDIVE_TABLE") or "Deep Dive Responses"

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("REPORTS_FROM_EMAIL")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID):
    raise RuntimeError("Airtable env vars missing")

if not (OPENAI_API_KEY and SENDGRID_API_KEY and FROM_EMAIL):
    raise RuntimeError("Reporting env vars missing")

client = OpenAI(api_key=OPENAI_API_KEY)


# -------- Airtable helpers -------- #

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
    """
    Gets the most recent record by Legacy Code from given table.
    Assumes there is a 'Legacy Code' field and optional 'Date Submitted'.
    """
    formula = f"{{Legacy Code}} = '{legacy_code}'"
    params = {
        "filterByFormula": formula,
        "maxRecords": 1,
    }
    # If Deep Dive table has Date Submitted, we sort by it.
    if table == DEEPDIVE_TABLE:
        params["sort[0][field]"] = "Date Submitted"
        params["sort[0][direction]"] = "desc"

    r = requests.get(_url(table, params=params), headers=_h())
    r.raise_for_status()
    data = r.json()
    records = data.get("records", [])
    if not records:
        return None
    return records[0]


def get_prospect_and_deepdive(legacy_code: str):
    """
    Pulls:
    - Prospect record (name, emails, GEM, etc.)
    - Latest Deep Dive record (all 30 answers)
    """
    prospect_rec = get_record_by_legacy_code(PROSPECTS_TABLE, legacy_code)
    if not prospect_rec:
        raise ValueError(f"No Prospect found with Legacy Code {legacy_code}")

    deepdive_rec = get_record_by_legacy_code(DEEPDIVE_TABLE, legacy_code)
    if not deepdive_rec:
        raise ValueError(f"No Deep Dive record found for Legacy Code {legacy_code}")

    return prospect_rec["fields"], deepdive_rec["fields"]


# -------- GPT content generation -------- #

def _deepdive_to_bullet_block(fields: dict) -> str:
    """
    Turns Airtable Deep Dive fields into a bullets block for GPT.
    """
    lines = []
    for k, v in fields.items():
        # Skip technical/system fields
        if k in ("Legacy Code", "Prospects", "Created", "Date Submitted"):
            continue
        if v is None or v == "":
            continue
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _call_gpt(system_prompt: str, user_prompt: str) -> str:
    """
    Wraps OpenAI call.
    """
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=1800,
    )
    return resp.choices[0].message.content


def generate_prospect_report_text(prospect: dict, deepdive: dict) -> str:
    """
    Text for the 90-Day Prospect Blueprint (prospect-facing language).
    This still gets emailed only to the sponsor.
    """
    name = prospect.get("Prospect Name") or "this client"
    gem = deepdive.get("Q6 Business Style (GEM)") or deepdive.get("GEM Type") or ""
    bullets = _deepdive_to_bullet_block(deepdive)

    system_prompt = """
You are a strategic Herbalife-aligned business coach.
Stay 100% compliant: do not make income guarantees, health claims, or disease language.
You are creating a clear ACTION blueprint, not promising results.

Rules:
- Use words like: support, guide, help, optimize, align.
- No curing, healing, treating, or guaranteed income.
- Frame any numbers as scenarios or examples, not promises.
- Focus on behaviors, routines, DMO, and trackable actions.
- Style: Navy SEAL instructor meets elite wellness strategist.
- Short sections, clear headers, bullet points. No fluff.
    """.strip()

    user_prompt = f"""
The prospect is: {name}
Their GEM style: {gem}

Their Deep Dive answers:
{bullets}

Create a 90-Day Business Blueprint that includes:

1) SNAPSHOT
   - Who they are
   - Their primary driver
   - Their GEM lens (how they best respond)

2) 90-DAY GAME PLAN (PHASED)
   - Phase 1 (Weeks 1–4): foundation behaviors, learning, and daily minimum DMO
   - Phase 2 (Weeks 5–8): skill-building, social proof, basic invites, tracking
   - Phase 3 (Weeks 9–12): tightening scripts, follow-up rhythm, simple duplication

3) DAILY / WEEKLY DMO
   - Concrete daily actions (simple list)
   - Weekly non-negotiables
   - How to track so their coach can review

4) RISK FACTORS & SUPPORT PLAN
   - Based on their past patterns & obstacles
   - How their coach should support them
   - How THEY should support themselves

5) BUSINESS PROJECTION (SCENARIO-BASED)
   - Show 1–2 sample scenarios for what could happen in 90 days
   - Use language like: "For example, if you complete X invites per week, you could potentially see..."
   - Absolutely no income guarantees or health claims.

Write it directly to the prospect, in second person ("you"), but keep it grounded and precise.
    """.strip()

    return _call_gpt(system_prompt, user_prompt)


def generate_consult_report_text(prospect: dict, deepdive: dict) -> str:
    """
    Text for the Sponsor Consultation Briefing (coach-facing language).
    """
    name = prospect.get("Prospect Name") or "this prospect"
    email = prospect.get("Prospect Email") or ""
    gem = deepdive.get("Q6 Business Style (GEM)") or deepdive.get("GEM Type") or ""
    bullets = _deepdive_to_bullet_block(deepdive)

    system_prompt = """
You are coaching a sponsor/coach on how to lead a 1:1 business consultation.
You must stay 100% compliant:
- No promises of income.
- No disease or medical claims.
- No hype.

Your job:
- Decode the prospect's GEM type and behavior.
- Give the sponsor a tactical game plan for the consult.
- Focus on questions, listening, and framing — not pitching.

Tone:
- Clear, direct, mentor-level.
- Practical, bullet-heavy, easy to skim.
    """.strip()

    user_prompt = f"""
Prospect:
- Name: {name}
- Email: {email}
- GEM style: {gem}

Deep Dive data:
{bullets}

Create a CONSULTATION BRIEFING for the sponsor with these sections:

1) GEM SNAPSHOT & COMMUNICATION STYLE
   - What this GEM cares about most
   - How to speak so they feel seen
   - Words to lean into, words to avoid

2) BIG 3 MOTIVATORS (BASED ON THEIR ANSWERS)
   - What is really driving them
   - What to reflect back early in the call

3) RED FLAGS & FRICTION POINTS
   - Likely obstacles (time, confidence, structure, past failures, etc.)
   - Questions to gently surface each one

4) CONSULTATION FLOW (STEP-BY-STEP)
   - Opening (2–3 key lines)
   - Discovery questions (5–8 specific questions)
   - How to present the 90-day blueprint in their GEM language
   - How to frame expectations (effort, time, learning curve) without hype
   - How to close ethically (inviting them into action without pressure)

5) FOLLOW-UP & ACCOUNTABILITY PLAN
   - How often to check in with them
   - What to track (behaviors, not just outcomes)
   - What "win" looks like at 30, 60, 90 days (behavioral, not income)

Write this as if you are giving the sponsor a private briefing before the Zoom.
Use clear headings and bullets. No fluff.
    """.strip()

    return _call_gpt(system_prompt, user_prompt)


# -------- HTML templating -------- #

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
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      background: #050608;
      color: #f5f5f5;
      margin: 0;
      padding: 32px;
    }}
    .card {{
      max-width: 780px;
      margin: 0 auto;
      background: #0f0f0f;
      border-radius: 18px;
      padding: 32px 36px;
      border: 1px solid #222;
    }}
    h1, h2, h3 {{
      color: #D4A72C;
    }}
    h1 {{
      font-size: 26px;
      margin-bottom: 4px;
    }}
    .meta {{
      font-size: 13px;
      color: #aaaaaa;
      margin-bottom: 20px;
    }}
    .section {{
      margin: 18px 0;
    }}
    ul {{
      padding-left: 20px;
    }}
    li {{
      margin: 4px 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>90-Day Business Blueprint</h1>
    <div class="meta">
      Prospect: {name}<br>
      Legacy Code: {legacy_code}<br>
      Generated: {today}
    </div>
    <div class="section">
      {body.replace('\n', '<br>')}
    </div>
  </div>
</body>
</html>
    """.strip()


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
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      background: #050608;
      color: #f5f5f5;
      margin: 0;
      padding: 32px;
    }}
    .card {{
      max-width: 780px;
      margin: 0 auto;
      background: #0f0f0f;
      border-radius: 18px;
      padding: 32px 36px;
      border: 1px solid #222;
    }}
    h1, h2, h3 {{
      color: #D4A72C;
    }}
    h1 {{
      font-size: 26px;
      margin-bottom: 4px;
    }}
    .meta {{
      font-size: 13px;
      color: #aaaaaa;
      margin-bottom: 20px;
    }}
    .section {{
      margin: 18px 0;
    }}
    ul {{
      padding-left: 20px;
    }}
    li {{
      margin: 4px 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Consultation Briefing</h1>
    <div class="meta">
      Prospect: {name} ({email})<br>
      Legacy Code: {legacy_code}<br>
      Generated: {today}
    </div>
    <div class="section">
      {body.replace('\n', '<br>')}
    </div>
  </div>
</body>
</html>
    """.strip()


# -------- Playwright PDF engine -------- #

def html_to_pdf(html: str, out_path: str):
    """
    Renders HTML to PDF using Playwright + headless Chromium.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=out_path,
            format="A4",
            print_background=True,
            margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
        )
        browser.close()


# -------- Email sender (SendGrid) -------- #

def send_reports_email(to_email: str, subject: str, text_body: str, attachments: list):
    """
    attachments: list of dicts with keys: filename, path
    """
    if not to_email:
        raise ValueError("No recipient email for reports")

    url = "https://api.sendgrid.com/v3/mail/send"

    files_payload = []
    for att in attachments:
        with open(att["path"], "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
        files_payload.append(
            {
                "content": content,
                "type": "application/pdf",
                "filename": att["filename"],
                "disposition": "attachment",
            }
        )

    data = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
            }
        ],
        "from": {"email": FROM_EMAIL, "name": "Legacy Code™ Reports"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": text_body}],
        "attachments": files_payload,
    }

    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }

    r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()


# -------- Orchestrator -------- #

def generate_and_email_reports_for_legacy_code(legacy_code: str):
    """
    Main entry point:
    - Fetch Prospect + Deep Dive
    - Generate 2 texts via GPT
    - Render 2 PDFs via Playwright
    - Email BOTH PDFs to the sponsor (Assigned Op Email).
    """
    prospect, deepdive = get_prospect_and_deepdive(legacy_code)

    sponsor_email = (
        prospect.get("Assigned Op Email")
        or prospect.get("Assigned Op Email (from Users)")
        or prospect.get("Prospect Email")
    )

    # 1) Generate texts
    prospect_text = generate_prospect_report_text(prospect, deepdive)
    consult_text = generate_consult_report_text(prospect, deepdive)

    # 2) Build HTML
    prospect_html = build_prospect_html(prospect, legacy_code, prospect_text)
    consult_html = build_consult_html(prospect, legacy_code, consult_text)

    # 3) Paths (Railway /tmp is safe)
    base = f"/tmp/{legacy_code.replace(' ', '_')}"
    prospect_pdf_path = base + "_prospect_90_day_blueprint.pdf"
    consult_pdf_path = base + "_consultation_briefing.pdf"

    html_to_pdf(prospect_html, prospect_pdf_path)
    html_to_pdf(consult_html, consult_pdf_path)

    # 4) Email to sponsor
    subject = f"Legacy Code™ Reports — {legacy_code}"
    body = (
        "Attached are the two reports for your upcoming consultation:\n\n"
        "1) 90-Day Business Blueprint (prospect-facing language)\n"
        "2) Consultation Briefing (coach-only)\n\n"
        "Reminder: These are scenario-based guides, not guarantees of any specific business or "
        "health outcomes. Use them to support behaviors, tracking, and aligned expectations."
    )

    send_reports_email(
        to_email=sponsor_email,
        subject=subject,
        text_body=body,
        attachments=[
            {"filename": "90-day-blueprint.pdf", "path": prospect_pdf_path},
            {"filename": "consultation-briefing.pdf", "path": consult_pdf_path},
        ],
    )

    return {
        "prospect_pdf": prospect_pdf_path,
        "consult_pdf": consult_pdf_path,
        "sent_to": sponsor_email,
    }
