from flask import Flask, request, jsonify
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


# ---------------------- SERVE HTML - EMBEDDED VERSION ---------------------- #

@app.route("/")
def serve_chat():
    """Serve the chat interface directly"""
    return '''<!DOCTYPE html>
<html>
<head>
  <title>ANGUS™ Legacy Builder Plan — Deep Dive</title>
  <style>
    body {
      font-family: 'Inter', Arial, sans-serif;
      background: #050608;
      color: #f5f5f5;
      margin: 0;
      padding: 20px;
      min-height: 100vh;
    }

    .chat-box {
      max-width: 680px;
      margin: 40px auto;
      background: #0f0f0f;
      padding: 30px;
      border-radius: 18px;
      box-shadow: 0 24px 60px rgba(0,0,0,0.85);
      border: 1px solid #222;
    }

    .message {
      margin: 16px 0;
      line-height: 1.6;
      animation: fadeIn 0.4s ease-in;
    }

    .bot {
      color: #f5f5f5;
    }

    .user {
      color: #D4A72C;
      text-align: right;
      font-weight: 500;
    }

    .bot strong {
      color: #D4A72C;
    }

    .options {
      margin: 20px 0;
    }

    .options button {
      display: block;
      margin: 8px 0;
      width: 100%;
      padding: 12px 16px;
      border-radius: 10px;
      border: 1px solid #D4A72C;
      background: #111111;
      color: #f5f5f5;
      cursor: pointer;
      font-size: 15px;
      transition: all 0.22s ease;
      text-align: left;
      letter-spacing: 0.2px;
    }

    .options button:hover {
      background: #D4A72C;
      color: #050608;
      transform: translateY(-1px);
      box-shadow: 0 0 18px rgba(212,167,44,0.45);
    }

    .input-wrap {
      display: flex;
      gap: 10px;
      margin-top: 16px;
    }

    .input-wrap input {
      flex: 1;
      padding: 12px;
      border-radius: 10px;
      border: 2px solid #333;
      background: #111;
      color: #f5f5f5;
      font-size: 15px;
      box-sizing: border-box;
    }

    .input-wrap input:focus {
      outline: none;
      border-color: #D4A72C;
      box-shadow: 0 0 10px rgba(212,167,44,0.35);
    }

    .next-btn {
      padding: 0 18px;
      border-radius: 10px;
      border: none;
      background: #D4A72C;
      color: #050608;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.18s ease;
    }

    .next-btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 0 14px rgba(212,167,44,0.55);
    }

    .loading {
      color: #D4A72C;
      font-style: italic;
    }

    .loading::after {
      content: '...';
      animation: dots 1.5s infinite;
    }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes dots {
      0% { content: ''; }
      33% { content: '.'; }
      66% { content: '..'; }
      100% { content: '...'; }
    }

    @media (max-width: 600px) {
      .chat-box {
        margin: 20px auto;
        padding: 20px;
      }
      .input-wrap {
        flex-direction: column;
      }
      .next-btn {
        width: 100%;
        padding: 10px 0;
      }
    }
  </style>
</head>

<body>
  <div class="chat-box" id="chat"></div>

  <script>
    const chat = document.getElementById('chat');
    const BACKEND_URL = "/submit";

    // ---------------------- Deep-Dive Questions (7–14) ---------------------- //
    const surveyFlow = [
      {
        id: "q7_history",
        type: "text",
        question: "Ever played in business territory before? Drop anything you've tried (affiliate, coaching, network marketing, content, etc.) — or say 'Never tried it' if you're brand new.",
        placeholder: "Give me the quick version of your business story so far."
      },
      {
        id: "q8_goal_style",
        type: "options",
        question: "When you go after a big goal, which style feels MOST like you?",
        options: [
          "I want a clear step-by-step game plan.",
          "I move fast and like quick wins to stay fired up.",
          "I like deeper strategy and long-term plays.",
          "I do best when someone checks in and keeps me accountable."
        ]
      },
      {
        id: "q8a_style_notes",
        type: "text",
        question: "Anything you want me to know about your style when you're chasing a goal? (Optional, but it helps me coach you cleaner.)",
        placeholder: "Example: 'I overthink.' 'I need reminders.' 'Once I commit, I'm locked in.'"
      },
      {
        id: "q9_obstacles",
        type: "text",
        question: "When you've chased big dreams before — in business or wellness — what usually tripped you up? Time, fear, energy, guidance, something else?",
        placeholder: "Be real here so we don't repeat the same pattern."
      },
      {
        id: "q10_personal_wins",
        type: "text",
        question: "Outside of money, what personal wins matter most to you in the next 6–12 months?",
        placeholder: "For example: confidence, energy, time freedom, being a stronger example, etc."
      },
      {
        id: "q11_ideal_coach",
        type: "text",
        question: "Picture your ideal coach. What do you want MOST from them?",
        placeholder: "Give me your wishlist: plan, accountability, real talk, community, etc."
      },
      {
        id: "q12_online_presence",
        type: "text",
        question: "Where do you show up online right now — and how big is your audience there?",
        placeholder: "Example: TikTok ~300, Instagram ~1,200, Facebook personal page only, etc."
      },
      {
        id: "q13_best_phone",
        type: "phone",
        question: "What's your best phone number in case we need to send you important updates or next steps?",
        placeholder: "Drop your mobile number here."
      },
      {
        id: "q14_best_email",
        type: "email",
        question: "And what's the best email for your custom Legacy Builder plan to land in?",
        placeholder: "Best email for your plan."
      }
    ];

    let step = 0;
    let answers = {};

    // ---------------------- Helpers ---------------------- //
    function showBotMessage(msg) {
      chat.innerHTML += `<div class="message bot">${msg}</div>`;
      chat.scrollTop = chat.scrollHeight;
    }

    function showUserMessage(msg) {
      chat.innerHTML += `<div class="message user">${msg}</div>`;
      chat.scrollTop = chat.scrollHeight;
    }

    function showLoading() {
      chat.innerHTML += `<div class="message loading" id="loading">ANGUS is thinking</div>`;
      chat.scrollTop = chat.scrollHeight;
    }

    function hideLoading() {
      const loading = document.getElementById('loading');
      if (loading) loading.remove();
    }

    // ---------------------- Flow Control ---------------------- //
    function askNext() {
      if (step >= surveyFlow.length) {
        finishSurvey();
        return;
      }

      const q = surveyFlow[step];

      showLoading();
      setTimeout(() => {
        hideLoading();
        const label = `Step ${step + 1} of ${surveyFlow.length}`;
        showBotMessage(`<strong>${label}</strong><br><br>${q.question}`);
        renderInput(q);
      }, 800);
    }

    function renderInput(q) {
      if (q.type === "options") {
        let optionsHTML = "<div class='options'>";
        q.options.forEach(opt => {
          const esc = opt.replace(/'/g, "\\\\'");
          optionsHTML += `<button onclick="selectOption('${esc}')">${opt}</button>`;
        });
        optionsHTML += "</div>";
        chat.innerHTML += optionsHTML;
      } else {
        const placeholder = q.placeholder || "Type your answer and press Enter.";
        const inputType = q.type === "email" ? "email" : (q.type === "phone" ? "tel" : "text");

        chat.innerHTML += `
          <div class="input-wrap">
            <input id="textInput" type="${inputType}"
                   placeholder="${placeholder.replace(/"/g, '&quot;')}"
                   onkeydown="if(event.key==='Enter'){submitTextAnswer();}">
            <button class="next-btn" onclick="submitTextAnswer()">Next</button>
          </div>
        `;
        const input = document.getElementById("textInput");
        if (input) input.focus();
      }

      chat.scrollTop = chat.scrollHeight;
    }

    function selectOption(option) {
      document.querySelectorAll('.options').forEach(div => div.remove());
      showUserMessage(option);
      const q = surveyFlow[step];
      answers[q.id] = option;
      step++;
      setTimeout(askNext, 400);
    }

    function submitTextAnswer() {
      const input = document.getElementById('textInput');
      if (!input) return;

      const value = input.value.trim();
      if (!value) {
        const q = surveyFlow[step];
        // Allow optional fields to be skipped
        if (q.id === "q8a_style_notes") {
          showUserMessage("(Skipped)");
          answers[q.id] = "";
          input.parentElement.remove();
          step++;
          setTimeout(askNext, 400);
          return;
        }
        showBotMessage("Give me a quick line here so I can dial this in with you.");
        return;
      }

      showUserMessage(value);
      const q = surveyFlow[step];
      answers[q.id] = value;

      input.parentElement.remove();
      step++;
      setTimeout(askNext, 400);
    }

    // ---------------------- Submit Logic ---------------------- //
    function finishSurvey() {
      const email = (answers["q14_best_email"] || "").trim();
      const phone = (answers["q13_best_phone"] || "").trim();

      if (!email) {
        showBotMessage("I'll need your best email so we know where to send your Legacy Builder plan.");
        return;
      }

      submitSurvey(email, phone);
    }

    function submitSurvey(email, phone) {
      showLoading();

      const payload = {
        email: email,
        phone: phone,
        answers: answers,
        survey_type: "deepdive"
      };

      fetch(BACKEND_URL, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      })
      .then(res => res.json())
      .then(data => {
        hideLoading();
        if (data.redirect_url) {
          showBotMessage("Perfect! Taking you to your personalized Legacy Builder plan now...");
          setTimeout(() => {
            window.top.location.href = data.redirect_url;
          }, 1500);
        } else {
          showBotMessage("Something glitched on the handoff. Refresh and try again.");
        }
      })
      .catch(err => {
        console.error("Full error:", err);
        hideLoading();
        showBotMessage("There was an issue sending your answers. Refresh and try once more.");
      });
    }

    // ---------------------- Intro ---------------------- //
    window.onload = () => {
      showBotMessage("Alright, legend — that first round gave me your big picture. Now let's lock in the details for your Legacy Builder plan.");
      setTimeout(() => {
        showBotMessage("These next questions are quick, but they help me tailor this with you — not at you.");
        setTimeout(askNext, 1200);
      }, 1200);
    };

    // expose for inline handlers
    window.selectOption = selectOption;
    window.submitTextAnswer = submitTextAnswer;
  </script>
</body>
</html>'''


