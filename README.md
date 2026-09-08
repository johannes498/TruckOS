# TruckOS v0.6

TruckOS v0.6 samler den eksisterende v0.4 og bygger videre uden at slette eksisterende PostgreSQL-data.

## Nyt i v0.6
- Enklere dashboard med store knapper
- Udvidede lastbilprofiler: mærke, model, årgang, VIN og motor
- Servicepåmindelser med dato og/eller kilometer
- Bedre AI-diagnosevisning
- Abonnements-side med Stripe Checkout-integration
- Stripe webhook + kundeportal-fundament
- PWA/manifest/service worker + app-ikoner
- SEO meta-tags
- `/health` viser også version 0.6

## Vigtigt om betaling
Koden er klar til Stripe, men rigtige betalinger starter først når disse miljøvariabler er sat på Render:
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_DRIVER`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_FLEET`.

Start med Stripe test mode, og gå først live når checkout, webhook, opsigelse og adgang er testet.

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
