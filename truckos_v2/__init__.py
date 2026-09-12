import os, json, urllib.parse, urllib.request
from datetime import datetime
from flask import Blueprint, jsonify, render_template, request, session, abort

bp = Blueprint('truckos_v2', __name__, template_folder='templates', static_folder='static', static_url_path='/truckos-v2-static')
FUEL_NETWORKS = ['Shell','Circle K','Q8','OK','Uno-X','IDS','DKV','UTA','Andet']

def _uid(): return session.get('user_id')
def _now(): return datetime.utcnow().replace(microsecond=0).isoformat()+'Z'
def _check_csrf():
    token=request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
    if not token or token != session.get('csrf_token'):
        abort(400, 'Ugyldig sikkerhedstoken')

def _db():
    from app import db
    return db()

def init_v2_db():
    c=_db()
    if c.is_postgres:
        stmts=[
          "CREATE TABLE IF NOT EXISTS fuel_cards(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,network TEXT NOT NULL,label TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)",
          "CREATE TABLE IF NOT EXISTS road_searches(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,query TEXT NOT NULL,lat REAL,lng REAL,created_at TEXT NOT NULL)"
        ]
    else:
        stmts=[
          "CREATE TABLE IF NOT EXISTS fuel_cards(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,network TEXT NOT NULL,label TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)",
          "CREATE TABLE IF NOT EXISTS road_searches(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,query TEXT NOT NULL,lat REAL,lng REAL,created_at TEXT NOT NULL)"
        ]
    for s in stmts: c.execute(s)
    # Leon feedback: service contract belongs on truck profile, but is optional.
    from app import ensure_column
    ensure_column(c,'trucks','service_contract_provider',"TEXT NOT NULL DEFAULT ''")
    ensure_column(c,'trucks','service_contract_note',"TEXT NOT NULL DEFAULT ''")
    c.execute('CREATE INDEX IF NOT EXISTS idx_fuel_cards_user ON fuel_cards(user_id)')
    c.commit(); c.close()

def _overpass(lat, lng, radius=12000):
    # OpenStreetMap basisdata. Ingen påstand om live-ledighed eller live-priser.
    q = f"""
[out:json][timeout:18];
(
  nwr(around:{radius},{lat},{lng})["amenity"="parking"]["hgv"="yes"];
  nwr(around:{radius},{lat},{lng})["amenity"="parking"]["hgv"="designated"];
  nwr(around:{radius},{lat},{lng})["highway"="rest_area"];
  nwr(around:{radius},{lat},{lng})["highway"="services"];
  nwr(around:{radius},{lat},{lng})["amenity"="fuel"];
  nwr(around:{radius},{lat},{lng})["amenity"="charging_station"];
  nwr(around:{radius},{lat},{lng})["amenity"="car_wash"]["hgv"="yes"];
  nwr(around:{radius},{lat},{lng})["shop"="truck_repair"];
  nwr(around:{radius},{lat},{lng})["amenity"="toilets"];
);
out center tags 60;
"""

    encoded = urllib.parse.urlencode({"data": q}).encode()

    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=encoded,
        headers={"User-Agent": "TruckOS/2.0"},
    )

    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)

    out = []

    for e in data.get("elements", []):
        tags = e.get("tags", {})

        lat_value = e.get("lat")
        lng_value = e.get("lon")

        if lat_value is None:
            lat_value = e.get("center", {}).get("lat")

        if lng_value is None:
            lng_value = e.get("center", {}).get("lon")

        if lat_value is None or lng_value is None:
            continue

        amenity = tags.get("amenity", "")
        highway = tags.get("highway", "")
        typ = "sted"

        if highway in ("rest_area", "services"):
            typ = "parking"
        elif amenity == "parking":
            typ = "parking"
        elif amenity == "fuel":
            typ = "fuel"
        elif amenity == "charging_station":
            typ = "charging"
        elif amenity == "car_wash":
            typ = "wash"
        elif tags.get("shop") == "truck_repair":
            typ = "workshop"
        elif amenity == "toilets":
            typ = "toilet"

        out.append({
            "id": f"osm-{e.get('type')}-{e.get('id')}",
            "name": tags.get("name") or tags.get("brand") or typ.title(),
            "type": typ,
            "lat": lat_value,
            "lng": lng_value,
            "brand": tags.get("brand", ""),
            "opening_hours": tags.get("opening_hours", ""),
        })

    return out

def _cards(user_id):
    c=_db(); rows=c.execute('SELECT * FROM fuel_cards WHERE user_id=? AND active=1 ORDER BY id DESC',(user_id,)).fetchall(); c.close()
    return [dict(r) for r in rows]

@bp.route('/road')
def road():
    if not _uid():
        from flask import redirect, url_for
        return redirect(url_for('login'))
    return render_template('road_v2.html', fuel_networks=FUEL_NETWORKS, cards=_cards(_uid()))