@app.route("/test")
def test():
    """Test route to verify server is running"""
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
    """
    Search for Prospect by email. If exists → return it.
    If not → create new + assign Legacy Code.
    """
    
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        print("Warning: Airtable credentials not configured")
        return "TEST-LC-001", "TEST-ID-001"

    formula = f"{{Prospect Email}} = '{email}'"
    search_url = _url(HQ_TABLE, params={"filterByFormula": formula, "maxRecords": 1})

    try:
        r = requests.get(search_url, headers=_h())
        r.raise_for_status()
        data = r.json()

        # ------------------ FOUND EXISTING PROSPECT ------------------ #
        if data.get("records"):
            rec = data["records"][0]
            rec_id = rec["id"]
            legacy_code = rec.get("fields", {}).get("Legacy Code")

            # Missing LC? Generate one.
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

        # ------------------ CREATE NEW PROSPECT ------------------ #
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


# ---------------------- SAVE DEEP-DIVE (Q7–Q14) ---------------------- #

def save_deepdive_to_airtable(legacy_code: str, prospect_id: str, email: str, phone: str, answers: dict):
    """Update prospect with deep-dive answers"""
    
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        print("Warning: Airtable credentials not configured")
        return "TEST-DEEPDIVE-ID"
    
    fields = {
        "Phone": phone,
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


# ---------------------- SYNC TO GHL ---------------------- #

def push_deepdive_to_ghl(email: str, phone: str, answers: dict, legacy_code: str):
    """Sync deep-dive data to GHL"""
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
            "phone": phone,
            "customField": {
                "q7_business_history": answers.get("q7_history", ""),
                "q8_goal_style": answers.get("q8_goal_style", ""),
                "q8a_style_notes": answers.get("q8a_style_notes", ""),
                "q9_obstacles": answers.get("q9_obstacles", ""),
                "q10_personal_wins": answers.get("q10_personal_wins", ""),
                "q11_ideal_coach": answers.get("q11_ideal_coach", ""),
                "q12_online_presence": answers.get("q12_online_presence", ""),
                "deepdive_completed": "true",
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
        phone = data.get("phone") or ""
        answers_raw = data.get("answers") or {}
        survey_type = data.get("survey_type") or "deepdive"

        if not email:
            return jsonify({"error": "Missing email"}), 400

        print(f"Processing deep-dive for {email}")
        
        # Get or create prospect
        legacy_code, prospect_id = get_or_create_prospect(email)
        
        # Save to Airtable
        save_deepdive_to_airtable(legacy_code, prospect_id, email, phone, answers_raw)
        
        # Sync to GHL
        assigned_user_id = push_deepdive_to_ghl(email, phone, answers_raw, legacy_code)
        
        # Build redirect
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


# ---------------------- RAILWAY PORT FIX (IMPORTANT) ---------------------- #

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Legacy Builder Deep-Dive Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
