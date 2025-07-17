# main.py

import re
import io
import os
import json
import psycopg2
import html
from datetime import date
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader

app = Flask(__name__)
CORS(app)

# Globale Variable für die Debug-Ansicht
LAST_UPLOADED_DEBUG_HTML = "<h1>Rohdaten-Ansicht</h1><p>Bitte zuerst eine PDF auf der Hauptseite analysieren.</p>"

# ==============================================================================
# === FINALE ANALYSE-FUNKTION: Jetzt mit der korrekten Logik für den Endstand ===
# ==============================================================================
def parse_spielbericht_and_get_raw_lines(pdf_bytes):
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

    debug_tags = {}

    for i, line in enumerate(lines):
        clean_line = line.strip()
        
        try:
            if 'Spiel Nr.' in clean_line and ' am ' in clean_line:
                data["spielklasse"] = clean_line.split(',')[0].strip()
                match_id = re.search(r"Spiel Nr\.\s*([\d\s]+?)\s*am", clean_line)
                if match_id: data["spielId"] = match_id.group(1).strip()
                match_datum = re.search(r"am\s*(\d{2}\.\d{2}\.\d{2})", clean_line)
                if match_datum: data["datum"] = match_datum.group(1).strip()
            
            elif 'Heim:' in clean_line:
                data["teams"]["heim"] = clean_line.split(':', 1)[1].strip()
            elif 'Gast:' in clean_line:
                data["teams"]["gast"] = clean_line.split(':', 1)[1].strip()

            # === KORRIGIERTE LOGIK FÜR ENDSTAND ===
            # Wenn "Endstand" in der Zeile ist...
            elif "Endstand" in clean_line:
                # ...ist der Wert in derselben Zeile.
                ergebnis_part = clean_line
                debug_tags[i] = "Schlüsselwort 'Endstand' und Wert gefunden"
                
                # Extrahiere Endstand, Halbzeit und Sieger aus dieser Zeile
                match_endstand = re.search(r'(\d+:\d+)', ergebnis_part)
                if match_endstand:
                    data["ergebnis"]["endstand"] = match_endstand.group(1)

                match_halbzeit = re.search(r'\((\d+:\d+)\)', ergebnis_part)
                if match_halbzeit:
                    data["ergebnis"]["halbzeit"] = match_halbzeit.group(1)

                match_sieger = re.search(r"Sieger\s*(.*)", ergebnis_part)
                if match_sieger:
                    # Wir nehmen alles nach "Sieger" und entfernen den Rest
                    sieger_text = match_sieger.group(1)
                    end_of_sieger = sieger_text.find("Zuschauer:")
                    if end_of_sieger != -1:
                        sieger_text = sieger_text[:end_of_sieger]
                    data["ergebnis"]["sieger"] = sieger_text.strip()

        except Exception as e:
            print(f"Kleiner Fehler bei der Analyse der Zeile '{clean_line}': {e}")
            continue

    # Spielverlauf-Extraktion
    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0], "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None, "aktion": ereignis[3].strip()
            })
            
    return data, lines, debug_tags

# ==============================================================================
# === AB HIER BLEIBT DER CODE UNVERÄNDERT ===
# ==============================================================================

@app.route('/upload', methods=['POST'])
def analyze_pdf_for_verification():
    global LAST_UPLOADED_DEBUG_HTML
    if 'file' not in request.files: return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data, raw_lines, debug_tags = parse_spielbericht_and_get_raw_lines(pdf_bytes)
        debug_html = """
        <style>
            body { font-family: monospace, sans-serif; margin: 2em; line-height: 1.6; }
            h1 { font-family: sans-serif; } ol { border: 1px solid #ccc; padding: 1em 1em 1em 4em; background-color: #f9f9f9; }
            li { margin-bottom: 5px; } pre { margin: 0; display: inline; }
            .highlight { background-color: #d4edda; color: #155724; padding: 2px 4px; border-radius: 3px; }
        </style>
        <h1>Zeilenweise Rohdaten mit Analyse-Markierung</h1><ol>
        """
        for i, line in enumerate(raw_lines):
            escaped_line = html.escape(line)
            tag_html = ""
            if i in debug_tags:
                tag_html = f' <span class="highlight"> &lt;-- {debug_tags[i]}</span>'
            debug_html += f"<li><pre>{escaped_line}</pre>{tag_html}</li>"
        debug_html += "</ol>"
        LAST_UPLOADED_DEBUG_HTML = debug_html
        if parsed_data is None or not parsed_data.get("spielId"): 
            return jsonify({"error": "Analyse fehlgeschlagen. Überprüfe die PDF-Struktur."}), 400
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e: return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

@app.route('/debug')
def debug_pdf_page():
    return LAST_UPLOADED_DEBUG_HTML

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
        html = """
        <style> body { font-family: sans-serif; margin: 2em; } table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #ddd; padding: 8px; text-align: left; } th { background-color: #f2f2f2; } </style>
        <h1>Gespeicherte Spielberichte</h1>
        """
        if not berichte:
            html += "<p>Noch keine Daten in der Datenbank gefunden.</p>"
            return html, 200
        html += "<table><tr><th>Spiel ID</th><th>Datum</th><th>Ergebnis</th><th>Halbzeit</th><th>Heim</th><th>Gast</th></tr>"
        for bericht in berichte:
            spiel_id, datum_val, ergebnis_json, teams_json = bericht
            datum_str = datum_val.strftime('%d.%m.%Y') if isinstance(datum_val, date) else 'N/A'
            ergebnis_str = ergebnis_json.get('endstand', 'N/A') if ergebnis_json else 'N/A'
            halbzeit_str = ergebnis_json.get('halbzeit', 'N/A') if ergebnis_json else 'N/A'
            heim_str = teams_json.get('heim', 'N/A') if teams_json else 'N/A'
            gast_str = teams_json.get('gast', 'N/A') if teams_json else 'N/A'
            html += f"<tr><td>{spiel_id}</td><td>{datum_str}</td><td>{ergebnis_str}</td><td>{halbzeit_str}</td><td>{heim_str}</td><td>{gast_str}</td></tr>"
        html += "</table>"
        return html, 200
    except Exception as e: return f"<h1>Fehler</h1><p>{e}</p>", 500
    finally:
        if conn: cur.close(); conn.close()

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
