import os, secrets, sqlite3
from datetime import datetime
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:
    psycopg2 = None
    DictCursor = None

APP_VERSION = '2.0'
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.6-luna')
DATABASE_URL = os.getenv('DATABASE_URL')
DB_PATH = os.getenv('DATABASE_PATH', 'truckos.db')

PLAN_INFO = {
    'driver': {'name':'Chauffør','price_dkk':79,'price_env':'STRIPE_PRICE_DRIVER','features':['1 chaufførkonto','AI-diagnose','På vejen','Servicehistorik','Påmindelser']},
    'pro': {'name':'Vognmand Pro','price_dkk':199,'price_env':'STRIPE_PRICE_PRO','features':['Alt i Chauffør','Flere lastbiler','Flådeoverblik','Fuld historik']},
    'fleet': {'name':'Flåde','price_dkk':499,'price_env':'STRIPE_PRICE_FLEET','features':['Alt i Pro','Reparationsflow','Prioriteret support','Flådeadministration']},
}
INTEGRATIONS = {
    'parts': ('PARTS_API_URL','PARTS_API_KEY'),
    'workshops': ('WORKSHOP_API_URL','WORKSHOP_API_KEY'),
    'roadside': ('ROADSIDE_API_URL','ROADSIDE_API_KEY'),
    'parking': ('PARKING_API_URL','PARKING_API_KEY'),
    'fuel_prices': ('FUEL_API_URL','FUEL_API_KEY'),
    'charging': ('CHARGING_API_URL','CHARGING_API_KEY'),
}

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-this-before-going-public')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('RENDER','').lower()=='true')

class DB:
    def __init__(self):
        self.is_postgres = bool(DATABASE_URL)
        if self.is_postgres:
            if psycopg2 is None: raise RuntimeError('psycopg2 mangler, men DATABASE_URL er sat')
            self.conn = psycopg2.connect(DATABASE_URL)
            self.cur = self.conn.cursor(cursor_factory=DictCursor)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
            self.cur = self.conn.cursor()
    def execute(self, sql, params=()):
        if self.is_postgres: sql = sql.replace('?', '%s')
        self.cur.execute(sql, params); return self.cur
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self): self.cur.close(); self.conn.close()

def db(): return DB()
def now_iso(): return datetime.utcnow().replace(microsecond=0).isoformat()
def uid(): return session.get('user_id')
def integration_ready(name):
    keys=INTEGRATIONS.get(name,()); return bool(keys) and all(os.getenv(k) for k in keys)
def ensure_csrf():
    if 'csrf_token' not in session: session['csrf_token']=secrets.token_urlsafe(24)
    return session['csrf_token']
def check_csrf():
    token=request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
    if not token or token != session.get('csrf_token'): abort(400,'Ugyldig sikkerhedstoken')
def guard(): return None if uid() else redirect(url_for('login'))
def ensure_column(c, table, column, definition):
    if c.is_postgres:
        c.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}')
    else:
        cols=c.execute(f'PRAGMA table_info({table})').fetchall(); names={r[1] for r in cols}
        if column not in names: c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

