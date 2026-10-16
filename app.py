"""Helpora - a customer-support chatbot that answers ONLY from a knowledge base (RAG).
Retrieval : TF-IDF style keyword scoring over knowledge_base/*.md (Q:/A: pairs)
AI mode   : Claude or Gemini write the answer from the retrieved passages (key in .env)
Offline   : returns the best-matching FAQ answer, no key needed
Escalation: unanswered questions can be sent to a human agent (support ticket)."""
import os, re, json, time, math, glob, uuid, functools
from datetime import datetime, timezone
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
COMPANY = "ShopEase"
TICKETS = os.getenv("TICKETS_FILE", os.path.join(BASE, "tickets.json"))
NOT_FOUND = "I couldn't find this information. Would you like me to connect you with a support agent?"
STOP = set("a an the is are was were be been to of in on at for and or my your i me we you it its do does did can "
           "could would will how what when where which who why with this that there any about please tell want need "
           "have has get if so am m s t".split())
SYN = {"arrive": "delivery", "arrival": "delivery", "reach": "delivery", "status": "track", "cost": "charge",
       "price": "charge", "fee": "charge", "money": "refund", "cod": "cash", "pay": "payment", "paid": "payment"}

PROMPT = """You are Helpora, a friendly customer support assistant for <<CO>>.
Answer ONLY from the knowledge base excerpts below. Never invent policies, prices, dates, order details or contact info.
If the excerpts do not contain the answer, set "found" to false. Keep answers short (1-4 sentences).
Reply with ONLY valid JSON: {"found": true or false, "answer": "...", "sources": ["section names you used"]}

KNOWLEDGE BASE:
<<C>>

CONVERSATION SO FAR:
<<H>>

CUSTOMER QUESTION: <<Q>>"""


def stem(w):
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if w.endswith(suf) and not w.endswith("ss") and len(w) - len(suf) >= 3:
            w = w[:-len(suf)] + rep
            if suf in ("ing", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]
            return w
    return w


def toks(t):
    return {stem(w) for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP}


def qtoks(q):
    ql = q.lower()
    t = toks(ql)
    t |= {stem(SYN[w]) for w in re.findall(r"[a-z0-9]+", ql) if w in SYN}
    if re.search(r"\bwhere\b", ql) and "order" in ql:
        t.add("track")
    return t


def load_kb():
    chunks = []
    for f in sorted(glob.glob(os.path.join(BASE, "knowledge_base", "*.md"))):
        sec = ""
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("# "):
                    sec = line[2:]
                elif line.startswith("Q:"):
                    chunks.append({"section": sec, "q": line[2:].strip(), "a": ""})
                elif line.startswith("A:") and chunks:
                    chunks[-1]["a"] = line[2:].strip()
    for c in chunks:
        c["qt"], c["at"] = toks(c["q"]), toks(c["a"])
    return chunks


KB = load_kb()


def has(t, S):
    return t in S or any(min(len(s), len(t)) >= 4 and (s.startswith(t) or t.startswith(s)) for s in S)


@functools.lru_cache(maxsize=None)
def idf(t):
    df = sum(1 for c in KB if has(t, c["qt"] | c["at"]))
    return math.log(1 + len(KB) / (1 + df)) if df else 0.8


def search(q, k=4):
    qt = qtoks(q)
    if not qt:
        return []
    tot = sum(idf(t) for t in qt)
    sc = [(sum(idf(t) * (1 if has(t, c["qt"]) else 0.6 if has(t, c["at"]) else 0) for t in qt) / tot, c) for c in KB]
    return sorted(sc, key=lambda x: -x[0])[:k]


_GEMINI = "https://generativelanguage.googleapis.com/v1beta/"
_model_cache = []


def _err(r):
    try:
        return r.json()["error"]["message"]
    except Exception:
        return f"HTTP {r.status_code}"


def gemini(prompt):
    """Call Gemini with the key in a header. Picks an available Flash model automatically."""
    key = os.getenv("GEMINI_API_KEY", "").strip().strip("\"'")
    h = {"x-goog-api-key": key, "Content-Type": "application/json"}
    t0 = time.monotonic()
    models = [os.getenv("GEMINI_MODEL", "").strip()]
    if not _model_cache:
        try:
            r = requests.get(_GEMINI + "models", headers=h, params={"pageSize": 100}, timeout=10)
            if r.ok:
                names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                         if "generateContent" in m.get("supportedGenerationMethods", [])]
                good = [n for n in names if "flash" in n and not re.search(r"lite|image|tts|live|audio|preview|exp|8b", n)]
                _model_cache.extend(sorted(good, reverse=True)[:3])
            elif "key" in _err(r).lower():
                raise RuntimeError(_err(r))
        except requests.RequestException:
            pass
    models += _model_cache + ["gemini-2.5-flash", "gemini-flash-latest"]
    last = "No Gemini model available"
    for m in dict.fromkeys(x for x in models if x):
        for cfg in ({"responseMimeType": "application/json"}, {}):
            if time.monotonic() - t0 > 55:
                raise RuntimeError("AI request timed out")
            r = requests.post(_GEMINI + f"models/{m}:generateContent", headers=h, timeout=40,
                              json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg})
            if r.ok:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            last = _err(r)
            if "api key" in last.lower() or r.status_code in (401, 403):
                raise RuntimeError(last)
            if r.status_code != 400:
                break
    raise RuntimeError(last)


