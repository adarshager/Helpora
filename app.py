"""Helpora - a customer-support chatbot that answers ONLY from a knowledge base (RAG).
Retrieval : TF-IDF style keyword scoring over knowledge_base/*.md (Q:/A: pairs)
AI mode   : Claude or Gemini write the answer from the retrieved passages (key in .env)
Offline   : returns the best-matching FAQ answer, no key needed
Escalation: unanswered questions become support tickets (database), the agent is alerted
            (Telegram/Slack/email) and works them in the /agent dashboard."""
import os, re, json, time, math, glob, uuid, functools, hashlib, hmac, threading, importlib, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib import error as urlerror, parse as urlparse, request as urlrequest
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, abort  # pyright: ignore[reportMissingImports]
try:
    _sqlalchemy = importlib.import_module("sqlalchemy")
    create_engine, text = _sqlalchemy.create_engine, _sqlalchemy.text
except ImportError:
    def text(sql):
        return sql

    class _SQLiteResult:
        def __init__(self, cursor):
            self.cursor = cursor

        def mappings(self):
            return self

        def all(self):
            return [dict(row) for row in self.cursor.fetchall()]

        def first(self):
            row = self.cursor.fetchone()
            return dict(row) if row is not None else None

    class _SQLiteConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            return _SQLiteResult(self.connection.execute(sql, params or {}))

    class _SQLiteEngine:
        def __init__(self, url):
            path = url[len("sqlite:///"):]
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            self.path = path

        @contextmanager
        def _connection(self, transaction):
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                yield _SQLiteConnection(connection)
                if transaction:
                    connection.commit()
            except Exception:
                if transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()

        def begin(self):
            return self._connection(True)

        def connect(self):
            return self._connection(False)

    def create_engine(url, **kwargs):
        if not url.startswith("sqlite:///"):
            raise RuntimeError("SQLAlchemy is required for non-SQLite DATABASE_URL values")
        return _SQLiteEngine(url)
from notify import notify_new_ticket, send_email, email_ready
try:
    load_dotenv = importlib.import_module("dotenv").load_dotenv
except ImportError:  # python-dotenv is optional in some environments
    def load_dotenv(*args, **kwargs):
        return False


class _HTTPResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300

    def json(self):
        return json.loads(self._body.decode("utf-8"))

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class _RequestException(Exception):
    pass


def _http_request(method, url, headers=None, params=None, timeout=None, json_body=None):
    if params:
        url += ("&" if "?" in url else "?") + urlparse.urlencode(params)
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urlrequest.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return _HTTPResponse(response.status, response.read())
    except urlerror.HTTPError as exc:
        return _HTTPResponse(exc.code, exc.read())
    except urlerror.URLError as exc:
        raise _RequestException(str(exc)) from exc


requests = SimpleNamespace(
    get=lambda url, **kwargs: _http_request("GET", url, **kwargs),
    post=lambda url, **kwargs: _http_request("POST", url, json_body=kwargs.pop("json", None), **kwargs),
    RequestException=_RequestException,
)

load_dotenv()
app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
COMPANY = "ShopEase"
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


# ---------------------------------------------------------------- database, tickets and agent desk
STATUSES = ("open", "in_progress", "resolved")
app.jinja_env.globals.update(
    BADGE={"open": "bg-amber-100 text-amber-800", "in_progress": "bg-sky-100 text-sky-800",
           "resolved": "bg-emerald-100 text-emerald-800"},
    LABEL={"open": "Open", "in_progress": "In progress", "resolved": "Resolved"})
app.secret_key = os.getenv("SECRET_KEY") or (
    hashlib.sha256((os.getenv("AGENT_PASSWORD", "") + "helpora").encode()).hexdigest()
    if os.getenv("AGENT_PASSWORD") else os.urandom(24).hex())
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")))


def make_engine():
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url or "sqlite:///" + os.path.join(BASE, "helpora.db"), pool_pre_ping=True)


ENGINE = make_engine()
try:
    with ENGINE.begin() as _c:
        _c.execute(text("CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, name TEXT, email TEXT, topic TEXT, "
                        "status TEXT, created TEXT, updated TEXT, transcript TEXT)"))
        _c.execute(text("CREATE TABLE IF NOT EXISTS replies (id TEXT PRIMARY KEY, ticket_id TEXT, body TEXT, "
                        "created TEXT, emailed INTEGER)"))
except Exception:
    app.logger.exception("Could not initialise the database")

_hits = {}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def limited(ip, n=5, window=3600):
    now = time.time()
    q = [x for x in _hits.get(ip, []) if now - x < window]
    if len(q) >= n:
        _hits[ip] = q
        return True
    _hits[ip] = q + [now]
    return False


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = uuid.uuid4().hex
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.filters["fmt"] = lambda s: str(s or "")[:16].replace("T", " ") + " UTC"


def check_csrf():
    if not request.form.get("csrf") or request.form.get("csrf") != session.get("csrf"):
        abort(400, "Invalid form token. Reload the page and try again.")


def login_required(f):
    @functools.wraps(f)
    def wrapper(*a, **k):
        if not session.get("agent"):
            return redirect(url_for("agent_login"))
        return f(*a, **k)
    return wrapper


