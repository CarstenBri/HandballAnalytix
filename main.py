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

# Die Analyse-Funktion bleibt genau wie von dir vorgegeben
def parse_spielbericht(pdf_bytes):
    """
    Liest eine PDF und extrahiert die Daten basierend auf exakten
    Zeilennummern und Schlüsselwörtern.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
    
    lines = full_text.splitlines()
    
    data = {
        "spielId": None,
        "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spielklasse": "Unbekannt",
        "spielverlauf": [] # Hinzugefügt, damit es immer existiert
    }

    try:
        zeile_2 = lines[1]
        data["spielklasse"] = zeile_2.split(',')[0].strip()
        start_id = zeile_2.find("Spiel Nr. ") + len("Spiel Nr. ")
        end_id = zeile_2.find(" am")
        data["spielId"] = zeile_2[start_id:end_id].strip()
        start_datum = zeile_2.find(" am ") + len(" am ")
        data["datum"] = zeile_2[start_datum:].strip()

        zeile_7 = lines[6]
        teams_part = zeile_7.split('","')[1]
        teams = teams_part.split(' - ')
        data["teams"]["heim"] = teams[0].strip()
        data["teams"]["gast"] = teams[1].strip()

        zeile_8 = lines[7]
        ergebnis_part = zeile_8.split('","')[1]
        endstand_end = ergebnis_part.find(" (")
        data["ergebnis"]["endstand"] = ergebnis_part[:endstand_end].strip()
        start_halbzeit = ergebnis_part.find("(") + 1
        end_halbzeit = ergebnis_part.find(")")
        data["ergebnis"]["halbzeit"] = ergebnis_part[start_halbzeit:end_halbzeit].strip()
        start_sieger = ergebnis_part.find("Sieger ") + len("Sieger ")
        data["ergebnis"]["sieger"] = ergebnis_part[start_sieger:].replace('",', '').strip()
    except IndexError:
        return None, None, None

    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0], "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None, "aktion": ereignis[3].strip()
            })
            
    return data, full_text, "\n".join(lines)

# === ENDPUNKT 1: NUR ANALYSIEREN ===
@app.route('/upload', methods=['POST'])
def analyze_pdf_for_verification():
    """Nimmt eine PDF entgegen, analysiert sie und gibt die Daten als JSON zurück."""
    if 'file' not in request.files:
        return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data, _, _ = parse_spielbericht(pdf_bytes)
        if parsed_data is None or not parsed_data.get("spielId"): 
            return jsonify({"error": "Analyse fehlgeschlagen. Überprüfe die PDF-Struktur."}), 400
        # Sende die extrahierten Daten zur Überprüfung an den Client
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e:
        return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

# === ENDPUNKT 2: DATEN SPEICHERN ===
@app.route('/save-data', methods=['POST'])
def save_verified_data():
    """Nimmt verifizierte JSON-Daten entgegen und speichert sie in der Datenbank."""
    verified_data = request.get_json()
    if not verified_data:
        return jsonify({"error": "Keine Daten zum Speichern erhalten."}), 400
    
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
    except Exception as e:
        return jsonify({"error": f"DB-Fehler: {str(e)}"}), 500
    finally:
        if conn: cur.close(); conn.close()

# Hilfsfunktion und andere Routen bleiben bestehen
def ensure_db_table_exists(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spielberichte (
            id SERIAL PRIMARY KEY, spiel_id VARCHAR(50) UNIQUE NOT NULL, datum DATE,
            teams JSONB, ergebnis JSONB, spieler JSONB, spielverlauf JSONB,
            erstellt_am TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)

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

@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    # Diese Funktion bleibt unverändert
    if request.method == 'POST':
        if 'file' not in request.files: return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            parsed_data, raw_text, _ = parse_spielbericht(pdf_bytes)
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

# Die Analyse-Funktion bleibt genau wie von dir vorgegeben
def parse_spielbericht(pdf_bytes):
    """
    Liest eine PDF und extrahiert die Daten basierend auf exakten
    Zeilennummern und Schlüsselwörtern.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
    
    lines = full_text.splitlines()
    
    data = {
        "spielId": None,
        "datum": None,
        "teams": {"heim": "Unbekannt", "gast": "Unbekannt"},
        "ergebnis": {"endstand": "0:0", "halbzeit": "0:0", "sieger": "Unbekannt"},
        "spielklasse": "Unbekannt",
        "spielverlauf": [] # Hinzugefügt, damit es immer existiert
    }

    try:
        zeile_2 = lines[1]
        data["spielklasse"] = zeile_2.split(',')[0].strip()
        start_id = zeile_2.find("Spiel Nr. ") + len("Spiel Nr. ")
        end_id = zeile_2.find(" am")
        data["spielId"] = zeile_2[start_id:end_id].strip()
        start_datum = zeile_2.find(" am ") + len(" am ")
        data["datum"] = zeile_2[start_datum:].strip()

        zeile_7 = lines[6]
        teams_part = zeile_7.split('","')[1]
        teams = teams_part.split(' - ')
        data["teams"]["heim"] = teams[0].strip()
        data["teams"]["gast"] = teams[1].strip()

        zeile_8 = lines[7]
        ergebnis_part = zeile_8.split('","')[1]
        endstand_end = ergebnis_part.find(" (")
        data["ergebnis"]["endstand"] = ergebnis_part[:endstand_end].strip()
        start_halbzeit = ergebnis_part.find("(") + 1
        end_halbzeit = ergebnis_part.find(")")
        data["ergebnis"]["halbzeit"] = ergebnis_part[start_halbzeit:end_halbzeit].strip()
        start_sieger = ergebnis_part.find("Sieger ") + len("Sieger ")
        data["ergebnis"]["sieger"] = ergebnis_part[start_sieger:].replace('",', '').strip()
    except IndexError:
        return None, None, None

    verlauf_start = full_text.find("Spielverlauf\n")
    if verlauf_start != -1:
        verlauf_text = full_text[verlauf_start:]
        ereignisse = re.findall(r"(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([\d:]*)\s+(.*)", verlauf_text)
        for ereignis in ereignisse:
            data["spielverlauf"].append({
                "uhrzeit": ereignis[0], "spielzeit": ereignis[1],
                "spielstand": ereignis[2] if ereignis[2] else None, "aktion": ereignis[3].strip()
            })
            
    return data, full_text, "\n".join(lines)