@bp.route('/api/v2/fuel-cards', methods=['GET','POST'])
def fuel_cards():
    if not _uid(): return jsonify({'error':'login_required'}),401
    c=_db()
    if request.method=='POST':
        _check_csrf()
        data=request.get_json(silent=True) or {}; network=str(data.get('network','')).strip()[:60]
        if not network: c.close(); return jsonify({'error':'network_required'}),400
        c.execute('INSERT INTO fuel_cards(user_id,network,label,active,created_at) VALUES(?,?,?,?,?)',(_uid(),network,str(data.get('label',''))[:80],1,_now())); c.commit()
    rows=c.execute('SELECT * FROM fuel_cards WHERE user_id=? ORDER BY id DESC',(_uid(),)).fetchall(); c.close()
    return jsonify([dict(r) for r in rows])

@bp.route('/api/v2/fuel-cards/<int:card_id>', methods=['DELETE'])
def delete_card(card_id):
    if not _uid(): return jsonify({'error':'login_required'}),401
    _check_csrf()
    c=_db(); c.execute('DELETE FROM fuel_cards WHERE id=? AND user_id=?',(card_id,_uid())); c.commit(); c.close(); return jsonify({'ok':True})

@bp.route('/api/v2/road/search', methods=['POST'])
def road_search():
    if not _uid(): return jsonify({'error':'login_required'}),401
    _check_csrf()
    d=request.get_json(silent=True) or {}
    try: lat=float(d['lat']); lng=float(d['lng'])
    except Exception: return jsonify({'error':'GPS-position mangler.'}),400
    query=str(d.get('query','')).strip()[:500]
    c=_db(); c.execute('INSERT INTO road_searches(user_id,query,lat,lng,created_at) VALUES(?,?,?,?,?)',(_uid(),query,lat,lng,_now())); c.commit(); c.close()
    try: places=_overpass(lat,lng)
    except Exception: places=[]
    low=query.lower()
    wanted=set()
    if any(x in low for x in ['park','raste','hvil']): wanted.add('parking')
    if any(x in low for x in ['diesel','adblue','tank','brændstof']): wanted.add('fuel')
    if any(x in low for x in ['lad','el','charging']): wanted.add('charging')
    if any(x in low for x in ['vask','wash']): wanted.add('wash')
    if any(x in low for x in ['værksted','workshop','reparation']): wanted.add('workshop')
    if wanted: places=[p for p in places if p.get('type') in wanted or p.get('type')=='toilet']
    cards=[x['network'].lower() for x in _cards(_uid())]
    for p in places:
        p['fuel_card_match']=bool(p.get('brand') and any(x in p['brand'].lower() for x in cards))
        # Matching a brand name is only prioritisation, not proof of card acceptance.
        p['fuel_card_verified']=False
    places.sort(key=lambda p:(not p['fuel_card_match'], p['name']))
    return jsonify({'query':query,'places':places,'data_policy':'Live belægning/priser vises kun når en verificeret leverandør leverer dem.','generated_at':_now()})

@bp.route('/api/v2/assistant', methods=['POST'])
def assistant():
    if not _uid(): return jsonify({'error':'login_required'}),401
    _check_csrf()
    d=request.get_json(silent=True) or {}; text=str(d.get('text','')).strip()[:1000]
    low=text.lower()
    if any(x in low for x in ['p0299','fejlkode','turbo','fejl']):
        return jsonify({'intent':'diagnosis','message':'Jeg kan hjælpe med fejlen. Åbn AI-diagnose og vælg lastbilen, så bruger TruckOS lastbilens data i vurderingen.'})
    if any(x in low for x in ['park','raste','hvil','tank','diesel','adblue','lad','vask','værksted','bad','toilet','mad']):
        return jsonify({'intent':'road_search','message':'Jeg søger omkring din GPS-position. Live ledighed og priser vises kun, når TruckOS har verificerede live-data.'})
    key=os.getenv('OPENAI_API_KEY')
    if key:
        try:
            from openai import OpenAI
            client=OpenAI(api_key=key)
            r=client.responses.create(model=os.getenv('OPENAI_MODEL','gpt-5.6-luna'),instructions='Du er TruckOS 2.0. Svar kort på dansk. Opfind aldrig live parkering, priser, åbningstider, booking eller vejhjælpsstatus. Henvis til På vejen for lokationssøgning og AI-diagnose for køretøjsfejl.',input=text)
            return jsonify({'intent':'chat','message':getattr(r,'output_text','').strip() or 'Jeg forstod spørgsmålet, men mangler data.'})
        except Exception: pass
    return jsonify({'intent':'chat','message':'TruckOS AI er klar, men jeg mangler flere oplysninger for at udføre den handling.'})

def register(app):
    app.register_blueprint(bp)
    with app.app_context(): init_v2_db()
