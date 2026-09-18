#!/bin/bash
# Doppelklick: fragt den Notion-Schlüssel in einem Fenster ab und schreibt ihn in die Datei .env.
# Die .env bleibt nur auf diesem Mac (steht in .gitignore) und geht nie online.
cd "$(dirname "$0")/.." || exit 1
TOKEN=$(osascript -e 'display dialog "Notion-Schlüssel (Internal Integration Secret) hier einfügen:" default answer "" with hidden answer with title "Website Sync" buttons {"Abbrechen","Speichern"} default button "Speichern"' -e 'text returned of result' 2>/dev/null)
TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
if [ -z "$TOKEN" ]; then echo "Abgebrochen, nichts geändert."; exit 1; fi
printf 'NOTION_TOKEN=%s\nNOTION_DATABASE_ID=11d2734b5d084ea98772df588fde5e21\n' "$TOKEN" > .env
echo "Schlüssel gespeichert (Datei .env, nur lokal)."
echo "Teste die Verbindung zu Notion ..."
echo
python3 scripts/sync_notion.py --check-only --no-notion-write
echo
echo "Dieses Fenster kann geschlossen werden."
