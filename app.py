import os
import secrets
import sqlite3
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:
    psycopg2 = None
    DictCursor = None
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-before-going-public")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("RENDER", "").lower() == "true",
)

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("DATABASE_PATH", "truckos.db")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
APP_VERSION = "0.6"

PLAN_INFO = {
    "driver": {"name": "Chauffør", "price_dkk": 79, "price_env": "STRIPE_PRICE_DRIVER", "features": ["1 chaufførkonto", "AI-diagnose", "Servicehistorik", "Påmindelser"]},
    "pro": {"name": "Vognmand Pro", "price_dkk": 199, "price_env": "STRIPE_PRICE_PRO", "features": ["Alt i Chauffør", "Flere lastbiler", "Prioriteret overblik", "Fuld historik"]},
    "fleet": {"name": "Flåde", "price_dkk": 499, "price_env": "STRIPE_PRICE_FLEET", "features": ["Alt i Pro", "Flådeoverblik", "Flere brugere senere", "Prioriteret support"]},
}


class DB:
    def __init__(self):
        self.is_postgres = bool(DATABASE_URL)
        if self.is_postgres:
            if psycopg2 is None:
                raise RuntimeError("psycopg2 mangler, men DATABASE_URL er sat")
            self.conn = psycopg2.connect(DATABASE_URL)
            self.cur = self.conn.cursor(cursor_factory=DictCursor)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
            self.cur = self.conn.cursor()

    def execute(self, sql, params=()):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        self.cur.execute(sql, params)
        return self.cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.cur.close()
        self.conn.close()


def db():
    return DB()


def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def ensure_column(c, table, column, definition):
    try:
        if c.is_postgres:
            c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            cols = c.execute(f"PRAGMA table_info({table})").fetchall()
            names = {row[1] for row in cols}
            if column not in names:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        c.rollback()
        raise


