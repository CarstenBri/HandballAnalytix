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
# === NEUE VERSION: Zeilenweise Verarbeitung nach deiner Logik ===
# ==============================================================================
def parse_spielbericht(pdf_bytes):
    """
    Analysiert den Inhalt einer PDF-Datei durch zeilenweise Verarbeitung.
    Dieser Ansatz ist robuster und folgt der Struktur der PDF.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    num_pages_to_read = min(2, len(reader.pages))
    for i in range(num_pages_to_read):
        full_text += reader.pages[i].extract_text() + "\n"

    data = {
        "spielId": None,
        "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spieler": {"heim": [], "gast": []},
        "spielverlauf": []
    }

    # Wir teilen den gesamten Text in einzelne Zeilen auf
    lines = full_text.splitlines()

    # Wir gehen jede Zeile einzeln durch
    for line in lines:
        # Wir bereinigen die Zeile von führenden/folgenden Leerzeichen
        clean_line = line.strip()

        # --- Suche nach Schlüsselwörtern ---
        
        # Suche nach der Zeile, die Spiel-ID und Datum enthält
        if clean_line.startswith('"Spiel/Datum'):
            try:
                # Die Zeile sieht so aus: "...,"WERT",...". Wir extrahieren den Wert.
                value = clean_line.split('","')[1].strip('", ')
                # Jetzt extrahieren wir die einzelnen Teile aus dem Wert
                match_id = re.search(r'(\d+)', value)
                if match_id:
                    data["spielId"] = match_id.group(1)
                
                match_date = re.search(r'am (\d{2}\.\d{2}\.\d{2})', value)
                if match_date:
                    data["datum"] = match_date.group(1)
            except IndexError:
                print("LOG: Konnte Wert für Spiel/Datum nicht extrahieren.")

        # Suche nach der Zeile mit den Teamnamen
        elif clean_line.startswith('"Heim Gast'):
            try:
                value = clean_line.split('","')[1].strip('", ')
                # Wir teilen den Wert am " - " Trennzeichen
                teams = value.split(' - ')
                if len(teams) == 2:
                    data["teams"]["heim"] = teams[0].strip()
                    data["teams"]["gast"] = teams[1].strip()
            except IndexError:
                print("LOG: Konnte Wert für Heim/Gast nicht extrahieren.")

        # Suche nach der Zeile mit dem Endergebnis
        elif clean_line.startswith('"Endstand'):
            try:
                value = clean_line.split('","')[1].strip('", ')
                # Regex, um Endstand, Halbzeit und Sieger zu finden
                match = re.search(r'(\d+:\d+)\s*\((\d+:\d+)\)\s*,\s*Sieger\s*(.*)', value)
                if match:
                    data["ergebnis"]["endstand"] = match.group(1).strip()
                    data["ergebnis"]["halbzeit"] = match.group(2).strip()
                    # Wir entfernen noch das Wort "Zuschauer", falls es am Ende steht
                    data["ergebnis"]["sieger"] = match.group(3).replace('Zuschauer:', '').strip()
            except IndexError:
                print("LOG: Konnte Wert für Endstand nicht extrahieren.")

    # --- SPIELVERLAUF-PARSING (bleibt gleich) ---
    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0], "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None, "aktion": ereignis[3].strip()
            })
            
    # Wir geben das Ergebnis für die normale Funktion und die Texte für die Debug-Seite zurück
    return data, full_text, "\n".join(lines)
# ==============================================================================
# === AB HIER BLEIBT DER CODE UNVERÄNDERT ===
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
    conn = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        ensure_db_table_exists(cur)
        cur.execute("SELECT spiel_id, datum, ergebnis, teams FROM spielberichte ORDER BY erstellt_am DESC;")
        berichte = cur.fetchall()
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
    except Exception as e: return f"<h1>Fehler</h1><p>{e}</p>", 500
    finally:
        if conn: cur.close(); conn.close()

@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    if request.method == 'POST':
        if 'file' not in request.files: return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            parsed_data, raw_text, clean_text = parse_spielbericht(pdf_bytes)
            html_response = f"""
            <style>
                body {{ font-family: sans-serif; margin: 2em; }}
                h2 {{ border-bottom: 2px solid #ccc; padding-bottom: 5px; }}
                pre {{ background-color: #f4f4f4; padding: 1em; white-space: pre-wrap; word-wrap: break-word; border: 1px solid #ddd; }}
            </style>
            <h1>PDF Debug-Ansicht</h1>
            <h2>1. Extrahierte Daten (JSON)</h2>
            <pre>{json.dumps(parsed_data, indent=2, ensure_ascii=False)}</pre>
            <h2>2. Roher Text (direkt aus der PDF)</h2>
            <pre>{raw_text}</pre>
            """
            return html_response
        except Exception as e: return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>"
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
