import os, sqlite3
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY','change-this-before-going-public')
DATABASE_URL = os.getenv('DATABASE_URL')
DB_PATH = os.getenv('DATABASE_PATH','truckos.db')

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
            sql = sql.replace('?', '%s')
        self.cur.execute(sql, params)
        return self.cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.cur.close()
        self.conn.close()

def db():
    return DB()

def init_db():
    c=db()
    if c.is_postgres:
        statements = [
            '''CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS trucks(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,name TEXT NOT NULL,plate TEXT NOT NULL,km INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS services(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,service_date TEXT NOT NULL,km INTEGER NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS diagnoses(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,fault_code TEXT,symptoms TEXT NOT NULL,answer TEXT NOT NULL,created_at TEXT NOT NULL)'''
        ]
        for stmt in statements:
            c.execute(stmt)
    else:
        statements = [
            '''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS trucks(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,plate TEXT NOT NULL,km INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,service_date TEXT NOT NULL,km INTEGER NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS diagnoses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,fault_code TEXT,symptoms TEXT NOT NULL,answer TEXT NOT NULL,created_at TEXT NOT NULL)'''
        ]
        for stmt in statements:
            c.execute(stmt)
    c.commit(); c.close()

def uid(): return session.get('user_id')
def guard(): return None if uid() else redirect(url_for('login'))

def fallback(symptoms, code):
    s=f'{symptoms} {code}'.lower()
    if any(x in s for x in ['bremse','brake','styring','steering','olietryk','oil pressure','overophed','overheat']):
        return 'HØJ PRIORITET\n\nSymptomerne kan være sikkerheds- eller motorkritiske. Følg køretøjets advarsler og producentens instruktioner, og få bilen vurderet af et kvalificeret værksted.'
    if any(x in s for x in ['p0299','turbo','boost','mister trækkraft','mister kraft']):
        return 'UNDERSØG SNART\n\nDet kan passe med et problem omkring ladetryk, indsugning, turbo, intercooler eller sensorik. Kontrollér synlige slanger/rør, gem fejlkoder og få ønsket/faktisk ladetryk vurderet med korrekt diagnoseudstyr.'
    return 'FORELØBIG VURDERING\n\nDer er ikke nok information til en sikker kategorisering. Notér hvornår fejlen opstår, belastning, temperatur, advarselslamper og eventuelle fejlkoder.'

def ai_answer(truck, symptoms, code):
    key=os.getenv('OPENAI_API_KEY'); model=os.getenv('OPENAI_MODEL')
    if not key or not model: return fallback(symptoms, code)
    try:
        from openai import OpenAI
        client=OpenAI(api_key=key)
        instructions=('Du er TruckOS, forsigtig beslutningsstøtte til chauffører og vognmænd. '
                      'Du har ikke fysisk inspiceret bilen. Forklar mulige årsager og kontrolpunkter. '
                      'Ved sikkerhedskritiske forhold skal du tydeligt anbefale producentens instruktioner og kvalificeret værksted. '
                      'Svar på dansk med: Prioritet, Mulige årsager, Kontrolpunkter, Næste skridt.')
        prompt=f"Lastbil: {truck['name']}\nPlade: {truck['plate']}\nKm: {truck['km']}\nFejlkode: {code or 'ingen'}\nSymptomer: {symptoms}"
        r=client.responses.create(model=model,instructions=instructions,input=prompt)
        return getattr(r,'output_text','').strip() or fallback(symptoms,code)
    except Exception:
        return fallback(symptoms,code)+'\n\n(AI-forbindelsen var ikke tilgængelig, så TruckOS brugte lokal fallback.)'

