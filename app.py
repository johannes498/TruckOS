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
APP_VERSION = "1.0"

PLAN_INFO = {
    "driver": {"name": "Chauffør", "price_dkk": 79, "price_env": "STRIPE_PRICE_DRIVER", "features": ["1 chaufførkonto", "AI-diagnose", "Servicehistorik", "Påmindelser"]},
    "pro": {"name": "Vognmand Pro", "price_dkk": 199, "price_env": "STRIPE_PRICE_PRO", "features": ["Alt i Chauffør", "Flere lastbiler", "Prioriteret overblik", "Fuld historik"]},
    "fleet": {"name": "Flåde", "price_dkk": 499, "price_env": "STRIPE_PRICE_FLEET", "features": ["Alt i Pro", "Flådeoverblik", "Reparationsflow", "Prioriteret support"]},
}

# External partner integrations are deliberately OFF until real credentials are configured.
# This prevents TruckOS from inventing stock, prices, booking slots or roadside status.
INTEGRATIONS = {
    "parts": ("PARTS_API_URL", "PARTS_API_KEY"),
    "workshops": ("WORKSHOP_API_URL", "WORKSHOP_API_KEY"),
    "roadside": ("ROADSIDE_API_URL", "ROADSIDE_API_KEY"),
}

def integration_ready(name):
    required = INTEGRATIONS.get(name, ())
    return bool(required) and all(os.getenv(key) for key in required)


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
            """CREATE TABLE IF NOT EXISTS repair_cases(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                diagnosis_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'review',
                likely_part TEXT NOT NULL DEFAULT '',
                part_number TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS part_options(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL,
                supplier_name TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                part_number TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                price_text TEXT NOT NULL DEFAULT '',
                stock_status TEXT NOT NULL DEFAULT 'unknown',
                eta_text TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS workshop_requests(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL,
                workshop_name TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                requested_time TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                contact TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS assistance_requests(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '',
                location_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            """CREATE TABLE IF NOT EXISTS repair_cases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                diagnosis_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'review',
                likely_part TEXT NOT NULL DEFAULT '',
                part_number TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS part_options(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL,
                supplier_name TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                part_number TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                price_text TEXT NOT NULL DEFAULT '',
                stock_status TEXT NOT NULL DEFAULT 'unknown',
                eta_text TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS workshop_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL,
                workshop_name TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                requested_time TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                contact TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS assistance_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                truck_id INTEGER NOT NULL,
                case_id INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '',
                location_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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

    # Helpful indexes for the 1.0 workflow. Safe to run repeatedly.
    c.execute("CREATE INDEX IF NOT EXISTS idx_repair_cases_user ON repair_cases(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_repair_cases_diagnosis ON repair_cases(diagnosis_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_part_options_case ON part_options(case_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_workshop_requests_case ON workshop_requests(case_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assistance_requests_truck ON assistance_requests(truck_id)")

    # Safe schema upgrades from v0.4 without deleting existing data.
    ensure_column(c, "trucks", "make", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "model", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "year", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(c, "trucks", "vin", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "engine", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "trucks", "registration_country", "TEXT NOT NULL DEFAULT 'DK'")
    ensure_column(c, "trucks", "fuel", "TEXT NOT NULL DEFAULT ''")
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
        "integration_status": {k: integration_ready(k) for k in INTEGRATIONS},
        "app_store_url": os.getenv("APP_STORE_URL", ""),
        "play_store_url": os.getenv("PLAY_STORE_URL", ""),
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
            f"Nummerplade: {truck['plate']}\nVIN: {truck['vin'] or 'ikke oplyst'}\nMotor: {truck['engine'] or 'ikke oplyst'}\n"
            f"Kilometerstand: {truck['km']}\nFejlkode: {code or 'ingen oplyst'}\nSymptomer: {symptoms}"
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


@app.route("/download")
def download_app():
    return render_template("download.html")


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
    cases = c.execute(
        """SELECT repair_cases.*, trucks.name AS truck_name, trucks.plate AS truck_plate, diagnoses.fault_code AS fault_code
           FROM repair_cases JOIN trucks ON trucks.id=repair_cases.truck_id
           JOIN diagnoses ON diagnoses.id=repair_cases.diagnosis_id
           WHERE repair_cases.user_id=? ORDER BY repair_cases.id DESC LIMIT 50""", (u,)
    ).fetchall()
    parts = c.execute(
        """SELECT part_options.* FROM part_options WHERE user_id=? ORDER BY id DESC LIMIT 100""", (u,)
    ).fetchall()
    workshop_requests = c.execute(
        """SELECT workshop_requests.*, repair_cases.truck_id, trucks.name AS truck_name
           FROM workshop_requests JOIN repair_cases ON repair_cases.id=workshop_requests.case_id
           JOIN trucks ON trucks.id=repair_cases.truck_id WHERE workshop_requests.user_id=?
           ORDER BY workshop_requests.id DESC LIMIT 50""", (u,)
    ).fetchall()
    assistance_requests = c.execute(
        """SELECT assistance_requests.*, trucks.name AS truck_name FROM assistance_requests
           JOIN trucks ON trucks.id=assistance_requests.truck_id WHERE assistance_requests.user_id=?
           ORDER BY assistance_requests.id DESC LIMIT 50""", (u,)
    ).fetchall()
    counts = {
        "trucks": c.execute("SELECT COUNT(*) FROM trucks WHERE user_id=?", (u,)).fetchone()[0],
        "services": c.execute("SELECT COUNT(*) FROM services WHERE user_id=?", (u,)).fetchone()[0],
        "diagnoses": c.execute("SELECT COUNT(*) FROM diagnoses WHERE user_id=?", (u,)).fetchone()[0],
        "reminders": c.execute("SELECT COUNT(*) FROM reminders WHERE user_id=? AND done=0", (u,)).fetchone()[0],
        "cases": c.execute("SELECT COUNT(*) FROM repair_cases WHERE user_id=? AND status!='closed'", (u,)).fetchone()[0],
    }
    c.close()
    return render_template("dashboard.html", trucks=trucks, services=services, diagnoses=diagnoses,
                           reminders=reminders, counts=counts, profile=profile, subscription=sub,
                           cases=cases, parts=parts, workshop_requests=workshop_requests, assistance_requests=assistance_requests)


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
        "INSERT INTO trucks(user_id,name,plate,km,make,model,year,vin,engine,registration_country,fuel,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid(), name, plate, km, request.form.get("make", "").strip()[:80], request.form.get("model", "").strip()[:80], year,
         request.form.get("vin", "").strip().upper()[:40], request.form.get("engine", "").strip()[:120],
         request.form.get("registration_country", "DK").strip().upper()[:8], request.form.get("fuel", "").strip()[:40], now_iso()))
    c.commit(); c.close(); flash("Lastbil tilføjet."); return redirect(url_for("index") + "#trucks")


