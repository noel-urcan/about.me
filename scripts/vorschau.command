#!/bin/bash
# Doppelklick: erzeugt die Website lokal aus Notion und öffnet sie im Browser. Nichts geht online.
cd "$(dirname "$0")/.." || exit 1
echo "Lese Notion und erzeuge die Seiten lokal ..."
python3 scripts/sync_notion.py --no-notion-write && open index.html
echo
echo "Fertig. Dieses Fenster kann geschlossen werden."
