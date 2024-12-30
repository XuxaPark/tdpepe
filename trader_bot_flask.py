from flask import Flask, render_template, send_file
import os

app = Flask(__name__)

LOG_FILE = "trading_log.txt"

@app.route('/')
def home():
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as file:
            logs = file.readlines()
    return render_template("home.html", logs=logs)

@app.route('/download_logs')
def download_logs():
    if os.path.exists(LOG_FILE):
        return send_file(LOG_FILE, as_attachment=True)
    return "Log file not found", 404

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)