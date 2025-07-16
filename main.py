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
# === NEUER START: Nur zeilenweise Ausgabe für die Analyse ===
# ==============================================================================
def analyze_pdf_by_lines(pdf_bytes):
    """
    Liest eine PDF und gibt den Inhalt als eine Liste von nummerierten Zeilen zurück.
    Keine Analyse, nur rohe Daten.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
    
    # Teile den gesamten Text in einzelne Zeilen auf
    lines = full_text.splitlines()
    
    return lines

# ==============================================================================
# === DEBUG-SEITE: Zeigt die nummerierten Zeilen an ===
# ==============================================================================
@app.route('/debug', methods=['GET', 'POST'])
def debug_pdf():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "<h1>Fehler</h1><p>Keine Datei hochgeladen.</p>", 400
        file = request.files['file']
        try:
            pdf_bytes = file.read()
            lines = analyze_pdf_by_lines(pdf_bytes)
            
            # HTML-Antwort mit nummerierter Liste erstellen
            html_response = """
            <style>
                body { font-family: monospace, sans-serif; margin: 2em; line-height: 1.6; }
                h1 { font-family: sans-serif; }
                ol { border: 1px solid #ccc; padding: 1em 1em 1em 4em; background-color: #f9f9f9; }
                li { margin-bottom: 5px; }
                pre { margin: 0; display: inline; }
            </style>
            <h1>Zeilenweise Rohdaten der PDF</h1>
            <p>Bitte gib mir die Zeilennummern für die folgenden Informationen:</p>
            <ul>
                <li>Spiel-ID und Datum</li>
                <li>Heim- und Gastmannschaft</li>
                <li>Endergebnis, Halbzeitstand und Sieger</li>
            </ul>
            <ol>
            """
            
            for line in lines:
                # Wir stellen sicher, dass HTML-Tags im Text nicht interpretiert werden
                import html
                escaped_line = html.escape(line)
                html_response += f"<li><pre>{escaped_line}</pre></li>"
                
            html_response += "</ol>"
            return html_response
        except Exception as e:
            return f"<h1>Ein Fehler ist aufgetreten</h1><p>{e}</p>"

    # GET-Request: Zeigt das Upload-Formular an
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
# Die alten Routen /upload und /view-data lassen wir vorerst unberührt.
# Wir konzentrieren uns nur auf die /debug-Seite.
# Die alten Funktionen sind hier zur Vollständigkeit noch enthalten.
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
    return jsonify({"message": "Diese Funktion ist für die neue Analyse vorübergehend deaktiviert."})

@app.route('/view-data')
def view_data():
    return "<h1>Datenansicht</h1><p>Diese Funktion ist während der Neuanalyse der PDF-Struktur deaktiviert.</p>"

if __name__ == '__main__':
    app.run(port=5001)

