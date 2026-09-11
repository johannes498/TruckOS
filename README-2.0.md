# TruckOS 2.0

Denne build er lavet oven på TruckOS 1.0, så eksisterende design, logo, login, lastbiler, service, diagnoser, reparationsflow, værksteder, vejhjælp og Stripe-fundament bevares.

## Nyt i 2.0
- På vejen med Leaflet/OpenStreetMap-basiskort
- GPS/geolocation med brugerens tilladelse
- Mikrofon/stemmegenkendelse i browsere der understøtter Web Speech API
- TruckOS AI-kommandoer til vej-søgning og henvisning til eksisterende AI-diagnose
- Truckparkering, brændstof, opladning, truckvask og værksteder fra verificerbare POI-data
- Mine brændstofkort med prioritering efter mærkematch; accept påstås ikke som verificeret uden partnerdata
- Serviceaftale-felter på lastbilprofil
- Falck + SOS Dansk Autohjælp som vejhjælpsvalg
- Ingen opdigtede live-pladser, priser, bookingtider eller vejhjælpsstatus

## Kræver ekstern aftale/API før live-funktion
Live parkeringsbelægning, aktuelle diesel/AdBlue/el-priser, verificeret brændstofkortaccept, direkte værkstedsbooking, reservedelslager/pris og direkte elektronisk Falck/SOS-afsendelse.

## Vigtigt
Stripe skal forblive i testtilstand indtil virksomhed/bank/Stripe LIVE er korrekt sat op. Test denne branch før merge til main.
