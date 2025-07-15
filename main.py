# main.py

import re
import io
from flask import Flask, request, jsonify
from flask_cors import CORS  # Wichtig für die lokale Entwicklung
from pypdf import PdfReader
# Firebase wird hier nicht mehr benötigt
# import firebase_admin
# from firebase_admin import credentials, firestore

# Erstelle eine Flask-Webanwendung
app = Flask(__name__)
CORS(app) # Erlaubt deinem HTML (auf einem anderen Port) mit dem Server zu sprechen

# Die parse_spielbericht Funktion von vorhin bleibt genau gleich...
def parse_spielbericht(pdf_bytes):
    # ... (keine Änderungen hier, einfach die Funktion von oben einfügen)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"

    data = {"spielId": "N/A", "spielverlauf": []} # Beispiel-Struktur
    match = re.search(r"Spiel Nr\. (\d+)", full_text)
    if match:
        data["spielId"] = match.group(1)
        
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

@app.route('/upload', methods=['POST'])
def upload_spielbericht():
    """
    Diese Funktion wird aufgerufen, wenn eine Datei an die URL /upload gesendet wird.
    """
    if 'file' not in request.files:
        return jsonify({"error": "Keine Datei gefunden"}), 400

    pdf_file = request.files['file']
    try:
        pdf_bytes = pdf_file.read()
        parsed_data = parse_spielbericht(pdf_bytes)
        
        # Anstatt in Firebase zu speichern, geben wir die Daten einfach zurück
        # Später kannst du hier eine andere Datenbank anbinden
        print("Daten erfolgreich extrahiert:", parsed_data)

        return jsonify({
            "success": True, 
            "message": "Daten erfolgreich extrahiert.",
            "data": parsed_data
        }), 200
    except Exception as e:
        return jsonify({"error": f"Verarbeitung fehlgeschlagen: {e}"}), 500

# Dieser Teil startet den Server, wenn du "python main.py" ausführst
if __name__ == '__main__':
    app.run(debug=True, port=5001)
