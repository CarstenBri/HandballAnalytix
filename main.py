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

# Globale Variablen für die Debug-Ansichten
LAST_UPLOADED_SUMMARY_HTML = "<h1>Analyse-Ergebnis</h1><p>Bitte zuerst eine PDF auf der Hauptseite analysieren.</p>"
LAST_UPLOADED_RAW_HTML = "<h1>Rohdaten-Ansicht</h1><p>Bitte zuerst eine PDF auf der Hauptseite analysieren.</p>"

# ==============================================================================
# === ANALYSE-FUNKTION: Jetzt mit der Logik für die Spielerlisten ===
# ==============================================================================
def parse_player_line(line):
    """Versucht, eine einzelne Zeile als Spielerdaten zu interpretieren."""
    # Ein Spieler-Zeile beginnt typischerweise mit einer Nummer in Anführungszeichen, z.B. "2 "
    if not (line.strip().startswith('"') and line.strip()[1].isdigit()):
        return None

    try:
        # Wir teilen die Zeile am Trennzeichen "," auf, um die Teile zu bekommen
        parts = line.split('","')
        nummer = parts[0].strip(' "')
        name = parts[1].strip()
        
        # Für den Anfang nehmen wir nur Nummer und Name
        return {"nummer": nummer, "name": name}
    except IndexError:
        # Wenn die Zeile nicht das erwartete Format hat, ist es kein Spieler
        return None

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
        "spielklasse": "Unbekannt", 
        "spieler": {"heim": [], "gast": []}, # Initialisiere die Spielerlisten
        "spielverlauf": []
    }

    # Zustandsvariablen für die Analyse
    current_team_context = None # Kann 'heim' oder 'gast' sein
    is_parsing_players = False  # Wird true, nachdem wir "Strafe" gefunden haben

    for line in lines:
        clean_line = line.strip()
        
        # Kontext für Heim/Gast/Spielverlauf setzen
        if clean_line.startswith('Heim:'):
            current_team_context = 'heim'
            is_parsing_players = False # Setze zurück, bis "Strafe" gefunden wird
        elif clean_line.startswith('Gast:'):
            current_team_context = 'gast'
            is_parsing_players = False
        elif clean_line.startswith('Spielverlauf'):
            current_team_context = None # Spielerlisten sind vorbei
            is_parsing_players = False

        # Wenn wir im Kontext eines Teams sind und "Strafe" finden, fangen wir an, Spieler zu suchen
        if current_team_context and "Strafe" in clean_line:
            is_parsing_players = True
            continue # Wir überspringen die "Strafe"-Zeile selbst

        # Wenn wir im Spieler-Analyse-Modus sind...
        if is_parsing_players:
            player_data = parse_player_line(clean_line)
            if player_data:
                # ...fügen wir den gefundenen Spieler zum richtigen Team hinzu.
                data["spieler"][current_team_context].append(player_data)

        # Allgemeine Daten wie gehabt extrahieren
        try:
            if 'Spiel Nr.' in clean_line and ' am ' in clean_line:
                data["spielklasse"] = clean_line.split(',')[0].strip()
                match_id = re.search(r"Spiel Nr\.\s*([\d\s]+?)\s*am", clean_line)
                if match_id: data["spielId"] = match_id.group(1).strip()
                match_datum = re.search(r"am\s*(\d{2}\.\d{2}\.\d{2})", clean_line)
                if match_datum: data["datum"] = match_datum.group(1).strip()
            
            elif "Endstand" in clean_line:
                ergebnis_part = clean_line
                match_endstand = re.search(r'(\d+:\d+)', ergebnis_part)
                if match_endstand: data["ergebnis"]["endstand"] = match_endstand.group(1)
                match_halbzeit = re.search(r'\((\d+:\d+)\)', ergebnis_part)
                if match_halbzeit: data["ergebnis"]["halbzeit"] = match_halbzeit.group(1)
                match_sieger = re.search(r"Sieger\s*(.*)", ergebnis_part)
                if match_sieger:
                    sieger_text = match_sieger.group(1)
                    end_of_sieger = sieger_text.find("Zuschauer:")
                    if end_of_sieger != -1: sieger_text = sieger_text[:end_of_sieger]
                    data["ergebnis"]["sieger"] = sieger_text.strip()
        except Exception as e:
            print(f"Kleiner Fehler bei der Analyse der Zeile '{clean_line}': {e}")
            continue
            
    return data, lines

