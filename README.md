# TruckOS 1.0

TruckOS 1.0 samler lastbilprofil, AI-fejlvurdering, reparationssager, reservedelsflow, værkstedsbooking, vejhjælp, service, påmindelser og Stripe-abonnementer.

## Vigtigt om live data
TruckOS opfinder ikke live lagerstatus, priser eller ledige værkstedstider. Uden en rigtig partnerintegration gemmes reservedels-, booking- og vejhjælpsdata som kladder/ikke-verificerede oplysninger. Når partner-API'er foreligger, kan de kobles på via integrationlaget.

## Miljøvariabler
Eksisterende: `SECRET_KEY`, `DATABASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_DRIVER`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_FLEET`.

Nye integrationer: `PARTS_API_URL`, `PARTS_API_KEY`, `WORKSHOP_API_URL`, `WORKSHOP_API_KEY`, `ROADSIDE_API_URL`, `ROADSIDE_API_KEY`.

## Datamigrering
1.0-opgraderingen er additiv: eksisterende brugere, lastbiler, servicehistorik, diagnoser, påmindelser og abonnementer slettes ikke. Nye tabeller oprettes automatisk ved opstart.

## Stripe
Webhook-endpoint er `/stripe/webhook`. 1.0 håndterer checkout, subscription created/updated/deleted samt invoice paid/payment failed/action required.

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