def init_db():
    c=db()
    serial='SERIAL PRIMARY KEY' if c.is_postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    stmts=[
        f'''CREATE TABLE IF NOT EXISTS users(id {serial},email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY,display_name TEXT NOT NULL DEFAULT '',company TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS trucks(id {serial},user_id INTEGER NOT NULL,name TEXT NOT NULL,plate TEXT NOT NULL,km INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS services(id {serial},user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,service_date TEXT NOT NULL,km INTEGER NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS diagnoses(id {serial},user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,fault_code TEXT,symptoms TEXT NOT NULL,answer TEXT NOT NULL,created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS reminders(id {serial},user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,title TEXT NOT NULL,due_date TEXT NOT NULL DEFAULT '',due_km INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',done INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS repair_cases(id {serial},user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,diagnosis_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'open',priority TEXT NOT NULL DEFAULT 'review',likely_part TEXT NOT NULL DEFAULT '',part_number TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS part_options(id {serial},user_id INTEGER NOT NULL,case_id INTEGER NOT NULL,supplier_name TEXT NOT NULL DEFAULT '',country TEXT NOT NULL DEFAULT '',part_number TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',price_text TEXT NOT NULL DEFAULT '',stock_status TEXT NOT NULL DEFAULT 'unknown',eta_text TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS workshop_requests(id {serial},user_id INTEGER NOT NULL,case_id INTEGER NOT NULL,workshop_name TEXT NOT NULL DEFAULT '',city TEXT NOT NULL DEFAULT '',requested_time TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'draft',contact TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS assistance_requests(id {serial},user_id INTEGER NOT NULL,truck_id INTEGER NOT NULL,case_id INTEGER NOT NULL DEFAULT 0,provider TEXT NOT NULL DEFAULT '',location_text TEXT NOT NULL DEFAULT '',latitude REAL,longitude REAL,status TEXT NOT NULL DEFAULT 'draft',note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS subscriptions(user_id INTEGER PRIMARY KEY,plan TEXT NOT NULL DEFAULT 'free',status TEXT NOT NULL DEFAULT 'inactive',stripe_customer_id TEXT NOT NULL DEFAULT '',stripe_subscription_id TEXT NOT NULL DEFAULT '',current_period_end TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)'''
    ]
    for s in stmts: c.execute(s)
    for col,definition in [('make',"TEXT NOT NULL DEFAULT ''"),('model',"TEXT NOT NULL DEFAULT ''"),('year','INTEGER NOT NULL DEFAULT 0'),('vin',"TEXT NOT NULL DEFAULT ''"),('engine',"TEXT NOT NULL DEFAULT ''"),('registration_country',"TEXT NOT NULL DEFAULT 'DK'"),('fuel',"TEXT NOT NULL DEFAULT ''"),('service_contract_provider',"TEXT NOT NULL DEFAULT ''"),('service_contract_note',"TEXT NOT NULL DEFAULT ''")]:
        ensure_column(c,'trucks',col,definition)
    c.execute('CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_diagnoses_user ON diagnoses(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cases_user ON repair_cases(user_id)')
    c.commit(); c.close()

def truck_for_user(c, truck_id): return c.execute('SELECT * FROM trucks WHERE id=? AND user_id=?',(truck_id,uid())).fetchone()
def subscription_for_user(c,user_id): return c.execute('SELECT * FROM subscriptions WHERE user_id=?',(user_id,)).fetchone()
def stripe_ready(): return bool(os.getenv('STRIPE_SECRET_KEY')) and any(os.getenv(p['price_env']) for p in PLAN_INFO.values())

@app.context_processor
def globals_ctx():
    return {'csrf_token':ensure_csrf(),'app_version':APP_VERSION,'ai_enabled':bool(os.getenv('OPENAI_API_KEY')),'ai_model':OPENAI_MODEL,'plans':PLAN_INFO,'stripe_ready':stripe_ready(),'integration_status':{k:integration_ready(k) for k in INTEGRATIONS},'app_store_url':os.getenv('APP_STORE_URL',''),'play_store_url':os.getenv('PLAY_STORE_URL','')}

def fallback(symptoms, code):
    s=f'{symptoms} {code}'.lower()
    if any(x in s for x in ['bremse','brake','styring','steering','olietryk','oil pressure','overophed','overheat','brand','fire']):
        return 'Prioritet: HØJ\n\nSandsynlige årsager\nSymptomerne kan være sikkerheds- eller motorkritiske.\n\nKontrolpunkter\nFølg køretøjets advarsler og producentens instruktioner.\n\nNæste skridt\nStands hvis producentens anvisninger kræver det, og få lastbilen vurderet af et kvalificeret værksted.'
    if any(x in s for x in ['p0299','turbo','boost','ladetryk','mister kraft']):
        return 'Prioritet: UNDERSØG SNART\n\nSandsynlige årsager\nDet kan passe med et problem omkring ladetryk, indsugning, turbo, intercooler eller sensorik.\n\nKontrolpunkter\nKontrollér synlige slanger og rør, og gem fejlkoder.\n\nNæste skridt\nFå ønsket og faktisk ladetryk vurderet med korrekt diagnoseudstyr.'
    return 'Prioritet: FORELØBIG VURDERING\n\nSandsynlige årsager\nDer er ikke nok information til en sikker kategorisering.\n\nKontrolpunkter\nNotér hvornår fejlen opstår, belastning, temperatur, advarselslamper og fejlkoder.\n\nNæste skridt\nFå relevante data kontrolleret, hvis fejlen fortsætter.'