def init_db():
    c = db()
    if c.is_postgres:
        statements = [
            """CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS profiles(
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS trucks(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                plate TEXT NOT NULL,
                km INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS services(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                service_date TEXT NOT NULL,
                km INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS diagnoses(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                fault_code TEXT,
                symptoms TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS reminders(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL DEFAULT '',
                due_km INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS subscriptions(
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                stripe_customer_id TEXT NOT NULL DEFAULT '',
                stripe_subscription_id TEXT NOT NULL DEFAULT '',
                current_period_end TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )""",
        ]
    else:
        statements = [
            """CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS profiles(
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS trucks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                plate TEXT NOT NULL,
                km INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS services(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                service_date TEXT NOT NULL,
                km INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS diagnoses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                fault_code TEXT,
                symptoms TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS reminders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL DEFAULT '',
                due_km INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS subscriptions(
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                stripe_customer_id TEXT NOT NULL DEFAULT '',
                stripe_subscription_id TEXT NOT NULL DEFAULT '',
                current_period_end TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )""",
        ]
    for stmt in statements:
        c.execute(stmt)

    # Safe schema upgrades from v0.4 without deleting existing data.
    ensure_column(c, "trucks", "make", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "model", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "year", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(c, "trucks", "vin", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "engine", "TEXT NOT NULL DEFAULT ''")
    c.commit()
    c.close()


def uid():
    return session.get("user_id")


def guard():
    return None if uid() else redirect(url_for("login"))


def ensure_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


def stripe_ready():
    return bool(os.getenv("STRIPE_SECRET_KEY")) and any(os.getenv(p["price_env"]) for p in PLAN_INFO.values())


def subscription_for_user(c, user_id):
    return c.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()


@app.context_processor
def inject_globals():
    c = None
    sub = None
    if uid():
        try:
            c = db()
            sub = subscription_for_user(c, uid())
        finally:
            if c:
                c.close()
    return {
        "csrf_token": ensure_csrf(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "ai_model": OPENAI_MODEL,
        "app_version": APP_VERSION,
        "stripe_ready": stripe_ready(),
        "plans": PLAN_INFO,
        "subscription": sub,
    }


def check_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not token or token != session.get("csrf_token"):
        abort(400, "Ugyldig sikkerhedstoken")


def truck_for_user(c, truck_id):
    return c.execute("SELECT * FROM trucks WHERE id=? AND user_id=?", (truck_id, uid())).fetchone()


def fallback(symptoms, code):
    s = f"{symptoms} {code}".lower()
    if any(x in s for x in ["bremse", "brake", "styring", "steering", "olietryk", "oil pressure", "overophed", "overheat", "brand", "fire"]):
        return (
            "Prioritet: HØJ\n\n"
            "Sandsynlige årsager\nSymptomerne kan være sikkerheds- eller motorkritiske.\n\n"
            "Kontrolpunkter\nFølg køretøjets advarsler og producentens instruktioner.\n\n"
            "Næste skridt\nStands hvis producentens anvisninger kræver det, og få lastbilen vurderet af et kvalificeret værksted."
        )
    if any(x in s for x in ["p0299", "turbo", "boost", "mister trækkraft", "mister kraft"]):
        return (
            "Prioritet: UNDERSØG SNART\n\n"
            "Sandsynlige årsager\nDet kan passe med et problem omkring ladetryk, indsugning, turbo, intercooler eller sensorik.\n\n"
            "Kontrolpunkter\nKontrollér synlige slanger og rør, og gem fejlkoder.\n\n"
            "Næste skridt\nFå ønsket og faktisk ladetryk vurderet med korrekt diagnoseudstyr."
        )
    return (
        "Prioritet: FORELØBIG VURDERING\n\n"
        "Sandsynlige årsager\nDer er ikke nok information til en sikker kategorisering.\n\n"
        "Kontrolpunkter\nNotér hvornår fejlen opstår, belastning, temperatur, advarselslamper og fejlkoder.\n\n"
        "Næste skridt\nFå relevante data kontrolleret, hvis fejlen fortsætter."
    )


def ai_answer(truck, symptoms, code):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return fallback(symptoms, code) + "\n\n(Lokal TruckOS-vurdering – OpenAI API er ikke slået til endnu.)"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        instructions = (
            "Du er TruckOS, forsigtig beslutningsstøtte til lastbilchauffører, vognmænd og mekanikere. "
            "Du har ikke fysisk inspiceret køretøjet og må ikke fremstille en diagnose som sikker. "
            "Prioritér sikkerhed. Hvis symptomer kan være bremse-, styrings-, dæk-, brand-, olie-, temperatur- "
            "eller anden sikkerhedskritisk fejl, så sig tydeligt at køretøjet bør standses eller vurderes efter "
            "producentens instruktioner og af kvalificeret fagperson. Svar på dansk i korte afsnit med overskrifterne: "
            "Prioritet, Sandsynlige årsager, Kontrolpunkter, Næste skridt. Undgå at opfinde måleværdier eller procedurer."
        )
        prompt = (
            f"Lastbil: {truck['name']}\nMærke/model: {truck['make']} {truck['model']}\n"
            f"Nummerplade: {truck['plate']}\nKilometerstand: {truck['km']}\n"
            f"Fejlkode: {code or 'ingen oplyst'}\nSymptomer: {symptoms}"
        )
        response = client.responses.create(model=OPENAI_MODEL, instructions=instructions, input=prompt)
        text = getattr(response, "output_text", "").strip()
        return text or fallback(symptoms, code)
    except Exception:
        return fallback(symptoms, code) + "\n\n(AI-forbindelsen var ikke tilgængelig, så TruckOS brugte lokal fallback.)"


@app.route("/health")
def health():
    try:
        c = db(); c.execute("SELECT 1").fetchone(); c.close()
        return jsonify({"ok": True, "database": "postgres" if DATABASE_URL else "sqlite", "version": APP_VERSION})
    except Exception:
        return jsonify({"ok": False, "version": APP_VERSION}), 503


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/sw.js")
def service_worker():
    response = app.send_static_file("sw.js")
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/")
def index():
    if not uid():
        return redirect(url_for("login"))
    c = db(); u = uid()
    trucks = c.execute("SELECT * FROM trucks WHERE user_id=? ORDER BY id DESC", (u,)).fetchall()
    services = c.execute(
        """SELECT services.*, trucks.name AS truck_name, trucks.plate AS truck_plate
           FROM services JOIN trucks ON trucks.id=services.truck_id
           WHERE services.user_id=? ORDER BY services.service_date DESC, services.id DESC LIMIT 50""", (u,)
    ).fetchall()
    diagnoses = c.execute(
        """SELECT diagnoses.*, trucks.name AS truck_name, trucks.plate AS truck_plate
           FROM diagnoses JOIN trucks ON trucks.id=diagnoses.truck_id
           WHERE diagnoses.user_id=? ORDER BY diagnoses.id DESC LIMIT 20""", (u,)
    ).fetchall()
    reminders = c.execute(
        """SELECT reminders.*, trucks.name AS truck_name, trucks.plate AS truck_plate
           FROM reminders JOIN trucks ON trucks.id=reminders.truck_id
           WHERE reminders.user_id=? ORDER BY reminders.done ASC, reminders.due_date ASC, reminders.id DESC LIMIT 50""", (u,)
    ).fetchall()
    profile = c.execute("SELECT * FROM profiles WHERE user_id=?", (u,)).fetchone()
    sub = subscription_for_user(c, u)
    counts = {
        "trucks": c.execute("SELECT COUNT(*) FROM trucks WHERE user_id=?", (u,)).fetchone()[0],
        "services": c.execute("SELECT COUNT(*) FROM services WHERE user_id=?", (u,)).fetchone()[0],
        "diagnoses": c.execute("SELECT COUNT(*) FROM diagnoses WHERE user_id=?", (u,)).fetchone()[0],
        "reminders": c.execute("SELECT COUNT(*) FROM reminders WHERE user_id=? AND done=0", (u,)).fetchone()[0],
    }
    c.close()
    return render_template("dashboard.html", trucks=trucks, services=services, diagnoses=diagnoses,
                           reminders=reminders, counts=counts, profile=profile, subscription=sub)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        check_csrf(); email = request.form["email"].strip().lower(); password = request.form["password"]
        if len(password) < 8:
            flash("Adgangskoden skal være mindst 8 tegn."); return redirect(url_for("register"))
        c = db()
        try:
            c.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", (email, generate_password_hash(password), now_iso()))
            c.commit()
        except tuple(x for x in (sqlite3.IntegrityError, getattr(psycopg2, "IntegrityError", None)) if x is not None):
            c.rollback(); c.close(); flash("Der findes allerede en bruger med den e-mail."); return redirect(url_for("register"))
        user = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        c.execute("INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)", (user["id"], "", "", now_iso(), now_iso()))
        c.execute("INSERT INTO subscriptions(user_id,plan,status,updated_at) VALUES(?,?,?,?)", (user["id"], "free", "inactive", now_iso()))
        c.commit(); c.close()
        session["user_id"] = user["id"]; session["email"] = email; ensure_csrf()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        check_csrf(); email = request.form["email"].strip().lower(); password = request.form["password"]
        c = db(); user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(); c.close()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Forkert e-mail eller adgangskode."); return redirect(url_for("login"))
        session.clear(); session["user_id"] = user["id"]; session["email"] = user["email"]; ensure_csrf()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))


