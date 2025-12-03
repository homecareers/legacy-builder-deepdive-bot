import os
import json
import datetime
import urllib.parse
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# AGGRESSIVE proxy removal - remove EVERYTHING proxy-related
import os as _os
for key in list(_os.environ.keys()):
    if 'proxy' in key.lower() or 'PROXY' in key:
        del _os.environ[key]

# Set NO_PROXY to prevent any proxy usage
_os.environ['NO_PROXY'] = '*'
_os.environ['no_proxy'] = '*'

from openai import OpenAI

# ---------------------- CONFIG ---------------------- #

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

SURVEY_TABLE = os.getenv("AIRTABLE_PROSPECTS_TABLE") or "Survey Responses"

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")

OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

# Force simple initialization
try:
    client = OpenAI()  # Let it use OPENAI_API_KEY env var directly
except Exception as e:
    print(f"Warning: OpenAI init issue: {e}")
    # Fallback - import without client if needed
    client = None


def _airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }


def _airtable_url(table: str, record_id: str | None = None, params: dict | None = None) -> str:
    base = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{urllib.parse.quote(table)}"
    if record_id:
        return f"{base}/{record_id}"
    if params:
        return f"{base}?{urllib.parse.urlencode(params)}"
    return base


# ---------------------- AIRTABLE LOOKUP ---------------------- #

def find_survey_row(prospect_email: str | None = None,
                    legacy_code: str | None = None) -> dict | None:
    if not prospect_email and not legacy_code:
        print("⚠️ find_survey_row: no email or legacy_code provided.")
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
            print(f"❌ Airtable lookup error with formula [{formula}]: {e}")

    print("⚠️ No Survey Responses row found for given keys.")
    return None


def extract_q_block(fields: dict) -> dict:
    q_data: dict[str, str | None] = {}

    for i in range(1, 31):
        prefix = f"Q{i}"
        value = None
        for k, v in fields.items():
            if k.startswith(prefix):
                value = v
                break
        q_data[prefix] = value

    return q_data


# ---------------------- OPENAI HELPERS ---------------------- #

def call_openai(messages: list[dict], temperature: float = 0.7) -> str:
    if not client:
        print("❌ OpenAI client not initialized")
        return "Report generation failed. (Client initialization error.)"
    
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        return "Report generation failed. (Model error.)"


def build_prospect_prompt(meta: dict, q_data: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are an elite business and behavior strategist. "
                "You create precise, practical 90-day action blueprints for new builders. "
                "Tone: grounded, confident, direct, no fluff. "
                "Write in second-person ('you'). "
                "Focus on clarity, priorities, and behavior systems — not hype."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "meta": meta,
                    "questions": q_data,
                    "instructions": (
                        "Using ALL available answers from Q1–Q30, create a 90-day business blueprint. "
                        "Structure with clear sections, like:\n"
                        "1) Snapshot of Where You Are Now\n"
                        "2) 90-Day Targets\n"
                        "3) Weekly Non-Negotiables\n"
                        "4) Daily Operating System\n"
                        "5) Risk Factors & How You'll Handle Them\n"
                        "6) Check-in Milestones\n\n"
                        "Keep it readable, concrete, and implementable."
                    ),
                },
                indent=2,
            ),
        },
    ]


def build_coach_prompt(meta: dict, q_data: dict) -> list[dict]:
    gem_style = q_data.get("Q6") or ""
    return [
        {
            "role": "system",
            "content": (
                "You are a senior builder-coach preparing a consultation briefing for another coach. "
                "Tone: tactical, candid, zero fluff. "
                "Write in third-person about the prospect. "
                "Highlight GEM-style clues, red flags, leverage points, and coaching strategy."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "meta": meta,
                    "questions": q_data,
                    "gem_hint": gem_style,
                    "instructions": (
                        "Create a consultation briefing with sections:\n"
                        "1) Identity Snapshot (who they are, GEM-style, story)\n"
                        "2) Motivation & Real Drivers (Q1, others)\n"
                        "3) Capacity & Constraints (time, life context, bandwidth)\n"
                        "4) Confidence, Patterns & Past Friction (what derailed them before)\n"
                        "5) Recommended Coaching Angle (how to lead them in first 90 days)\n"
                        "6) Key Questions to Ask Live\n\n"
                        "Assume this is used right before a 30–45 min consult."
                    ),
                },
                indent=2,
            ),
        },
    ]


# ---------------------- HTML → PDF ---------------------- #

def html_shell(title: str, legacy_code: str | None, body_html: str) -> str:
    meta_line = f"{title}"
    if legacy_code:
        meta_line += f" · {legacy_code}"

    generated_ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    template = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif;
    background: #05060a;
    color: #f5f5f5;
    margin: 0;
    padding: 32px;
  }}
  .card {{
    max-width: 800px;
    margin: 0 auto;
    background: #0b0c10;
    border-radius: 18px;
    padding: 28px 30px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.85);
    border: 1px solid #222;
  }}
  h1 {{
    font-size: 26px;
    margin: 0 0 6px;
  }}
  .meta {{
    font-size: 12px;
    color: #c3c3c3;
    margin-bottom: 16px;
  }}
  h2 {{
    font-size: 18px;
    margin-top: 22px;
    margin-bottom: 6px;
    color: #f7cb4e;
  }}
  p, li {{
    font-size: 13px;
    line-height: 1.6;
  }}
  ul {{
    padding-left: 18px;
  }}
