# TruckOS 2.0

Komplet samlet build af TruckOS 1.0-funktionerne + TruckOS 2.0-funktionerne.

## Indeholder
- Konto/login og fælles backend/database til telefon og PC.
- Mine lastbiler med køretøjsdata og servicekontrakt.
- Servicehistorik og påmindelser.
- TruckOS AI-fejldiagnose med lokal fallback og OpenAI-integration.
- Reparationsflow: diagnose → løsning → reservedel → værksted → vejhjælp.
- Falck + SOS Dansk Autohjælp som vejhjælpsvalg. Uden partner-API gemmes kun kladde.
- Stripe-planer: Chauffør 79 kr., Vognmand Pro 199 kr., Flåde 499 kr. Stripe skal fortsat være test, indtil LIVE-opsætning er godkendt.
- PWA-installation til iPhone/Android samt web/PC.
- Ny **På vejen**: kort, GPS, mikrofon/stemmestyring, AI-intent, truckparkering, brændstof, ladning, truckvask, værksteder og navigation.
- Mine brændstofkort med prioritering af stationsmærke.
- OpenStreetMap/Overpass som baseline POI-kilde. Denne kilde er **ikke** live belægnings- eller prisdata.
- Klargjorte miljøvariabler til partner-API'er for live parkering, fuel-priser, opladning, værksteder, reservedele og vejhjælp.

## Datapolitik
TruckOS må aldrig opfinde antal ledige truckpladser, priser, kortaccept, lagerstatus, værkstedstider eller vejhjælpsstatus. Mangler en verificeret datakilde, vises data som utilgængelige/ikke-verificerede.

## Render
Build command: `pip install -r requirements.txt`
Start command: `gunicorn app:app`
Health check: `/health`

## Lokal test
1. `python -m venv .venv`
2. Aktivér miljøet.
3. `pip install -r requirements.txt`
4. `python app.py`
5. Åbn `http://127.0.0.1:5000`

## Vigtigt før deployment
Tag backup af den nuværende GitHub-version først. Upload ikke en gammel lokal `truckos.db` oven på produktionsdata. På Render bruges PostgreSQL via `DATABASE_URL`.
