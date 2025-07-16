# main.py

import re
import io
import os
import json
import psycopg2
from datetime import date
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader

app = Flask(__name__)
CORS(app)

# Die parse_spielbericht Funktion bleibt unverändert
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

def ensure_db_table_exists(cur):
    """Stellt sicher, dass die Zieltabelle in der DB existiert."""
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
    except Exception as e:
        return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return jsonify({"error": "Datenbank-Konfiguration auf dem Server fehlt."}), 500
            
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        ensure_db_table_exists(cur)
        
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
        
        data_tuple = (
            parsed_data['spielId'],
            parsed_data['datum'],
            json.dumps(parsed_data['teams']),
            json.dumps(parsed_data['ergebnis']),
            json.dumps(parsed_data['spieler']),
            json.dumps(parsed_data['spielverlauf'])
        )
        
        cur.execute(insert_query, data_tuple)
        conn.commit()
        
        return jsonify({
            "success": True, 
            "message": f"Spielbericht {parsed_data['spielId']} erfolgreich in DB gespeichert.",
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Datenbank-Fehler: {str(e)}"}), 500
    finally:
        if conn:
            cur.close()
            conn.close()

# NEUE FUNKTION: Daten aus der Datenbank anzeigen
@app.route('/view-data')
def view_data():
    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return "<h1>Fehler</h1><p>Datenbank-Konfiguration fehlt.</p>", 500
            
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Sicherstellen, dass die Tabelle existiert, bevor wir sie abfragen
        ensure_db_table_exists(cur)
        
        # Daten aus der Datenbank abfragen, neueste zuerst
        cur.execute("SELECT spiel_id, datum, ergebnis, teams FROM spielberichte ORDER BY erstellt_am DESC;")
        berichte = cur.fetchall()
        
        # Eine einfache HTML-Seite als Antwort erstellen
        html = """
        <style>
            body { font-family: sans-serif; margin: 2em; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
        <h1>Gespeicherte Spielberichte</h1>
        """
        
        if not berichte:
            html += "<p>Noch keine Daten in der Datenbank gefunden.</p>"
            return html, 200

        html += "<table><tr><th>Spiel ID</th><th>Datum</th><th>Ergebnis</th><th>Heim</th><th>Gast</th></tr>"
        
        for bericht in berichte:
            spiel_id, datum_val, ergebnis_json, teams_json = bericht
            
            datum_str = datum_val.strftime('%d.%m.%Y') if isinstance(datum_val, date) else 'N/A'
            ergebnis_str = ergebnis_json.get('endstand', 'N/A') if ergebnis_json else 'N/A'
            heim_str = teams_json.get('heim', 'N/A') if teams_json else 'N/A'
            gast_str = teams_json.get('gast', 'N/A') if teams_json else 'N/A'
            
            html += f"<tr><td>{spiel_id}</td><td>{datum_str}</td><td>{ergebnis_str}</td><td>{heim_str}</td><td>{gast_str}</td></tr>"
            
        html += "</table>"
        
        return html, 200

    except Exception as e:
        return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>", 500
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == '__main__':
    app.run(port=5001)