def llm(prompt):
    if os.getenv("ANTHROPIC_API_KEY"):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                          headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY").strip(),
                                   "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"), "max_tokens": 800,
                                "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    if os.getenv("GEMINI_API_KEY"):
        return gemini(prompt)
    return None


def ai_answer(q, hist, hits):
    ctx = "\n".join(f"[{c['section']}] Q: {c['q']} A: {c['a']}" for c in hits)
    h = "\n".join(("Customer: " if m.get("role") == "user" else "Assistant: ") + str(m.get("text", ""))[:300]
                  for m in hist[-6:])
    out = llm(PROMPT.replace("<<CO>>", COMPANY).replace("<<C>>", ctx).replace("<<H>>", h or "(none)").replace("<<Q>>", q))
    d = json.loads(re.search(r"\{.*\}", out, re.S).group())
    return {"found": bool(d.get("found")), "answer": str(d.get("answer", "")).strip(), "sources": d.get("sources") or []}


GREET = re.compile(r"^\s*(hi+|hello|hey|good (morning|afternoon|evening))\b", re.I)
THANKS = re.compile(r"\b(thanks|thank you|thx)\b", re.I)
HUMAN = re.compile(r"\b(human|agent|representative|real person|customer care|talk to someone|speak to someone)\b", re.I)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/suggestions")
def suggestions():
    seen, out = set(), []
    for c in KB:
        if c["section"] not in seen:
            seen.add(c["section"])
            out.append(c["q"])
    return jsonify(out[:6])


@app.post("/api/chat")
def chat():
    j = request.get_json(silent=True) or {}
    q = str(j.get("message", "")).strip()[:500]
    if not q:
        return jsonify(error="Please type a question."), 400

    def reply(a, found=True, esc=False, src=(), mode="Instant reply", err="", connect=False):
        return jsonify(answer=a, found=found, escalate=esc, sources=list(src), mode=mode, ai_error=err, connect=connect)

    if HUMAN.search(q):
        return reply("Sure, I can connect you with a support agent. Please share your details.", connect=True)
    if GREET.search(q) and len(q.split()) <= 4:
        return reply(f"Hello! I'm Helpora, the {COMPANY} support assistant. Ask me about shipping, returns, refunds, payments or cancellations.")
    if THANKS.search(q) and len(q.split()) <= 5:
        return reply("You're welcome! Is there anything else I can help with?")
    hist = [m for m in (j.get("history") or []) if isinstance(m, dict)]
    hits = search(q)
    best = hits[0][0] if hits else 0
    keyed = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("GEMINI_API_KEY"))
    mode, out, err = ("AI mode (answers from knowledge base)" if keyed else "Offline mode (knowledge base search)"), None, ""
    if best >= 0.25 and keyed:
        try:
            out = ai_answer(q, hist, [c for s, c in hits if s >= 0.2])
        except Exception as e:
            app.logger.warning("AI call failed: %s", e)
            mode, err = "Offline mode (AI unavailable)", str(e)[:200]
    if out is None:
        ok = best >= 0.5
        out = {"found": ok, "answer": hits[0][1]["a"] if ok else NOT_FOUND,
               "sources": [hits[0][1]["section"]] if ok else []}
    if not out["found"] or not out["answer"]:
        out = {"found": False, "answer": NOT_FOUND, "sources": []}
    return reply(out["answer"], out["found"], not out["found"], out["sources"], mode, err)


@app.post("/api/escalate")
def escalate():
    j = request.get_json(silent=True) or {}
    name, email = str(j.get("name", "")).strip()[:80], str(j.get("email", "")).strip()[:120]
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify(error="Please enter your name and a valid email address."), 400
    t = {"id": "TKT-" + uuid.uuid4().hex[:6].upper(), "name": name, "email": email, "topic": str(j.get("topic", "")).strip()[:200],
         "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "transcript": [{"role": str(m.get("role"))[:10], "text": str(m.get("text"))[:500]}
                        for m in (j.get("history") or [])[-20:] if isinstance(m, dict)]}
    try:
        data = []
        if os.path.exists(TICKETS):
            with open(TICKETS, encoding="utf-8") as fh:
                data = json.load(fh)
        data.append(t)
        with open(TICKETS, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except Exception as e:
        app.logger.warning("Could not save ticket: %s", e)
    return jsonify(ticket=t["id"], message=f"Thanks {name}! Your request {t['id']} has been created. "
                                           f"A support agent will contact you at {email} within 24 hours.")


@app.get("/admin/tickets")
def admin_tickets():
    key = os.getenv("ADMIN_KEY")
    if not key or request.args.get("key") != key:
        return jsonify(error="Not found"), 404
    if not os.path.exists(TICKETS):
        return jsonify([])
    with open(TICKETS, encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    app.logger.exception("Unhandled error")
    return jsonify(error="Something went wrong on the server. Please try again."), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