@app.post("/api/escalate")
def escalate():
    j = request.get_json(silent=True) or {}
    name, email = str(j.get("name", "")).strip()[:80], str(j.get("email", "")).strip()[:120]
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify(error="Please enter your name and a valid email address."), 400
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")
    if limited(ip):
        return jsonify(error="Too many requests from this network. Please try again later."), 429
    now = utcnow()
    t = {"id": "TKT-" + uuid.uuid4().hex[:6].upper(), "name": name, "email": email,
         "topic": str(j.get("topic", "")).strip()[:200], "status": "open", "created": now, "updated": now,
         "transcript": json.dumps([{"role": str(m.get("role"))[:10], "text": str(m.get("text"))[:500]}
                                   for m in (j.get("history") or [])[-20:] if isinstance(m, dict)])}
    try:
        with ENGINE.begin() as c:
            c.execute(text("INSERT INTO tickets (id,name,email,topic,status,created,updated,transcript) VALUES "
                           "(:id,:name,:email,:topic,:status,:created,:updated,:transcript)"), t)
    except Exception:
        app.logger.exception("Could not save ticket")
        return jsonify(error="We couldn't save your request right now. Please email support@shopease.example."), 500
    threading.Thread(target=notify_new_ticket, args=(t,), daemon=True).start()
    extra = " A confirmation email is on its way." if email_ready() else ""
    return jsonify(ticket=t["id"], message=f"Thanks {name}! Your request {t['id']} has been created. "
                                           f"A support agent will contact you at {email} within 24 hours.{extra}")


@app.route("/agent/login", methods=["GET", "POST"])
def agent_login():
    err = ""
    pw = os.getenv("AGENT_PASSWORD", "")
    if request.method == "POST":
        check_csrf()
        u, p = request.form.get("username", ""), request.form.get("password", "")
        ok = bool(pw) and hmac.compare_digest(u.encode(), os.getenv("AGENT_USERNAME", "agent").encode()) \
            and hmac.compare_digest(p.encode(), pw.encode())
        if ok:
            session.clear()
            session["agent"] = u
            return redirect(url_for("agent_home"))
        time.sleep(1)
        err = "Wrong username or password." if pw else "The agent desk is not configured. Set AGENT_PASSWORD first."
    return render_template("login.html", err=err, configured=bool(pw))


@app.get("/agent/logout")
def agent_logout():
    session.clear()
    return redirect(url_for("agent_login"))


@app.get("/agent")
@login_required
def agent_home():
    st = request.args.get("status", "")
    sql = "SELECT id,name,email,topic,status,created FROM tickets" + (" WHERE status=:s" if st in STATUSES else "")
    with ENGINE.connect() as c:
        rows = c.execute(text(sql + " ORDER BY created DESC LIMIT 200"), {"s": st} if st in STATUSES else {}).mappings().all()
        counts = {r["status"]: r["n"] for r in c.execute(text("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")).mappings()}
    return render_template("agent.html", rows=rows, counts=counts, st=st, total=sum(counts.values()))


@app.get("/agent/ticket/<tid>")
@login_required
def agent_ticket(tid):
    with ENGINE.connect() as c:
        t = c.execute(text("SELECT * FROM tickets WHERE id=:i"), {"i": tid}).mappings().first()
        if not t:
            abort(404)
        reps = c.execute(text("SELECT body,created,emailed FROM replies WHERE ticket_id=:i ORDER BY created"), {"i": tid}).mappings().all()
    try:
        tr = json.loads(t["transcript"] or "[]")
    except ValueError:
        tr = []
    return render_template("ticket.html", t=t, tr=tr, reps=reps, statuses=STATUSES, email_ok=email_ready(),
                           msg=request.args.get("msg", ""))


@app.post("/agent/ticket/<tid>/status")
@login_required
def agent_status(tid):
    check_csrf()
    s = request.form.get("status", "")
    if s not in STATUSES:
        abort(400)
    with ENGINE.begin() as c:
        c.execute(text("UPDATE tickets SET status=:s, updated=:u WHERE id=:i"), {"s": s, "u": utcnow(), "i": tid})
    return redirect(url_for("agent_ticket", tid=tid, msg="Status updated."))


@app.post("/agent/ticket/<tid>/reply")
@login_required
def agent_reply(tid):
    check_csrf()
    body = request.form.get("body", "").strip()[:2000]
    if not body:
        return redirect(url_for("agent_ticket", tid=tid, msg="Write a reply first."))
    with ENGINE.connect() as c:
        t = c.execute(text("SELECT name,email,status FROM tickets WHERE id=:i"), {"i": tid}).mappings().first()
    if not t:
        abort(404)
    ok, info = send_email(t["email"], f"Re: your support request {tid}",
                          f"Hi {t['name']},\n\n{body}\n\nRegards,\nShopEase Support",
                          reply_to=os.getenv("AGENT_NOTIFY_EMAIL", "").strip() or None)
    with ENGINE.begin() as c:
        c.execute(text("INSERT INTO replies (id,ticket_id,body,created,emailed) VALUES (:id,:t,:b,:c,:e)"),
                  {"id": uuid.uuid4().hex, "t": tid, "b": body, "c": utcnow(), "e": 1 if ok else 0})
        c.execute(text("UPDATE tickets SET status=CASE WHEN status='open' THEN 'in_progress' ELSE status END, "
                       "updated=:u WHERE id=:i"), {"u": utcnow(), "i": tid})
    msg = "Reply emailed to the customer." if ok else f"Reply saved, but not emailed ({info}). Contact the customer manually."
    return redirect(url_for("agent_ticket", tid=tid, msg=msg))


@app.errorhandler(Exception)
def on_error(e):
    try:
        from werkzeug.exceptions import HTTPException  # pyright: ignore[reportMissingImports]
    except ImportError:
        class HTTPException(Exception):
            code = 500
            description = "Something went wrong on the server. Please try again."
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    app.logger.exception("Unhandled error")
    return jsonify(error="Something went wrong on the server. Please try again."), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