@app.route("/profile", methods=["POST"])
def update_profile():
    g = guard()
    if g: return g
    check_csrf(); display_name = request.form.get("display_name", "").strip()[:80]; company = request.form.get("company", "").strip()[:120]
    c = db(); existing = c.execute("SELECT user_id FROM profiles WHERE user_id=?", (uid(),)).fetchone()
    if existing:
        c.execute("UPDATE profiles SET display_name=?, company=?, updated_at=? WHERE user_id=?", (display_name, company, now_iso(), uid()))
    else:
        c.execute("INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)", (uid(), display_name, company, now_iso(), now_iso()))
    c.commit(); c.close(); flash("Profil gemt."); return redirect(url_for("index") + "#profile")


@app.route("/trucks", methods=["POST"])
def add_truck():
    g = guard()
    if g: return g
    check_csrf()
    try: km = max(0, int(request.form["km"])); year = max(0, int(request.form.get("year") or 0))
    except ValueError: flash("Kilometerstand og årgang skal være tal."); return redirect(url_for("index") + "#trucks")
    name = request.form["name"].strip()[:120]; plate = request.form["plate"].strip().upper()[:30]
    if not name or not plate: flash("Navn og nummerplade skal udfyldes."); return redirect(url_for("index") + "#trucks")
    c = db(); c.execute(
        "INSERT INTO trucks(user_id,name,plate,km,make,model,year,vin,engine,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (uid(), name, plate, km, request.form.get("make", "").strip()[:80], request.form.get("model", "").strip()[:80], year,
         request.form.get("vin", "").strip().upper()[:40], request.form.get("engine", "").strip()[:120], now_iso()))
    c.commit(); c.close(); flash("Lastbil tilføjet."); return redirect(url_for("index") + "#trucks")