def ai_answer(truck,symptoms,code):
    key=os.getenv('OPENAI_API_KEY')
    if not key: return fallback(symptoms,code)+'\n\n(Lokal TruckOS-vurdering – OpenAI API er ikke slået til endnu.)'
    try:
        from openai import OpenAI
        client=OpenAI(api_key=key)
        prompt=f"Lastbil: {truck['name']}\nMærke/model: {truck['make']} {truck['model']}\nNummerplade: {truck['plate']}\nVIN: {truck['vin'] or 'ikke oplyst'}\nMotor: {truck['engine'] or 'ikke oplyst'}\nKilometer: {truck['km']}\nFejlkode: {code or 'ingen'}\nSymptomer: {symptoms}"
        r=client.responses.create(model=OPENAI_MODEL,instructions='Du er TruckOS 2.0, forsigtig beslutningsstøtte til lastbilchauffører og vognmænd. Svar på dansk med Prioritet, Sandsynlige årsager, Kontrolpunkter, Næste skridt. Opfind aldrig måleværdier eller en sikker diagnose.',input=prompt)
        return r.output_text.strip()
    except Exception:
        return fallback(symptoms,code)+'\n\n(AI-forbindelsen kunne ikke bruges, så TruckOS viser lokal vurdering.)'

def likely_part_for(code,symptoms):
    t=f'{code} {symptoms}'.lower()
    if any(x in t for x in ['p0299','turbo','boost','ladetryk']): return 'Ladetrykssystem / turboslange / intercooler / sensor'
    if any(x in t for x in ['bremse','brake']): return 'Bremsesystem – kræver faglig kontrol før del vælges'
    if any(x in t for x in ['adblue','scr','nox']): return 'SCR/AdBlue-system / NOx-sensor'
    if any(x in t for x in ['dpf','partikelfilter']): return 'DPF/udstødningssystem'
    return 'Del skal identificeres ud fra VIN/OEM-nummer og værkstedsdiagnose'

@app.route('/')
def index():
    if not uid(): return redirect(url_for('login'))
    c=db(); u=uid()
    trucks=c.execute('SELECT * FROM trucks WHERE user_id=? ORDER BY id DESC',(u,)).fetchall()
    services=c.execute('SELECT s.*,t.name truck_name FROM services s JOIN trucks t ON t.id=s.truck_id WHERE s.user_id=? ORDER BY s.id DESC LIMIT 50',(u,)).fetchall()
    diagnoses=c.execute('SELECT d.*,t.name truck_name FROM diagnoses d JOIN trucks t ON t.id=d.truck_id WHERE d.user_id=? ORDER BY d.id DESC LIMIT 50',(u,)).fetchall()
    reminders=c.execute('SELECT r.*,t.name truck_name FROM reminders r JOIN trucks t ON t.id=r.truck_id WHERE r.user_id=? ORDER BY r.done,r.id DESC',(u,)).fetchall()
    cases=c.execute('SELECT rc.*,t.name truck_name,d.fault_code FROM repair_cases rc JOIN trucks t ON t.id=rc.truck_id JOIN diagnoses d ON d.id=rc.diagnosis_id WHERE rc.user_id=? ORDER BY rc.id DESC',(u,)).fetchall()
    parts=c.execute('SELECT * FROM part_options WHERE user_id=? ORDER BY id DESC',(u,)).fetchall()
    workshops=c.execute('SELECT * FROM workshop_requests WHERE user_id=? ORDER BY id DESC',(u,)).fetchall()
    assistance=c.execute('SELECT a.*,t.name truck_name FROM assistance_requests a JOIN trucks t ON t.id=a.truck_id WHERE a.user_id=? ORDER BY a.id DESC',(u,)).fetchall()
    profile=c.execute('SELECT * FROM profiles WHERE user_id=?',(u,)).fetchone(); sub=subscription_for_user(c,u)
    c.close()
    return render_template('dashboard.html',trucks=trucks,services=services,diagnoses=diagnoses,reminders=reminders,cases=cases,parts=parts,workshops=workshops,assistance=assistance,profile=profile,subscription=sub)