# ==============================================================================
# === ROUTEN: /upload wird angepasst, um die Spielerdaten anzuzeigen ===
# ==============================================================================

@app.route('/upload', methods=['POST'])
def analyze_pdf_for_verification():
    global LAST_UPLOADED_SUMMARY_HTML, LAST_UPLOADED_RAW_HTML

    if 'file' not in request.files: return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data, raw_lines = parse_spielbericht_and_get_raw_lines(pdf_bytes)

        # === Erstelle die Zusammenfassungs-Liste (jetzt mit Spielern) ===
        heim_spieler_html = "".join([f"<li>{p['nummer']} - {html.escape(p['name'])}</li>" for p in parsed_data['spieler']['heim']])
        gast_spieler_html = "".join([f"<li>{p['nummer']} - {html.escape(p['name'])}</li>" for p in parsed_data['spieler']['gast']])

        summary_html = f"""
        <style>
            body {{ font-family: sans-serif; margin: 2em; }}
            .container {{ display: flex; gap: 2em; }}
            .column {{ flex: 1; }}
            h2 {{ border-bottom: 2px solid #eee; padding-bottom: 10px; }}
            ul {{ list-style-type: none; padding: 0; }}
            li {{ background-color: #f9f9f9; padding: 8px 12px; border-bottom: 1px solid #eee; }}
            li b {{ color: #333; }}
        </style>
        <div class="container">
            <div class="column">
                <h2>Gefundene Werte</h2>
                <ul>
                    <li><b>Spielklasse:</b> {html.escape(str(parsed_data.get('spielklasse')))}</li>
                    <li><b>Spiel ID:</b> {html.escape(str(parsed_data.get('spielId')))}</li>
                    <li><b>Datum:</b> {html.escape(str(parsed_data.get('datum')))}</li>
                    <li><b>Heim:</b> {html.escape(str(parsed_data.get('teams', {{}}).get('heim')))}</li>
                    <li><b>Gast:</b> {html.escape(str(parsed_data.get('teams', {{}}).get('gast')))}</li>
                    <li><b>Endstand:</b> {html.escape(str(parsed_data.get('ergebnis', {{}}).get('endstand')))}</li>
                    <li><b>Halbzeit:</b> {html.escape(str(parsed_data.get('ergebnis', {{}}).get('halbzeit')))}</li>
                    <li><b>Sieger:</b> {html.escape(str(parsed_data.get('ergebnis', {{}}).get('sieger')))}</li>
                </ul>
            </div>
            <div class="column">
                <h2>Heim-Spieler</h2>
                <ul>{heim_spieler_html}</ul>
            </div>
            <div class="column">
                <h2>Gast-Spieler</h2>
                <ul>{gast_spieler_html}</ul>
            </div>
        </div>
        """
        LAST_UPLOADED_SUMMARY_HTML = summary_html
        
        # Erstelle die Rohdaten-Ansicht
        raw_html = """
        <style>
            h2 {{ border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 40px; }}
            ol {{ border: 1px solid #ccc; padding: 1em 1em 1em 4em; background-color: #f9f9f9; font-family: monospace; }}
            li {{ margin-bottom: 5px; }}
            pre {{ margin: 0; display: inline; }}
        </style>
        <h2>Zeilenweise Rohdaten der Analyse</h2>
        <ol>
        """
        for line in raw_lines:
            escaped_line = html.escape(line)
            raw_html += f"<li><pre>{escaped_line}</pre></li>"
        raw_html += "</ol>"
        LAST_UPLOADED_RAW_HTML = raw_html

        if parsed_data is None or not parsed_data.get("spielId"): 
            return jsonify({"error": "Analyse fehlgeschlagen. Überprüfe die PDF-Struktur."}), 400
        
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e: return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

# Der Rest der Datei (/debug, /save-data, etc.) bleibt unverändert.
# ... (Hier den restlichen Code von der vorherigen Version einfügen)
@app.route('/debug')
def debug_pdf_page():
    return LAST_UPLOADED_SUMMARY_HTML + LAST_UPLOADED_RAW_HTML

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