</style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <div class="meta">
      {meta_line}<br>
      Generated: {generated_ts}
    </div>
    {body_html}
  </div>
</body>
</html>
""".strip()

    return template.format(
        title=title,
        meta_line=meta_line,
        generated_ts=generated_ts,
        body_html=body_html,
    )


def markdownish_to_html(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        if p.startswith("# "):
            html_parts.append(f"<h2>{p[2:].strip()}</h2>")
        else:
            html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def html_to_pdf(html: str, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(path=str(output_path), format="A4", print_background=True)
        browser.close()


# ---------------------- ATTACH TO AIRTABLE ---------------------- #

def attach_pdfs_to_airtable(record_id: str,
                            prospect_pdf_url: str | None,
                            coach_pdf_url: str | None):
    if not (prospect_pdf_url or coach_pdf_url):
        print("⚠️ No PDF URLs to attach.")
        return

    fields = {}
    if prospect_pdf_url:
        fields["90 Day Blueprint PDF"] = [{"url": prospect_pdf_url}]
    if coach_pdf_url:
        fields["Consultation Briefing PDF"] = [{"url": coach_pdf_url}]

    try:
        r = requests.patch(
            _airtable_url(SURVEY_TABLE, record_id),
            headers=_airtable_headers(),
            json={"fields": fields},
            timeout=20,
        )
        r.raise_for_status()
        print(f"✅ Attached PDFs to Airtable record {record_id}")
    except Exception as e:
        print(f"❌ Failed to attach PDFs to Airtable: {e}")


# ---------------------- PUBLIC ENTRYPOINT ---------------------- #

def generate_reports_for_email_or_legacy_code(prospect_email: str | None = None,
                                              legacy_code: str | None = None,
                                              public_base_url: str | None = None) -> dict:
    result = {"ok": False, "reason": None}

    record = find_survey_row(prospect_email, legacy_code)
    if not record:
        result["reason"] = "no_record"
        return result

    record_id = record["id"]
    fields = record.get("fields", {})

    legacy_code_val = fields.get("Legacy Code") or legacy_code
    meta = {
        "prospect_name": fields.get("Prospect Name"),
        "prospect_email": fields.get("Prospect Email"),
        "legacy_code": legacy_code_val,
        "date_submitted": fields.get("Date Submitted"),
    }

    q_data = extract_q_block(fields)

    prospect_messages = build_prospect_prompt(meta, q_data)
    coach_messages = build_coach_prompt(meta, q_data)

    prospect_text = call_openai(prospect_messages, temperature=0.65)
    coach_text = call_openai(coach_messages, temperature=0.55)

    prospect_html_body = markdownish_to_html(prospect_text)
    coach_html_body = markdownish_to_html(coach_text)

    prospect_html = html_shell("90-Day Business Blueprint", legacy_code_val, prospect_html_body)
    coach_html = html_shell("Consultation Briefing", legacy_code_val, coach_html_body)

    reports_dir = Path(os.getenv("REPORTS_DIR") or "reports")
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    safe_suffix = legacy_code_val or (prospect_email or "prospect").replace("@", "_at_")
    safe_suffix = "".join(c for c in safe_suffix if c.isalnum() or c in ("-", "_"))

    prospect_pdf_name = f"blueprint_{safe_suffix}_{timestamp}.pdf"
    coach_pdf_name = f"briefing_{safe_suffix}_{timestamp}.pdf"

    prospect_pdf_path = reports_dir / prospect_pdf_name
    coach_pdf_path = reports_dir / coach_pdf_name

    try:
        html_to_pdf(prospect_html, prospect_pdf_path)
        html_to_pdf(coach_html, coach_pdf_path)
    except Exception as e:
        print(f"❌ PDF generation error: {e}")
        result["reason"] = "pdf_error"
        return result

    base_url = (public_base_url or PUBLIC_BASE_URL or "").rstrip("/")
    if base_url:
        prospect_url = f"{base_url}/reports/{prospect_pdf_name}"
        coach_url = f"{base_url}/reports/{coach_pdf_name}"
    else:
        print("⚠️ No PUBLIC_BASE_URL set; PDFs will not be attached.")
        prospect_url = coach_url = None

    attach_pdfs_to_airtable(record_id, prospect_url, coach_url)

    result.update(
        {
            "ok": True,
            "record_id": record_id,
            "prospect_pdf": prospect_pdf_name,
            "coach_pdf": coach_pdf_name,
        }
    )
    return result