@app.route("/trucks/<int:truck_id>/edit", methods=["POST"])
def edit_truck(truck_id):
    g = guard()
    if g: return g
    check_csrf(); c = db(); truck = truck_for_user(c, truck_id)
    if not truck: c.close(); abort(404)
    try: km = max(0, int(request.form["km"])); year = max(0, int(request.form.get("year") or 0))
    except ValueError: c.close(); flash("Kilometerstand og årgang skal være tal."); return redirect(url_for("index") + "#trucks")
    c.execute("UPDATE trucks SET name=?, plate=?, km=?, make=?, model=?, year=?, vin=?, engine=?, registration_country=?, fuel=? WHERE id=? AND user_id=?",
              (request.form["name"].strip()[:120], request.form["plate"].strip().upper()[:30], km,
               request.form.get("make", "").strip()[:80], request.form.get("model", "").strip()[:80], year,
               request.form.get("vin", "").strip().upper()[:40], request.form.get("engine", "").strip()[:120],
               request.form.get("registration_country", "DK").strip().upper()[:8], request.form.get("fuel", "").strip()[:40], truck_id, uid()))
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
    c.commit()
    row = c.execute("SELECT id FROM diagnoses WHERE user_id=? AND truck_id=? ORDER BY id DESC LIMIT 1", (uid(), tid)).fetchone()
    diagnosis_id = row["id"] if row else 0
    c.close(); return jsonify({"answer": answer, "ai": bool(os.getenv("OPENAI_API_KEY")), "diagnosis_id": diagnosis_id})


def likely_part_for(code, symptoms):
    text = f"{code} {symptoms}".lower()
    if any(x in text for x in ["p0299", "turbo", "boost", "ladetryk"]):
        return "Ladetrykssystem / turboslange / intercooler / sensor"
    if any(x in text for x in ["bremse", "brake"]):
        return "Bremsesystem – kræver faglig kontrol før del vælges"
    if any(x in text for x in ["adblue", "scr", "nox"]):
        return "SCR/AdBlue-system / NOx-sensor"
    if any(x in text for x in ["dpf", "partikelfilter"]):
        return "DPF/udstødningssystem"
    return "Del skal identificeres ud fra VIN/OEM-nummer og værkstedsdiagnose"

