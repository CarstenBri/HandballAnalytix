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
# === ANALYSE-FUNKTION, BASIEREND AUF DEINEN ZEILEN-ANGABEN ===
# ==============================================================================
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
        "spielklasse": "Unbekannt"
    }

    try:
        # --- Zeile 2: Spielklasse, Spiel-ID, Datum ---
        zeile_2 = lines[1]
        data["spielklasse"] = zeile_2.split(',')[0].strip()
        start_id = zeile_2.find("Spiel Nr. ") + len("Spiel Nr. ")
        end_id = zeile_2.find(" am")
        data["spielId"] = zeile_2[start_id:end_id].strip()
        start_datum = zeile_2.find(" am ") + len(" am ")
        data["datum"] = zeile_2[start_datum:].strip()

        # --- Zeile 7: Heim- und Gastmannschaft ---
        zeile_7 = lines[6]
        teams_part = zeile_7.split('","')[1]
        teams = teams_part.split(' - ')
        data["teams"]["heim"] = teams[0].strip()
        data["teams"]["gast"] = teams[1].strip()

        # --- Zeile 8: Ergebnis, Halbzeit, Sieger ---
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
        print("FEHLER: Eine der erwarteten Zeilen wurde in der PDF nicht gefunden.")
        return None, full_text, "\n".join(lines)

    # Spielverlauf-Extraktion (vorerst deaktiviert, um uns auf die Kopfdaten zu konzentrieren)
    data["spielverlauf"] = []
            
    return data, full_text, "\n".join(lines)

# ==============================================================================
# === DEBUG-SEITE: Zeigt JSON und Rohdaten an ===
# ==============================================================================
@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    if request.method == 'POST':
        if 'file' not in request.files: return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            parsed_data, raw_text, _ = parse_spielbericht(pdf_bytes)
            
            # Sicherheitsabfrage, falls die Analyse fehlschlägt
            if parsed_data is None:
                parsed_data = {"error": "Analyse fehlgeschlagen, siehe Rohtext unten."}

            html_response = f"""
            <style>
                body {{ font-family: sans-serif; margin: 2em; display: flex; gap: 2em; }}
                .column {{ flex: 1; }}
                h2 {{ border-bottom: 2px solid #ccc; padding-bottom: 5px; }}
                pre {{ background-color: #f4f4f4; padding: 1em; white-space: pre-wrap; word-wrap: break-word; border: 1px solid #ddd; height: 80vh; overflow-y: scroll; }}
            </style>
            <div class="column">
                <h2>1. Extrahierte Daten (JSON)</h2>
                <pre>{json.dumps(parsed_data, indent=2, ensure_ascii=False)}</pre>
            </div>
            <div class="column">
                <h2>2. Roher Text (direkt aus der PDF)</h2>
                <pre>{raw_text}</pre>
            </div>
            """
            return html_response
        except Exception as e: return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>"

    return '''
        <!doctype html>
        <title>PDF Debug Uploader</title>
        <style>body { font-family: sans-serif; margin: 2em; }</style>
        <h1>Lade eine PDF zur Analyse hoch</h1>
        <form method=post enctype=multipart/form-data>
          <input type=file name=file>
          <input type=submit value=Analysieren>
        </form>
    '''

# ==============================================================================
# Die Routen /upload und /view-data sind temporär deaktiviert,
# um Verwirrung zu vermeiden. Wir fokussieren uns auf /debug.
# ==============================================================================
@app.route('/upload', methods=['POST'])
def upload_spielbericht():
    return jsonify({"message": "Upload ist deaktiviert. Bitte benutze /debug zur Verifizierung."})

@app.route('/view-data')
def view_data():
    return "<h1>Datenansicht</h1><p>Datenbank ist deaktiviert. Bitte benutze /debug zur Verifizierung.</p>"

if __name__ == '__main__':
    app.run(port=5001)