@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        check_csrf(); email=request.form['email'].strip().lower(); pw=request.form['password']
        if len(pw)<8: flash('Adgangskoden skal være mindst 8 tegn.'); return redirect(url_for('register'))
        c=db()
        try:
            c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,generate_password_hash(pw),now_iso())); c.commit()
            user=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(); u=user['id']
            c.execute('INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)',(u,'','',now_iso(),now_iso()))
            c.execute('INSERT INTO subscriptions(user_id,plan,status,updated_at) VALUES(?,?,?,?)',(u,'free','inactive',now_iso())); c.commit()
        except Exception:
            c.rollback(); c.close(); flash('Der findes muligvis allerede en bruger med den e-mail.'); return redirect(url_for('register'))
        c.close(); session.clear(); session['user_id']=u; session['email']=email; ensure_csrf(); return redirect(url_for('index'))
    return render_template('auth.html',mode='register')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        check_csrf(); email=request.form['email'].strip().lower(); pw=request.form['password']; c=db(); user=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone(); c.close()
        if not user or not check_password_hash(user['password_hash'],pw): flash('Forkert e-mail eller adgangskode.'); return redirect(url_for('login'))
        session.clear(); session['user_id']=user['id']; session['email']=user['email']; ensure_csrf(); return redirect(url_for('index'))
    return render_template('auth.html',mode='login')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/profile',methods=['POST'])
def update_profile():
    if guard(): return guard()
    check_csrf(); c=db(); name=request.form.get('display_name','').strip()[:80]; company=request.form.get('company','').strip()[:120]
    if c.execute('SELECT user_id FROM profiles WHERE user_id=?',(uid(),)).fetchone(): c.execute('UPDATE profiles SET display_name=?,company=?,updated_at=? WHERE user_id=?',(name,company,now_iso(),uid()))
    else: c.execute('INSERT INTO profiles(user_id,display_name,company,created_at,updated_at) VALUES(?,?,?,?,?)',(uid(),name,company,now_iso(),now_iso()))
    c.commit(); c.close(); flash('Profil gemt.'); return redirect('/#profile')

@app.route('/trucks',methods=['POST'])
def add_truck():
    if guard(): return guard()
    check_csrf(); c=db()
    try: km=max(0,int(request.form.get('km',0))); year=max(0,int(request.form.get('year',0) or 0))
    except ValueError: flash('Kilometerstand og årgang skal være tal.'); return redirect('/#trucks')
    vals=(uid(),request.form['name'].strip()[:120],request.form['plate'].strip().upper()[:30],km,request.form.get('make','').strip()[:80],request.form.get('model','').strip()[:80],year,request.form.get('vin','').strip().upper()[:40],request.form.get('engine','').strip()[:120],request.form.get('registration_country','DK').strip().upper()[:8],request.form.get('fuel','').strip()[:40],request.form.get('service_contract_provider','').strip()[:120],request.form.get('service_contract_note','').strip()[:500],now_iso())
    c.execute('INSERT INTO trucks(user_id,name,plate,km,make,model,year,vin,engine,registration_country,fuel,service_contract_provider,service_contract_note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',vals); c.commit(); c.close(); flash('Lastbil tilføjet.'); return redirect('/#trucks')

