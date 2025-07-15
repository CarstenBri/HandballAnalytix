# main.py

import re
import io
import os
import json
import psycopg2 # Neue Bibliothek für die PostgreSQL-Datenbank
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader

# Erstelle eine Flask-Webanwendung
app = Flask(__name__)
CORS(app)

# Die parse_spielbericht Funktion bleibt genau gleich
def parse_spielbericht(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"

    data = {
        "spielId": None,
        "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spieler": {"heim": [], "gast": []},
        "spielverlauf": []
    }

    match = re.search(r"Spiel Nr\. (\d+)", full_text)
    if match:
        data["spielId"] = match.group(1)
    
    match = re.search(r"am (\d{2}\.\d{2}\.\d{2})", full_text)
    if match:
        data["datum"] = match.group(1)

    match = re.search(r"Heim Gast\s*\n\s*(.*?) - (.*?)\n", full_text)
    if match:
        data["teams"]["heim"] = match.group(1).strip()
        data["teams"]["gast"] = match.group(2).strip()

    match = re.search(r"Endstand\s*\n\s*(\d+:\d+) \((\d+:\d+)\), Sieger (.*?)\n", full_text)
    if match:
        data["ergebnis"]["endstand"] = match.group(1)
        data["ergebnis"]["halbzeit"] = match.group(2)
        data["ergebnis"]["sieger"] = match.group(3).strip()

    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0],
                "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None,
                "aktion": ereignis[3].strip()
            })
    return data

def init_db():
    """Erstellt die Datenbank-Tabelle, falls sie noch nicht existiert."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("DATABASE_URL ist nicht gesetzt. Kann DB nicht initialisieren.")
        return
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    # Wir verwenden JSONB für flexible JSON-Daten
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spielberichte (
            id SERIAL PRIMARY KEY,
            spiel_id VARCHAR(50) UNIQUE NOT NULL,
            datum DATE,
            teams JSONB,
            ergebnis JSONB,
            spieler JSONB,
            spielverlauf JSONB,
            erstellt_am TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

@app.route('/upload', methods=['POST'])
def upload_spielbericht():
    if 'file' not in request.files:
        return jsonify({"error": "Keine Datei gefunden"}), 400

    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data = parse_spielbericht(pdf_bytes)

        if not parsed_data.get("spielId"):
            return jsonify({"error": "Konnte keine Spiel-ID extrahieren."}), 400

        # --- NEU: Daten in die Datenbank speichern ---
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return jsonify({"error": "Datenbank-Konfiguration auf dem Server fehlt."}), 500
            
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # SQL-Befehl zum Einfügen oder Aktualisieren von Daten
        # ON CONFLICT sorgt dafür, dass ein bereits vorhandener Spielbericht aktualisiert wird
        insert_query = """
            INSERT INTO spielberichte (spiel_id, datum, teams, ergebnis, spieler, spielverlauf)
            VALUES (%s, TO_DATE(%s, 'DD.MM.YY'), %s, %s, %s, %s)
            ON CONFLICT (spiel_id) DO UPDATE SET
                datum = EXCLUDED.datum,
                teams = EXCLUDED.teams,
                ergebnis = EXCLUDED.ergebnis,
                spieler = EXCLUDED.spieler,
                spielverlauf = EXCLUDED.spielverlauf;
        """
        
        # Daten für die Abfrage vorbereiten
        # Wir konvertieren die Python-Dicts mit json.dumps in JSON-Strings
        data_tuple = (
            parsed_data['spielId'],
            parsed_data['datum'],
            json.dumps(parsed_data['teams']),
            json.dumps(parsed_data['ergebnis']),
            json.dumps(parsed_data['spieler']),
            json.dumps(parsed_data['spielverlauf'])
        )
        
        cur.execute(insert_query, data_tuple)
        
        conn.commit() # Änderungen speichern
        cur.close()
        conn.close()
        # --- Ende des neuen Datenbank-Teils ---

        return jsonify({
            "success": True, 
            "message": f"Spielbericht {parsed_data['spielId']} erfolgreich in DB gespeichert.",
            "data": parsed_data
        }), 200
    except Exception as e:
        # Gibt eine detailliertere Fehlermeldung für die Fehlersuche zurück
        return jsonify({"error": f"Verarbeitung fehlgeschlagen: {str(e)}"}), 500

# Initialisiere die Datenbank beim Start der Anwendung
init_db()

if __name__ == '__main__':
    app.run(port=5001)
