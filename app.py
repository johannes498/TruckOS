import os
import secrets
import sqlite3
from datetime import datetime

import psycopg2
from psycopg2.extras import DictCursor
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


class DB:
    def __init__(self):
        self.is_postgres = bool(DATABASE_URL)
        if self.is_postgres:
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
        ]
    for stmt in statements:
        c.execute(stmt)
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


@app.context_processor
def inject_globals():
    return {
        "csrf_token": ensure_csrf(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "ai_model": OPENAI_MODEL,
    }


def check_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not token or token != session.get("csrf_token"):
        abort(400, "Ugyldig sikkerhedstoken")


def truck_for_user(c, truck_id):
    return c.execute(
        "SELECT * FROM trucks WHERE id=? AND user_id=?", (truck_id, uid())
    ).fetchone()


def fallback(symptoms, code):
    s = f"{symptoms} {code}".lower()
    if any(
        x in s
        for x in [
            "bremse",
            "brake",
            "styring",
            "steering",
            "olietryk",
            "oil pressure",
            "overophed",
            "overheat",
        ]
    ):
        return (
            "HØJ PRIORITET\n\n"
            "Symptomerne kan være sikkerheds- eller motorkritiske. Følg køretøjets "
            "advarsler og producentens instruktioner, og få lastbilen vurderet af et "
            "kvalificeret værksted."
        )
    if any(x in s for x in ["p0299", "turbo", "boost", "mister trækkraft", "mister kraft"]):
        return (
            "UNDERSØG SNART\n\n"
            "Det kan passe med et problem omkring ladetryk, indsugning, turbo, intercooler "
            "eller sensorik. Kontrollér synlige slanger/rør, gem fejlkoder og få ønsket/faktisk "
            "ladetryk vurderet med korrekt diagnoseudstyr."
        )
    return (
        "FORELØBIG VURDERING\n\n"
        "Der er ikke nok information til en sikker kategorisering. Notér hvornår fejlen opstår, "
        "belastning, temperatur, advarselslamper og eventuelle fejlkoder."
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
            "Prioritér sikkerhed. Hvis symptomer kan være bremse-, styrings-, dæk-, brand-, olie-, "
            "temperatur- eller anden sikkerhedskritisk fejl, så sig tydeligt at køretøjet bør standses "
            "eller vurderes efter producentens instruktioner og af kvalificeret fagperson. "
            "Svar på dansk i korte afsnit med overskrifterne: Prioritet, Sandsynlige årsager, "
            "Kontrolpunkter, Næste skridt. Undgå at opfinde specifikke måleværdier eller procedurer."
        )
        prompt = (
            f"Lastbil: {truck['name']}\n"
            f"Nummerplade: {truck['plate']}\n"
            f"Kilometerstand: {truck['km']}\n"
            f"Fejlkode: {code or 'ingen oplyst'}\n"
            f"Symptomer: {symptoms}"
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=prompt,
        )
        text = getattr(response, "output_text", "").strip()
        return text or fallback(symptoms, code)
    except Exception:
        return fallback(symptoms, code) + "\n\n(AI-forbindelsen var ikke tilgængelig, så TruckOS brugte lokal fallback.)"


@app.route("/health")
def health():
    try:
        c = db()
        c.execute("SELECT 1").fetchone()
        c.close()
        return jsonify({"ok": True, "database": "postgres" if DATABASE_URL else "sqlite"})
    except Exception:
        return jsonify({"ok": False}), 503


@app.route("/")
def index():
    if not uid():
        return redirect(url_for("login"))
    c = db()
    u = uid()
    trucks = c.execute("SELECT * FROM trucks WHERE user_id=? ORDER BY id DESC", (u,)).fetchall()
    services = c.execute(
        """SELECT services.*, trucks.name AS truck_name, trucks.plate AS truck_plate
           FROM services JOIN trucks ON trucks.id=services.truck_id
           WHERE services.user_id=? ORDER BY services.service_date DESC, services.id DESC LIMIT 50""",
        (u,),
    ).fetchall()
    diagnoses = c.execute(
        """SELECT diagnoses.*, trucks.name AS truck_name, trucks.plate AS truck_plate
           FROM diagnoses JOIN trucks ON trucks.id=diagnoses.truck_id
           WHERE diagnoses.user_id=? ORDER BY diagnoses.id DESC LIMIT 20""",
        (u,),
    ).fetchall()
    profile = c.execute("SELECT * FROM profiles WHERE user_id=?", (u,)).fetchone()
    counts = {
        "trucks": c.execute("SELECT COUNT(*) FROM trucks WHERE user_id=?", (u,)).fetchone()[0],
        "services": c.execute("SELECT COUNT(*) FROM services WHERE user_id=?", (u,)).fetchone()[0],
        "diagnoses": c.execute("SELECT COUNT(*) FROM diagnoses WHERE user_id=?", (u,)).fetchone()[0],
    }
    c.close()
    return render_template(
        "dashboard.html",
        trucks=trucks,
        services=services,
        diagnoses=diagnoses,
        counts=counts,
        profile=profile,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        check_csrf()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if len(password) < 8:
            flash("Adgangskoden skal være mindst 8 tegn.")
            return redirect(url_for("register"))
        c = db()
        try:
            c.execute(
                "INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                (email, generate_password_hash(password), now_iso()),
            )
            c.commit()
        except (sqlite3.IntegrityError, psycopg2.IntegrityError):
            c.rollback()
            c.close()
            flash("Der findes allerede en bruger med den e-mail.")
            return redirect(url_for("register"))
        user = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        c.execute(
            "INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)",
            (user["id"], "", "", now_iso(), now_iso()),
        )
        c.commit()
        c.close()
        session["user_id"] = user["id"]
        session["email"] = email
        ensure_csrf()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        check_csrf()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        c = db()
        user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        c.close()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Forkert e-mail eller adgangskode.")
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = user["id"]
        session["email"] = user["email"]
        ensure_csrf()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/profile", methods=["POST"])
def update_profile():
    g = guard()
    if g:
        return g
    check_csrf()
    display_name = request.form.get("display_name", "").strip()[:80]
    company = request.form.get("company", "").strip()[:120]
    c = db()
    existing = c.execute("SELECT user_id FROM profiles WHERE user_id=?", (uid(),)).fetchone()
    if existing:
        c.execute(
            "UPDATE profiles SET display_name=?, company=?, updated_at=? WHERE user_id=?",
            (display_name, company, now_iso(), uid()),
        )
    else:
        c.execute(
            "INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)",
            (uid(), display_name, company, now_iso(), now_iso()),
        )
    c.commit()
    c.close()
    flash("Profil gemt.")
    return redirect(url_for("index") + "#profile")


@app.route("/trucks", methods=["POST"])
def add_truck():
    g = guard()
    if g:
        return g
    check_csrf()
    try:
        km = max(0, int(request.form["km"]))
    except ValueError:
        flash("Kilometerstand skal være et tal.")
        return redirect(url_for("index") + "#trucks")
    name = request.form["name"].strip()[:120]
    plate = request.form["plate"].strip().upper()[:30]
    if not name or not plate:
        flash("Navn og nummerplade skal udfyldes.")
        return redirect(url_for("index") + "#trucks")
    c = db()
    c.execute(
        "INSERT INTO trucks(user_id,name,plate,km,created_at) VALUES(?,?,?,?,?)",
        (uid(), name, plate, km, now_iso()),
    )
    c.commit()
    c.close()
    flash("Lastbil tilføjet.")
    return redirect(url_for("index") + "#trucks")


@app.route("/trucks/<int:truck_id>/edit", methods=["POST"])
def edit_truck(truck_id):
    g = guard()
    if g:
        return g
    check_csrf()
    c = db()
    truck = truck_for_user(c, truck_id)
    if not truck:
        c.close()
        abort(404)
    try:
        km = max(0, int(request.form["km"]))
    except ValueError:
        c.close()
        flash("Kilometerstand skal være et tal.")
        return redirect(url_for("index") + "#trucks")
    c.execute(
        "UPDATE trucks SET name=?, plate=?, km=? WHERE id=? AND user_id=?",
        (
            request.form["name"].strip()[:120],
            request.form["plate"].strip().upper()[:30],
            km,
            truck_id,
            uid(),
        ),
    )
    c.commit()
    c.close()
    flash("Lastbil opdateret.")
    return redirect(url_for("index") + "#trucks")


@app.route("/trucks/<int:truck_id>/delete", methods=["POST"])
def delete_truck(truck_id):
    g = guard()
    if g:
        return g
    check_csrf()
    c = db()
    truck = truck_for_user(c, truck_id)
    if not truck:
        c.close()
        abort(404)
    c.execute("DELETE FROM services WHERE truck_id=? AND user_id=?", (truck_id, uid()))
    c.execute("DELETE FROM diagnoses WHERE truck_id=? AND user_id=?", (truck_id, uid()))
    c.execute("DELETE FROM trucks WHERE id=? AND user_id=?", (truck_id, uid()))
    c.commit()
    c.close()
    flash("Lastbil og dens historik er slettet.")
    return redirect(url_for("index") + "#trucks")


@app.route("/service", methods=["POST"])
def add_service():
    g = guard()
    if g:
        return g
    check_csrf()
    tid = int(request.form["truck_id"])
    c = db()
    if not truck_for_user(c, tid):
        c.close()
        return "Ikke tilladt", 403
    try:
        km = max(0, int(request.form["km"]))
    except ValueError:
        c.close()
        flash("Kilometerstand skal være et tal.")
        return redirect(url_for("index") + "#service")
    c.execute(
        "INSERT INTO services(user_id,truck_id,service_date,km,note,created_at) VALUES(?,?,?,?,?,?)",
        (
            uid(),
            tid,
            request.form["service_date"],
            km,
            request.form["note"].strip()[:2000],
            now_iso(),
        ),
    )
    c.commit()
    c.close()
    flash("Servicepost gemt.")
    return redirect(url_for("index") + "#service")


@app.route("/service/<int:service_id>/edit", methods=["POST"])
def edit_service(service_id):
    g = guard()
    if g:
        return g
    check_csrf()
    c = db()
    row = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", (service_id, uid())).fetchone()
    if not row:
        c.close()
        abort(404)
    tid = int(request.form["truck_id"])
    if not truck_for_user(c, tid):
        c.close()
        return "Ikke tilladt", 403
    c.execute(
        "UPDATE services SET truck_id=?, service_date=?, km=?, note=? WHERE id=? AND user_id=?",
        (
            tid,
            request.form["service_date"],
            max(0, int(request.form["km"])),
            request.form["note"].strip()[:2000],
            service_id,
            uid(),
        ),
    )
    c.commit()
    c.close()
    flash("Servicepost opdateret.")
    return redirect(url_for("index") + "#service")


@app.route("/service/<int:service_id>/delete", methods=["POST"])
def delete_service(service_id):
    g = guard()
    if g:
        return g
    check_csrf()
    c = db()
    c.execute("DELETE FROM services WHERE id=? AND user_id=?", (service_id, uid()))
    c.commit()
    c.close()
    flash("Servicepost slettet.")
    return redirect(url_for("index") + "#service")


@app.route("/api/diagnose", methods=["POST"])
def diagnose():
    if not uid():
        return jsonify({"error": "Log ind først"}), 401
    check_csrf()
    data = request.get_json(force=True)
    try:
        tid = int(data.get("truck_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Vælg en lastbil"}), 400
    symptoms = (data.get("symptoms") or "").strip()[:4000]
    code = (data.get("fault_code") or "").strip().upper()[:50]
    if not symptoms:
        return jsonify({"error": "Beskriv symptomerne"}), 400
    c = db()
    truck = truck_for_user(c, tid)
    if not truck:
        c.close()
        return jsonify({"error": "Lastbil ikke fundet"}), 404
    answer = ai_answer(truck, symptoms, code)
    c.execute(
        "INSERT INTO diagnoses(user_id,truck_id,fault_code,symptoms,answer,created_at) VALUES(?,?,?,?,?,?)",
        (uid(), tid, code, symptoms, answer, now_iso()),
    )
    c.commit()
    c.close()
    return jsonify({"answer": answer, "ai": bool(os.getenv("OPENAI_API_KEY"))})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
else:
    init_db()