@app.route('/trucks/<int:truck_id>/edit',methods=['POST'])
def edit_truck(truck_id):
    if guard(): return guard()
    check_csrf(); c=db(); tr=truck_for_user(c,truck_id)
    if not tr: c.close(); abort(404)
    km=max(0,int(request.form.get('km',0))); year=max(0,int(request.form.get('year',0) or 0))
    vals=(request.form['name'].strip()[:120],request.form['plate'].strip().upper()[:30],km,request.form.get('make','').strip()[:80],request.form.get('model','').strip()[:80],year,request.form.get('vin','').strip().upper()[:40],request.form.get('engine','').strip()[:120],request.form.get('registration_country','DK').strip().upper()[:8],request.form.get('fuel','').strip()[:40],request.form.get('service_contract_provider','').strip()[:120],request.form.get('service_contract_note','').strip()[:500],truck_id,uid())
    c.execute('UPDATE trucks SET name=?,plate=?,km=?,make=?,model=?,year=?,vin=?,engine=?,registration_country=?,fuel=?,service_contract_provider=?,service_contract_note=? WHERE id=? AND user_id=?',vals); c.commit(); c.close(); flash('Lastbil opdateret.'); return redirect('/#trucks')

@app.route('/services',methods=['POST'])
def add_service():
    if guard(): return guard()
    check_csrf(); tid=int(request.form['truck_id']); c=db()
    if not truck_for_user(c,tid): c.close(); abort(404)
    c.execute('INSERT INTO services(user_id,truck_id,service_date,km,note,created_at) VALUES(?,?,?,?,?,?)',(uid(),tid,request.form['service_date'],int(request.form.get('km',0)),request.form.get('note','').strip()[:1000],now_iso())); c.commit(); c.close(); flash('Service gemt.'); return redirect('/#service')

@app.route('/reminders',methods=['POST'])
def add_reminder():
    if guard(): return guard()
    check_csrf(); tid=int(request.form['truck_id']); c=db()
    if not truck_for_user(c,tid): c.close(); abort(404)
    c.execute('INSERT INTO reminders(user_id,truck_id,title,due_date,due_km,note,done,created_at) VALUES(?,?,?,?,?,?,?,?)',(uid(),tid,request.form['title'].strip()[:140],request.form.get('due_date',''),int(request.form.get('due_km') or 0),request.form.get('note','').strip()[:500],0,now_iso())); c.commit(); c.close(); flash('Påmindelse gemt.'); return redirect('/#reminders')

@app.route('/reminders/<int:rid>/toggle',methods=['POST'])
def toggle_reminder(rid):
    if guard(): return guard()
    check_csrf(); c=db(); r=c.execute('SELECT done FROM reminders WHERE id=? AND user_id=?',(rid,uid())).fetchone()
    if not r: c.close(); abort(404)
    c.execute('UPDATE reminders SET done=? WHERE id=? AND user_id=?',(0 if r['done'] else 1,rid,uid())); c.commit(); c.close(); return redirect('/#reminders')

@app.route('/api/diagnose',methods=['POST'])
def diagnose():
    if not uid(): return jsonify({'error':'login_required'}),401
    check_csrf(); d=request.get_json(silent=True) or request.form; tid=int(d.get('truck_id')); code=str(d.get('fault_code','')).strip()[:80]; symptoms=str(d.get('symptoms','')).strip()[:1500]
    c=db(); tr=truck_for_user(c,tid)
    if not tr: c.close(); return jsonify({'error':'truck_not_found'}),404
    answer=ai_answer(tr,symptoms,code); c.execute('INSERT INTO diagnoses(user_id,truck_id,fault_code,symptoms,answer,created_at) VALUES(?,?,?,?,?,?)',(uid(),tid,code,symptoms,answer,now_iso())); c.commit(); row=c.execute('SELECT id FROM diagnoses WHERE user_id=? ORDER BY id DESC LIMIT 1',(uid(),)).fetchone(); c.close()
    return jsonify({'answer':answer,'diagnosis_id':row['id'] if row else 0,'ai':bool(os.getenv('OPENAI_API_KEY'))})

