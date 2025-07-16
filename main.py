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

# ==============================================================================
# === NEU VERBESSERTE DATEN-INTERPRETATION ===
# ==============================================================================
def parse_spielbericht(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    # Wir nehmen nur die ersten beiden Seiten, um die Analyse zu beschleunigen
    num_pages_to_read = min(2, len(reader.pages))
    for i in range(num_pages_to_read):
        full_text += reader.pages[i].extract_text() + "\n"

    # Wir ersetzen mehrere Leerzeichen/Zeilenumbrüche durch ein einzelnes Leerzeichen
    # Diesmal aber vorsichtiger, um keine Wörter zusammenzukleben.
    clean_text = re.sub(r'\s+', ' ', full_text)

    data = {
        "spielId": None,
        "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spieler": {"heim": [], "gast": []},
        "spielverlauf": []
    }

    # --- NOCH ROBUSTERE REGEX-MUSTER ---
    # \s* erlaubt beliebig viele (auch keine) Leerzeichen zwischen den Wörtern
    match = re.search(r"Spiel\s*Nr\s*\.\s*(\d+)", clean_text)
    if match:
        data["spielId"] = match.group(1)
    
    match = re.search(r"am\s*(\d{2}\.\d{2}\.\d{2})", clean_text)
    if match:
        data["datum"] = match.group(1)

    match = re.search(r"Heim\s*Gast\s*,\s*\"(.*?)\s*-\s*(.*?)\"", full_text.replace('\n', ''))
    if match:
        data["teams"]["heim"] = match.group(1).strip()
        data["teams"]["gast"] = match.group(2).strip()

    match = re.search(r"Endstand\s*,\s*\"(\d+:\d+)\s*\((\d+:\d+)\)\s*,\s*Sieger\s*(.*?)\"", full_text.replace('\n', ''))
    if match:
        data["ergebnis"]["endstand"] = match.group(1).strip()
        data["ergebnis"]["halbzeit"] = match.group(2).strip()
        data["ergebnis"]["sieger"] = match.group(3).strip()
    
    # Der Rest bleibt wie gehabt...
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
            
    return data, full_text, clean_text # Wir geben jetzt auch die Texte für das Debugging zurück
# ==============================================================================
# === AB HIER BLEIBT DER CODE UNVERÄNDERT (bis auf die neue Debug-Route) ===
# ==============================================================================

def ensure_db_table_exists(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spielberichte (
            id SERIAL PRIMARY KEY, spiel_id VARCHAR(50) UNIQUE NOT NULL, datum DATE,
            teams JSONB, ergebnis JSONB, spieler JSONB, spielverlauf JSONB,
            erstellt_am TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)

@app.route('/upload', methods=['POST'])
def upload_spielbericht():
    if 'file' not in request.files: return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data, _, _ = parse_spielbericht(pdf_bytes)
        if not parsed_data.get("spielId"): return jsonify({"error": "Konnte keine Spiel-ID extrahieren."}), 400
    except Exception as e: return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500
    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url: return jsonify({"error": "DB-Konfiguration fehlt."}), 500
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        ensure_db_table_exists(cur)
        insert_query = """
            INSERT INTO spielberichte (spiel_id, datum, teams, ergebnis, spieler, spielverlauf)
            VALUES (%s, TO_DATE(%s, 'DD.MM.YY'), %s, %s, %s, %s)
            ON CONFLICT (spiel_id) DO UPDATE SET
                datum = EXCLUDED.datum, teams = EXCLUDED.teams, ergebnis = EXCLUDED.ergebnis,
                spieler = EXCLUDED.spieler, spielverlauf = EXCLUDED.spielverlauf;
        """
        data_tuple = (
            parsed_data['spielId'], parsed_data['datum'], json.dumps(parsed_data['teams']),
            json.dumps(parsed_data['ergebnis']), json.dumps(parsed_data['spieler']),
            json.dumps(parsed_data['spielverlauf'])
        )
        cur.execute(insert_query, data_tuple)
        conn.commit()
        return jsonify({"success": True, "message": f"Spielbericht {parsed_data['spielId']} gespeichert."}), 200
    except Exception as e: return jsonify({"error": f"DB-Fehler: {str(e)}"}), 500
    finally:
        if conn: cur.close(); conn.close()

@app.route('/view-data')
def view_data():
    # Diese Funktion bleibt unverändert
    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        ensure_db_table_exists(cur)
        cur.execute("SELECT spiel_id, datum, ergebnis, teams FROM spielberichte ORDER BY erstellt_am DESC;")
        berichte = cur.fetchall()
        html = "..." # HTML-Generierung wie zuvor
        return html, 200
    except Exception as e: return f"<h1>Fehler</h1><p>{e}</p>", 500
    finally:
        if conn: cur.close(); conn.close()

# ==============================================================================
# === NEUE DEBUG-SEITE ===
# ==============================================================================
@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            # Wir rufen unsere Analyse-Funktion auf und bekommen alles zurück
            parsed_data, raw_text, clean_text = parse_spielbericht(pdf_bytes)
            
            # Wir bauen eine HTML-Antwort, die uns alles anzeigt
            html_response = f"""
            <style>
                body {{ font-family: sans-serif; margin: 2em; }}
                h2 {{ border-bottom: 2px solid #ccc; padding-bottom: 5px; }}
                pre {{ background-color: #f4f4f4; padding: 1em; white-space: pre-wrap; word-wrap: break-word; border: 1px solid #ddd; }}
            </style>
            <h1>PDF Debug-Ansicht</h1>
            
            <h2>1. Extrahierte Daten (JSON)</h2>
            <pre>{json.dumps(parsed_data, indent=2, ensure_ascii=False)}</pre>
            
            <h2>2. Bereinigter Text (wird für die Analyse verwendet)</h2>
            <pre>{clean_text}</pre>
            
            <h2>3. Roher Text (direkt aus der PDF)</h2>
            <pre>{raw_text}</pre>
            """
            return html_response
        except Exception as e:
            return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>"

    # Wenn die Seite normal aufgerufen wird (GET), zeigen wir das Upload-Formular an
    return '''
        <!doctype html>
        <title>PDF Debug Uploader</title>
        <h1>Lade eine PDF zur Analyse hoch</h1>
        <form method=post enctype=multipart/form-data>
          <input type=file name=file>
          <input type=submit value=Analysieren>
        </form>
    '''

if __name__ == '__main__':
    app.run(port=5001)

