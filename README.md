# Synergy Cockpit

Zielerreichung Kevin & Robin auf einer Seite.

## Lokal starten

```
pip install -r requirements.txt
streamlit run app.py
```

Credentials liegen in `~/.tracker.env` (lokaler Fallback) oder als Streamlit Secrets.

## Deploy

1. Repo auf GitHub pushen
2. share.streamlit.io  ->  New app, dieses Repo waehlen
3. Secrets unter "Advanced settings" eintragen:
   ```
   AIRCALL_API_ID = "..."
   AIRCALL_API_TOKEN = "..."
   RECRUITCRM_API_TOKEN = "..."
   ```
4. Login-Schutz: in den Streamlit Cloud Settings -> "Viewers" beschraenken (z.B. nur eingeladene Google-Accounts)