# === ENDPUNKT 1: NUR ANALYSIEREN ===
@app.route('/upload', methods=['POST'])
def analyze_pdf_for_verification():
    """Nimmt eine PDF entgegen, analysiert sie und gibt die Daten als JSON zurück."""
    if 'file' not in request.files:
        return jsonify({"error": "Keine Datei gefunden"}), 400
    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data, _, _ = parse_spielbericht(pdf_bytes)
        if parsed_data is None or not parsed_data.get("spielId"): 
            return jsonify({"error": "Analyse fehlgeschlagen. Überprüfe die PDF-Struktur."}), 400
        # Sende die extrahierten Daten zur Überprüfung an den Client
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e:
        return jsonify({"error": f"PDF-Verarbeitung fehlgeschlagen: {str(e)}"}), 500

# === ENDPUNKT 2: DATEN SPEICHERN ===
@app.route('/save-data', methods=['POST'])
def save_verified_data():
    """Nimmt verifizierte JSON-Daten entgegen und speichert sie in der Datenbank."""
    verified_data = request.get_json()
    if not verified_data:
        return jsonify({"error": "Keine Daten zum Speichern erhalten."}), 400
    
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
    except Exception as e:
        return jsonify({"error": f"DB-Fehler: {str(e)}"}), 500
    finally:
        if conn: cur.close(); conn.close()

# Hilfsfunktion und andere Routen bleiben bestehen
def ensure_db_table_exists(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spielberichte (
            id SERIAL PRIMARY KEY, spiel_id VARCHAR(50) UNIQUE NOT NULL, datum DATE,
            teams JSONB, ergebnis JSONB, spieler JSONB, spielverlauf JSONB,
            erstellt_am TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)

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

@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    # Diese Funktion bleibt unverändert
    if request.method == 'POST':
        if 'file' not in request.files: return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            parsed_data, raw_text, _ = parse_spielbericht(pdf_bytes)
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