@app.route("/solutions/<int:diagnosis_id>", methods=["POST"])
def create_solution(diagnosis_id):
    g = guard()
    if g: return g
    check_csrf(); c = db()
    d = c.execute("SELECT * FROM diagnoses WHERE id=? AND user_id=?", (diagnosis_id, uid())).fetchone()
    if not d: c.close(); abort(404)
    existing = c.execute("SELECT id FROM repair_cases WHERE diagnosis_id=? AND user_id=?", (diagnosis_id, uid())).fetchone()
    if existing:
        c.close(); flash("Der findes allerede en løsning til diagnosen."); return redirect(url_for("index") + "#solutions")
    likely = likely_part_for(d["fault_code"], d["symptoms"])
    c.execute("INSERT INTO repair_cases(user_id,truck_id,diagnosis_id,status,priority,likely_part,part_number,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (uid(), d["truck_id"], diagnosis_id, "open", "review", likely, "", "Bekræft del via VIN/OEM-data før bestilling.", now_iso(), now_iso()))
    c.commit(); c.close(); flash("Løsningsforløb oprettet. Nu kan du samle del, værksted og vejhjælp samme sted.")
    return redirect(url_for("index") + "#solutions")

@app.route("/parts/<int:case_id>/add", methods=["POST"])
def add_part_option(case_id):
    g = guard()
    if g: return g
    check_csrf(); c = db()
    case = c.execute("SELECT * FROM repair_cases WHERE id=? AND user_id=?", (case_id, uid())).fetchone()
    if not case: c.close(); abort(404)
    c.execute("INSERT INTO part_options(user_id,case_id,supplier_name,country,part_number,description,price_text,stock_status,eta_text,source_url,verified,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (uid(), case_id, request.form.get("supplier_name", "").strip()[:120], request.form.get("country", "").strip().upper()[:8],
               request.form.get("part_number", "").strip().upper()[:100], request.form.get("description", "").strip()[:500],
               request.form.get("price_text", "").strip()[:80], request.form.get("stock_status", "unknown").strip()[:40],
               request.form.get("eta_text", "").strip()[:100], request.form.get("source_url", "").strip()[:500], 0, now_iso()))
    c.commit(); c.close(); flash("Reservedelsmulighed gemt. Pris/lager står som ikke-verificeret, indtil en rigtig leverandørintegration bekræfter det.")
    return redirect(url_for("index") + "#solutions")

@app.route("/workshops/request", methods=["POST"])
def workshop_request():
    g = guard()
    if g: return g
    check_csrf(); case_id = int(request.form["case_id"]); c = db()
    case = c.execute("SELECT * FROM repair_cases WHERE id=? AND user_id=?", (case_id, uid())).fetchone()
    if not case: c.close(); abort(404)
    status = "pending" if integration_ready("workshops") else "draft"
    c.execute("INSERT INTO workshop_requests(user_id,case_id,workshop_name,city,requested_time,status,contact,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (uid(), case_id, request.form.get("workshop_name", "").strip()[:140], request.form.get("city", "").strip()[:100],
               request.form.get("requested_time", "").strip()[:40], status, request.form.get("contact", "").strip()[:180],
               request.form.get("note", "").strip()[:1000], now_iso(), now_iso()))
    c.commit(); c.close()
    flash("Bookingforespørgsel gemt." if status == "draft" else "Bookingforespørgsel sendt til den konfigurerede integration.")
    return redirect(url_for("index") + "#workshops")

@app.route("/assistance", methods=["POST"])
def assistance_request():
    g = guard()
    if g: return g
    check_csrf(); truck_id = int(request.form["truck_id"]); c = db()
    if not truck_for_user(c, truck_id): c.close(); abort(404)
    status = "pending" if integration_ready("roadside") else "draft"
    c.execute("INSERT INTO assistance_requests(user_id,truck_id,case_id,provider,location_text,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (uid(), truck_id, int(request.form.get("case_id") or 0), request.form.get("provider", "").strip()[:120],
               request.form.get("location_text", "").strip()[:250], status, request.form.get("note", "").strip()[:1000], now_iso(), now_iso()))
    c.commit(); c.close(); flash("Vejhjælpssag gemt. Ring altid 112 ved akut fare.")
    return redirect(url_for("index") + "#assistance")

@app.route("/cases/<int:case_id>/status", methods=["POST"])
def update_case_status(case_id):
    g = guard()
    if g: return g
    check_csrf(); status = request.form.get("status", "open")
    if status not in {"open", "part_found", "workshop_pending", "booked", "repaired", "closed"}: status = "open"
    c = db(); row = c.execute("SELECT id FROM repair_cases WHERE id=? AND user_id=?", (case_id, uid())).fetchone()
    if not row: c.close(); abort(404)
    c.execute("UPDATE repair_cases SET status=?, updated_at=? WHERE id=? AND user_id=?", (status, now_iso(), case_id, uid()))
    c.commit(); c.close(); flash("Status opdateret."); return redirect(url_for("index") + "#solutions")


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
    elif event["type"] in {"invoice.payment_failed", "invoice.payment_action_required"}:
        customer = obj.get("customer") or ""; subscription_id = obj.get("subscription") or ""
        c = db(); sub = c.execute("SELECT * FROM subscriptions WHERE stripe_customer_id=? OR stripe_subscription_id=? LIMIT 1", (customer, subscription_id)).fetchone(); c.close()
        if sub: upsert_subscription(sub["user_id"], sub["plan"], "past_due", customer, subscription_id, sub["current_period_end"])
    elif event["type"] == "invoice.paid":
        customer = obj.get("customer") or ""; subscription_id = obj.get("subscription") or ""
        c = db(); sub = c.execute("SELECT * FROM subscriptions WHERE stripe_customer_id=? OR stripe_subscription_id=? LIMIT 1", (customer, subscription_id)).fetchone(); c.close()
        if sub: upsert_subscription(sub["user_id"], sub["plan"], "active", customer, subscription_id, sub["current_period_end"])
    return "ok", 200


if __name__ == "__main__":
    init_db(); app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
else:
    init_db()
