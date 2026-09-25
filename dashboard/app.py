import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from flask import Flask, render_template
from payment_guard import AuditLog

app = Flask(__name__)


@app.route("/")
def index():
    records = AuditLog().fetch_all()
    for r in records:
        r["risk_signals"] = json.loads(r["risk_signals"])
        r["reasoning_trace"] = json.loads(r["reasoning_trace"])
    return render_template("index.html", records=records)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