@app.route("/trucks/<int:truck_id>/edit", methods=["POST"])
def edit_truck(truck_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); truck = truck_for_user(c, truck_id)
    if not truck: c.close(); abort(404)
    try: km = max(0, int(request.form["km"])); year = max(0, int(request.form.get("year") or 0))
    except ValueError: c.close(); flash("Kilometerstand og årgang skal være tal."); return redirect(url_for("index") + "#trucks")
    c.execute("UPDATE trucks SET name=?, plate=?, km=?, make=?, model=?, year=?, vin=?, engine=? WHERE id=? AND user_id=?",
              (request.form["name"].strip()[:120], request.form["plate"].strip().upper()[:30], km,
               request.form.get("make", "").strip()[:80], request.form.get("model", "").strip()[:80], year,
               request.form.get("vin", "").strip().upper()[:40], request.form.get("engine", "").strip()[:120], truck_id, uid()))
    c.commit(); c.close(); flash("Lastbil opdateret."); return redirect(url_for("index") + "#trucks")


@app.route("/trucks/<int:truck_id>/delete", methods=["POST"])
def delete_truck(truck_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); truck = truck_for_user(c, truck_id)
    if not truck: c.close(); abort(404)
    c.execute("DELETE FROM services WHERE truck_id=? AND user_id=?", (truck_id, uid()))
    c.execute("DELETE FROM diagnoses WHERE truck_id=? AND user_id=?", (truck_id, uid()))
    c.execute("DELETE FROM reminders WHERE truck_id=? AND user_id=?", (truck_id, uid()))
    c.execute("DELETE FROM trucks WHERE id=? AND user_id=?", (truck_id, uid()))
    c.commit(); c.close(); flash("Lastbil og dens historik er slettet."); return redirect(url_for("index") + "#trucks")


@app.route("/service", methods=["POST"])
def add_service():
    g = guard()
    if g: return g
    check_csrf(); tid = int(request.form["truck_id"]); c = db()
    if not truck_for_user(c, tid): c.close(); return "Ikke tilladt", 403
    try: km = max(0, int(request.form["km"]))
    except ValueError: c.close(); flash("Kilometerstand skal være et tal."); return redirect(url_for("index") + "#service")
    c.execute("INSERT INTO services(user_id,truck_id,service_date,km,note,created_at) VALUES(?,?,?,?,?,?)",
              (uid(), tid, request.form["service_date"], km, request.form["note"].strip()[:2000], now_iso()))
    c.commit(); c.close(); flash("Servicepost gemt."); return redirect(url_for("index") + "#service")