@app.route('/')
def index():
    if not uid(): return redirect(url_for('login'))
    c=db(); u=uid()
    trucks=c.execute('SELECT * FROM trucks WHERE user_id=? ORDER BY id DESC',(u,)).fetchall()
    services=c.execute('SELECT * FROM services WHERE user_id=? ORDER BY id DESC LIMIT 8',(u,)).fetchall()
    diagnoses=c.execute('SELECT * FROM diagnoses WHERE user_id=? ORDER BY id DESC LIMIT 8',(u,)).fetchall()
    counts={'trucks':c.execute('SELECT COUNT(*) FROM trucks WHERE user_id=?',(u,)).fetchone()[0],
            'services':c.execute('SELECT COUNT(*) FROM services WHERE user_id=?',(u,)).fetchone()[0],
            'diagnoses':c.execute('SELECT COUNT(*) FROM diagnoses WHERE user_id=?',(u,)).fetchone()[0]}
    c.close(); return render_template('dashboard.html',trucks=trucks,services=services,diagnoses=diagnoses,counts=counts)

@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        email=request.form['email'].strip().lower(); password=request.form['password']
        if len(password)<8: flash('Adgangskoden skal være mindst 8 tegn.'); return redirect(url_for('register'))
        c=db()
        try:
            c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,generate_password_hash(password),datetime.utcnow().isoformat())); c.commit()
        except (sqlite3.IntegrityError, psycopg2.IntegrityError):
            c.close(); flash('Der findes allerede en bruger med den e-mail.'); return redirect(url_for('register'))
        user=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(); c.close(); session['user_id']=user['id']; session['email']=email
        return redirect(url_for('index'))
    return render_template('auth.html',mode='register')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form['email'].strip().lower(); password=request.form['password']; c=db(); user=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone(); c.close()
        if not user or not check_password_hash(user['password_hash'],password): flash('Forkert e-mail eller adgangskode.'); return redirect(url_for('login'))
        session['user_id']=user['id']; session['email']=user['email']; return redirect(url_for('index'))
    return render_template('auth.html',mode='login')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/trucks',methods=['POST'])
def add_truck():
    g=guard()
    if g: return g
    c=db(); c.execute('INSERT INTO trucks(user_id,name,plate,km,created_at) VALUES(?,?,?,?,?)',(uid(),request.form['name'].strip(),request.form['plate'].strip().upper(),int(request.form['km']),datetime.utcnow().isoformat())); c.commit(); c.close(); return redirect(url_for('index'))

@app.route('/service',methods=['POST'])
def add_service():
    g=guard()
    if g: return g
    tid=int(request.form['truck_id']); c=db(); own=c.execute('SELECT id FROM trucks WHERE id=? AND user_id=?',(tid,uid())).fetchone()
    if not own: c.close(); return 'Ikke tilladt',403
    c.execute('INSERT INTO services(user_id,truck_id,service_date,km,note,created_at) VALUES(?,?,?,?,?,?)',(uid(),tid,request.form['service_date'],int(request.form['km']),request.form['note'].strip(),datetime.utcnow().isoformat())); c.commit(); c.close(); return redirect(url_for('index'))

@app.route('/api/diagnose',methods=['POST'])
def diagnose():
    if not uid(): return jsonify({'error':'Log ind først'}),401
    data=request.get_json(force=True); tid=int(data.get('truck_id')); symptoms=(data.get('symptoms') or '').strip(); code=(data.get('fault_code') or '').strip().upper()
    if not symptoms: return jsonify({'error':'Beskriv symptomerne'}),400
    c=db(); truck=c.execute('SELECT * FROM trucks WHERE id=? AND user_id=?',(tid,uid())).fetchone()
    if not truck: c.close(); return jsonify({'error':'Lastbil ikke fundet'}),404
    answer=ai_answer(truck,symptoms,code); c.execute('INSERT INTO diagnoses(user_id,truck_id,fault_code,symptoms,answer,created_at) VALUES(?,?,?,?,?,?)',(uid(),tid,code,symptoms,answer,datetime.utcnow().isoformat())); c.commit(); c.close(); return jsonify({'answer':answer})

if __name__=='__main__':
    init_db(); app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=True)
else: init_db()