@app.route('/solutions/<int:diagnosis_id>',methods=['POST'])
def create_solution(diagnosis_id):
    if guard(): return guard()
    check_csrf(); c=db(); d=c.execute('SELECT * FROM diagnoses WHERE id=? AND user_id=?',(diagnosis_id,uid())).fetchone()
    if not d: c.close(); abort(404)
    if not c.execute('SELECT id FROM repair_cases WHERE diagnosis_id=? AND user_id=?',(diagnosis_id,uid())).fetchone():
        c.execute('INSERT INTO repair_cases(user_id,truck_id,diagnosis_id,status,priority,likely_part,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(uid(),d['truck_id'],diagnosis_id,'open','review',likely_part_for(d['fault_code'],d['symptoms']),'Bekræft del via VIN/OEM-data før bestilling.',now_iso(),now_iso())); c.commit()
    c.close(); flash('Løsningsforløb oprettet.'); return redirect('/#solutions')

@app.route('/parts/<int:case_id>/add',methods=['POST'])
def add_part(case_id):
    if guard(): return guard()
    check_csrf(); c=db(); case=c.execute('SELECT id FROM repair_cases WHERE id=? AND user_id=?',(case_id,uid())).fetchone()
    if not case: c.close(); abort(404)
    c.execute('INSERT INTO part_options(user_id,case_id,supplier_name,country,part_number,description,price_text,stock_status,eta_text,source_url,verified,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uid(),case_id,request.form.get('supplier_name','')[:120],request.form.get('country','')[:8],request.form.get('part_number','')[:100],request.form.get('description','')[:500],request.form.get('price_text','')[:80],request.form.get('stock_status','unknown')[:40],request.form.get('eta_text','')[:100],request.form.get('source_url','')[:500],0,now_iso())); c.commit(); c.close(); flash('Reservedel gemt som ikke-verificeret.'); return redirect('/#solutions')

@app.route('/workshops/request',methods=['POST'])
def workshop_request():
    if guard(): return guard()
    check_csrf(); case_id=int(request.form['case_id']); c=db()
    if not c.execute('SELECT id FROM repair_cases WHERE id=? AND user_id=?',(case_id,uid())).fetchone(): c.close(); abort(404)
    status='pending' if integration_ready('workshops') else 'draft'
    c.execute('INSERT INTO workshop_requests(user_id,case_id,workshop_name,city,requested_time,status,contact,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),case_id,request.form.get('workshop_name','')[:140],request.form.get('city','')[:100],request.form.get('requested_time','')[:40],status,request.form.get('contact','')[:180],request.form.get('note','')[:1000],now_iso(),now_iso())); c.commit(); c.close(); flash('Bookingforespørgsel gemt.' if status=='draft' else 'Bookingforespørgsel sendt til integration.'); return redirect('/#workshops')

@app.route('/assistance',methods=['POST'])
def assistance_request():
    if guard(): return guard()
    check_csrf(); tid=int(request.form['truck_id']); c=db()
    if not truck_for_user(c,tid): c.close(); abort(404)
    status='pending' if integration_ready('roadside') else 'draft'; provider=request.form.get('provider','')[:120]
    lat=float(request.form.get('latitude')) if request.form.get('latitude') else None; lng=float(request.form.get('longitude')) if request.form.get('longitude') else None
    c.execute('INSERT INTO assistance_requests(user_id,truck_id,case_id,provider,location_text,latitude,longitude,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),tid,int(request.form.get('case_id') or 0),provider,request.form.get('location_text','')[:250],lat,lng,status,request.form.get('note','')[:1000],now_iso(),now_iso())); c.commit(); c.close(); flash('Vejhjælpssag gemt som kladde.' if status=='draft' else 'Vejhjælpssag sendt til den konfigurerede integration.'); return redirect('/#assistance')

@app.route('/cases/<int:case_id>/status',methods=['POST'])
def case_status(case_id):
    if guard(): return guard()
    check_csrf(); status=request.form.get('status','open')
    if status not in {'open','part_found','workshop_pending','booked','repaired','closed'}: status='open'
    c=db(); c.execute('UPDATE repair_cases SET status=?,updated_at=? WHERE id=? AND user_id=?',(status,now_iso(),case_id,uid())); c.commit(); c.close(); return redirect('/#solutions')

@app.route('/billing/checkout/<plan>',methods=['POST'])
def billing_checkout(plan):
    if guard(): return guard()
    check_csrf()
    if plan not in PLAN_INFO: abort(404)
    try:
        import stripe
        stripe.api_key=os.getenv('STRIPE_SECRET_KEY'); price=os.getenv(PLAN_INFO[plan]['price_env'])
        if not stripe.api_key or not price: raise RuntimeError('Stripe testmiljø er ikke konfigureret')
        checkout=stripe.checkout.Session.create(mode='subscription',line_items=[{'price':price,'quantity':1}],success_url=url_for('index',_external=True)+'?payment=success',cancel_url=url_for('index',_external=True)+'?payment=cancel',customer_email=session.get('email'),metadata={'user_id':str(uid()),'plan':plan})
        return redirect(checkout.url)
    except Exception as e:
        flash(f'Stripe kunne ikke starte checkout: {e}'); return redirect('/#billing')

@app.route('/billing/portal',methods=['POST'])
def billing_portal():
    if guard(): return guard()
    check_csrf(); c=db(); sub=subscription_for_user(c,uid()); c.close()
    try:
        import stripe
        stripe.api_key=os.getenv('STRIPE_SECRET_KEY')
        if not sub or not sub['stripe_customer_id']: raise RuntimeError('Ingen Stripe-kunde fundet endnu')
        portal=stripe.billing_portal.Session.create(customer=sub['stripe_customer_id'],return_url=url_for('index',_external=True)); return redirect(portal.url)
    except Exception as e: flash(f'Kundeportal kunne ikke åbnes: {e}'); return redirect('/#billing')

@app.route('/stripe/webhook',methods=['POST'])
def stripe_webhook():
    try:
        import stripe
        stripe.api_key=os.getenv('STRIPE_SECRET_KEY'); secret=os.getenv('STRIPE_WEBHOOK_SECRET')
        event=stripe.Webhook.construct_event(request.data,request.headers.get('Stripe-Signature',''),secret) if secret else request.get_json(force=True)
    except Exception: return 'invalid',400
    obj=event.get('data',{}).get('object',{}); et=event.get('type','')
    user_id=None; plan='free'; customer=obj.get('customer','') or ''; subscription=obj.get('subscription','') or obj.get('id','') if 'subscription' in et else ''
    md=obj.get('metadata') or {}
    if md.get('user_id'): user_id=int(md['user_id']); plan=md.get('plan','free')
    if user_id:
        c=db(); existing=subscription_for_user(c,user_id); status=obj.get('status','active' if 'checkout.session.completed'==et else 'inactive')
        if existing: c.execute('UPDATE subscriptions SET plan=?,status=?,stripe_customer_id=?,stripe_subscription_id=?,updated_at=? WHERE user_id=?',(plan,status,customer,subscription,now_iso(),user_id))
        else: c.execute('INSERT INTO subscriptions(user_id,plan,status,stripe_customer_id,stripe_subscription_id,updated_at) VALUES(?,?,?,?,?,?)',(user_id,plan,status,customer,subscription,now_iso()))
        c.commit(); c.close()
    return 'ok',200

@app.route('/download')
def download_page(): return render_template('download.html')
@app.route('/health')
def health(): return jsonify({'ok':True,'version':APP_VERSION})

init_db()
from truckos_v2 import register as register_truckos_v2
register_truckos_v2(app)

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT',5000)),debug=os.getenv('FLASK_DEBUG')=='1')