@app.route("/service/<int:service_id>/edit", methods=["POST"])
def edit_service(service_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); row = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", (service_id, uid())).fetchone()
    if not row: c.close(); abort(404)
    tid = int(request.form["truck_id"])
    if not truck_for_user(c, tid): c.close(); return "Ikke tilladt", 403
    try: km = max(0, int(request.form["km"]))
    except ValueError: c.close(); flash("Kilometerstand skal være et tal."); return redirect(url_for("index") + "#service")
    c.execute("UPDATE services SET truck_id=?, service_date=?, km=?, note=? WHERE id=? AND user_id=?",
              (tid, request.form["service_date"], km, request.form["note"].strip()[:2000], service_id, uid()))
    c.commit(); c.close(); flash("Servicepost opdateret."); return redirect(url_for("index") + "#service")


@app.route("/service/<int:service_id>/delete", methods=["POST"])
def delete_service(service_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); c.execute("DELETE FROM services WHERE id=? AND user_id=?", (service_id, uid())); c.commit(); c.close()
    flash("Servicepost slettet."); return redirect(url_for("index") + "#service")


@app.route("/reminders", methods=["POST"])
def add_reminder():
    g = guard()
    if g: return g
    check_csrf(); tid = int(request.form["truck_id"]); c = db()
    if not truck_for_user(c, tid): c.close(); return "Ikke tilladt", 403
    try: due_km = max(0, int(request.form.get("due_km") or 0))
    except ValueError: c.close(); flash("Kilometer skal være et tal."); return redirect(url_for("index") + "#reminders")
    title = request.form.get("title", "").strip()[:140]
    if not title: c.close(); flash("Skriv hvad du vil huskes på."); return redirect(url_for("index") + "#reminders")
    c.execute("INSERT INTO reminders(user_id,truck_id,title,due_date,due_km,note,done,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (uid(), tid, title, request.form.get("due_date", "")[:20], due_km, request.form.get("note", "").strip()[:1000], 0, now_iso()))
    c.commit(); c.close(); flash("Påmindelse oprettet."); return redirect(url_for("index") + "#reminders")


@app.route("/reminders/<int:reminder_id>/toggle", methods=["POST"])
def toggle_reminder(reminder_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); row = c.execute("SELECT done FROM reminders WHERE id=? AND user_id=?", (reminder_id, uid())).fetchone()
    if not row: c.close(); abort(404)
    c.execute("UPDATE reminders SET done=? WHERE id=? AND user_id=?", (0 if row["done"] else 1, reminder_id, uid()))
    c.commit(); c.close(); return redirect(url_for("index") + "#reminders")


@app.route("/reminders/<int:reminder_id>/delete", methods=["POST"])
def delete_reminder(reminder_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); c.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, uid())); c.commit(); c.close()
    flash("Påmindelse slettet."); return redirect(url_for("index") + "#reminders")


@app.route("/api/diagnose", methods=["POST"])
def diagnose():
    if not uid(): return jsonify({"error": "Log ind først"}), 401
    check_csrf(); data = request.get_json(force=True)
    try: tid = int(data.get("truck_id"))
    except (TypeError, ValueError): return jsonify({"error": "Vælg en lastbil"}), 400
    symptoms = (data.get("symptoms") or "").strip()[:4000]; code = (data.get("fault_code") or "").strip().upper()[:50]
    if not symptoms: return jsonify({"error": "Beskriv symptomerne"}), 400
    c = db(); truck = truck_for_user(c, tid)
    if not truck: c.close(); return jsonify({"error": "Lastbil ikke fundet"}), 404
    answer = ai_answer(truck, symptoms, code)
    c.execute("INSERT INTO diagnoses(user_id,truck_id,fault_code,symptoms,answer,created_at) VALUES(?,?,?,?,?,?)",
              (uid(), tid, code, symptoms, answer, now_iso()))
    c.commit(); c.close(); return jsonify({"answer": answer, "ai": bool(os.getenv("OPENAI_API_KEY"))})


