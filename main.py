# main.py

import re
import io
import os
import json
import psycopg2
import html # For escaping text in debug view
from datetime import date
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader

app = Flask(__name__)
CORS(app)

# ==============================================================================
# === ANALYSE-FUNKTION (ROBUST) FÜR DEN NORMALEN WORKFLOW ===
# ==============================================================================
def parse_spielbericht(pdf_bytes):
    """
    Liest eine PDF und extrahiert die Daten durch eine robuste,
    zeilenweise Suche nach Schlüsselwörtern.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
    
    lines = full_text.splitlines()
    
    data = {
        "spielId": None, "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spielklasse": "Unbekannt", "spielverlauf": []
    }

    for line in lines:
        clean_line = line.strip()
        try:
            if 'Spiel Nr.' in clean_line and ' am ' in clean_line:
                data["spielklasse"] = clean_line.split(',')[0].strip()
                match_id = re.search(r"Spiel Nr\.\s*([\d\s]+?)\s*am", clean_line)
                if match_id: data["spielId"] = match_id.group(1).strip()
                match_datum = re.search(r"am\s*(\d{2}\.\d{2}\.\d{2})", clean_line)
                if match_datum: data["datum"] = match_datum.group(1).strip()
            elif clean_line.startswith('"Heim Gast'):
                teams_part = clean_line.split('","')[1]
                teams = teams_part.split(' - ')
                if len(teams) == 2:
                    data["teams"]["heim"] = teams[0].strip()
                    data["teams"]["gast"] = teams[1].strip()
            elif clean_line.startswith('"Endstand'):
                ergebnis_part = clean_line.split('","')[1]
                match_ergebnis = re.search(r'(\d+:\d+)\s*\((\d+:\d+)\)', ergebnis_part)
                if match_ergebnis:
                    data["ergebnis"]["endstand"] = match_ergebnis.group(1)
                    data["ergebnis"]["halbzeit"] = match_ergebnis.group(2)
                match_sieger = re.search(r"Sieger\s*(.*)", ergebnis_part)
                if match_sieger: data["ergebnis"]["sieger"] = match_sieger.group(1).replace('",', '').strip()
        except Exception:
            continue

    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0], "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None, "aktion": ereignis[3].strip()
            })
            
    return data

# ==============================================================================
# === ROUTEN FÜR DEN NORMALEN WORKFLOW (ANALYSE & SPEICHERN) ===
# ==============================================================================

@app.route('/upload', methods=['POST'])
def analyze_pdf_for_verification():
    if 'file' not in request.files: return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data = parse_spielbericht(pdf_bytes)
        if parsed_data is None or not parsed_data.get("spielId"): 
            return jsonify({"error": "Analyse fehlgeschlagen. Die PDF-Struktur scheint unbekannt zu sein."}), 400
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e: return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

@app.route('/save-data', methods=['POST'])
def save_verified_data():
    verified_data = request.get_json()
    if not verified_data: return jsonify({"error": "Keine Daten zum Speichern erhalten."}), 400
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
            verified_data['spielId'], verified_data['datum'], json.dumps(verified_data['teams']),
            json.dumps(verified_data['ergebnis']), json.dumps(verified_data.get('spieler', {})),
            json.dumps(verified_data.get('spielverlauf', []))
        )
        cur.execute(insert_query, data_tuple)
        conn.commit()
        return jsonify({"success": True, "message": f"Spielbericht {verified_data['spielId']} erfolgreich gespeichert."}), 200
    except Exception as e: return jsonify({"error": f"DB-Fehler: {str(e)}"}), 500
    finally:
        if conn: cur.close(); conn.close()

@app.route('/view-data')
def view_data():
    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        ensure_db_table_exists(cur)
        cur.execute("SELECT spiel_id, datum, ergebnis, teams FROM spielberichte ORDER BY erstellt_am DESC;")
        berichte = cur.fetchall()
        html_content = "..." # Gekürzt zur Übersichtlichkeit
        return html_content, 200
    except Exception as e: return f"<h1>Fehler</h1><p>{e}</p>", 500
    finally:
        if conn: cur.close(); conn.close()

# ==============================================================================
# === NEUE DEBUG-SEITE FÜR ROHDATEN-ANALYSE ===
# ==============================================================================
@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf_page():
    if request.method == 'POST':
        if 'file' not in request.files: return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            reader = PdfReader(io.BytesIO(pdf_bytes))
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text() + "\n"
            lines = full_text.splitlines()
            
            html_response = """
            <style>
                body { font-family: monospace, sans-serif; margin: 2em; line-height: 1.6; }
                h1, p, ul, li { font-family: sans-serif; }
                ol { border: 1px solid #ccc; padding: 1em 1em 1em 4em; background-color: #f9f9f9; }
                li { margin-bottom: 5px; }
                pre { margin: 0; display: inline; }
            </style>
            <h1>Zeilenweise Rohdaten der PDF</h1>
            <p>Hier kannst du die Rohdaten analysieren, um die Regeln für die Spielerdaten zu finden.</p>
            <ol>
            """
            for line in lines:
                escaped_line = html.escape(line)
                html_response += f"<li><pre>{escaped_line}</pre></li>"
            html_response += "</ol>"
            return html_response
        except Exception as e: return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>"

    return '''
        <!doctype html>
        <title>PDF Debug Uploader</title>
        <style>body { font-family: sans-serif; margin: 2em; }</style>
        <h1>Lade eine PDF zur Rohdaten-Analyse hoch</h1>
        <form method=post enctype=multipart/form-data>
          <input type=file name=file>
          <input type=submit value=Analysieren>
        </form>
    '''

def ensure_db_table_exists(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spielberichte (
            id SERIAL PRIMARY KEY, spiel_id VARCHAR(50) UNIQUE NOT NULL, datum DATE,
            teams JSONB, ergebnis JSONB, spieler JSONB, spielverlauf JSONB,
            erstellt_am TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)

if __name__ == '__main__':
    app.run(port=5001)