def upsert_subscription(user_id, plan, status, customer_id="", subscription_id="", period_end=""):
    c = db(); existing = subscription_for_user(c, user_id)
    if existing:
        c.execute("UPDATE subscriptions SET plan=?,status=?,stripe_customer_id=?,stripe_subscription_id=?,current_period_end=?,updated_at=? WHERE user_id=?",
                  (plan, status, customer_id or existing["stripe_customer_id"], subscription_id or existing["stripe_subscription_id"], period_end, now_iso(), user_id))
    else:
        c.execute("INSERT INTO subscriptions(user_id,plan,status,stripe_customer_id,stripe_subscription_id,current_period_end,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (user_id, plan, status, customer_id, subscription_id, period_end, now_iso()))
    c.commit(); c.close()


@app.route("/billing/checkout", methods=["POST"])
def billing_checkout():
    g = guard()
    if g: return g
    check_csrf(); plan = request.form.get("plan", "")
    if plan not in PLAN_INFO: flash("Ugyldigt abonnement."); return redirect(url_for("index") + "#billing")
    secret = os.getenv("STRIPE_SECRET_KEY"); price_id = os.getenv(PLAN_INFO[plan]["price_env"])
    if not secret or not price_id:
        flash("Betaling er ikke konfigureret endnu. Tilføj Stripe-nøgler på Render først.")
        return redirect(url_for("index") + "#billing")
    try:
        import stripe
        stripe.api_key = secret
        checkout = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=session.get("email"),
            success_url=url_for("billing_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("index", _external=True) + "#billing",
            metadata={"user_id": str(uid()), "plan": plan},
            subscription_data={"metadata": {"user_id": str(uid()), "plan": plan}},
            allow_promotion_codes=True,
        )
        return redirect(checkout.url, code=303)
    except Exception:
        flash("Kunne ikke starte betalingen. Tjek Stripe-konfigurationen.")
        return redirect(url_for("index") + "#billing")


@app.route("/billing/success")
def billing_success():
    if not uid(): return redirect(url_for("login"))
    flash("Betalingen blev sendt til behandling. TruckOS opdaterer abonnementet automatisk.")
    return redirect(url_for("index") + "#billing")


@app.route("/billing/portal", methods=["POST"])
def billing_portal():
    g = guard()
    if g: return g
    check_csrf(); secret = os.getenv("STRIPE_SECRET_KEY")
    c = db(); sub = subscription_for_user(c, uid()); c.close()
    if not secret or not sub or not sub["stripe_customer_id"]:
        flash("Der er endnu ingen Stripe-kunde tilknyttet denne konto."); return redirect(url_for("index") + "#billing")
    try:
        import stripe
        stripe.api_key = secret
        portal = stripe.billing_portal.Session.create(customer=sub["stripe_customer_id"], return_url=url_for("index", _external=True) + "#billing")
        return redirect(portal.url, code=303)
    except Exception:
        flash("Kunne ikke åbne abonnementsportalen."); return redirect(url_for("index") + "#billing")


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    secret = os.getenv("STRIPE_SECRET_KEY"); webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret or not webhook_secret: return "Stripe ikke konfigureret", 400
    try:
        import stripe
        stripe.api_key = secret
        event = stripe.Webhook.construct_event(request.data, request.headers.get("Stripe-Signature", ""), webhook_secret)
    except Exception:
        return "Ugyldig webhook", 400

    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        meta = obj.get("metadata") or {}; user_id = int(meta.get("user_id") or 0); plan = meta.get("plan") or "free"
        if user_id:
            upsert_subscription(user_id, plan, "active", obj.get("customer") or "", obj.get("subscription") or "", "")
    elif event["type"] in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        meta = obj.get("metadata") or {}; user_id = int(meta.get("user_id") or 0); plan = meta.get("plan") or "free"
        status = obj.get("status") or "inactive"; period_end = str(obj.get("current_period_end") or "")
        if user_id:
            upsert_subscription(user_id, plan, status, obj.get("customer") or "", obj.get("id") or "", period_end)
    return "ok", 200


if __name__ == "__main__":
    init_db(); app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
else:
    init_db()
