import os
from flask import Flask, request, jsonify
import mysql.connector
import redis
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)
REQS = Counter("api_requests_total", "Total API requests", ["endpoint"])

cache = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379,
                    decode_responses=True)

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "mysql"),
        user=os.getenv("DB_USER", "irrig"),
        password=os.getenv("DB_PASSWORD", "irrigpass"),
        database=os.getenv("DB_NAME", "irrigation"),
        connection_timeout=3,
    )

def recommend(moisture):
    if moisture < 20:
        return {"action": "irrigate_now", "duration_min": 30}
    if moisture < 40:
        return {"action": "irrigate_light", "duration_min": 10}
    if moisture <= 70:
        return {"action": "no_irrigation", "duration_min": 0}
    return {"action": "skip_overwatered", "duration_min": 0}

@app.get("/health")
def health():
    REQS.labels("health").inc()
    status = {"api": "ok"}
    try:
        c = get_db(); c.close(); status["mysql"] = "ok"
    except Exception as e:
        status["mysql"] = f"error: {e}"
    try:
        cache.ping(); status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"
    code = 200 if all(v == "ok" for v in status.values()) else 503
    return jsonify(status), code

@app.post("/reading")
def reading():
    REQS.labels("reading").inc()
    data = request.get_json(silent=True) or {}
    try:
        field = str(data.get("field_id", "field-1"))
        moisture = float(data["moisture"])
    except (KeyError, ValueError, TypeError):
        return jsonify(error="'moisture' (number) is required"), 400
    if not 0 <= moisture <= 100:
        return jsonify(error="moisture must be 0-100"), 400

    rec = recommend(moisture)
    conn = get_db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS readings (
        id INT AUTO_INCREMENT PRIMARY KEY, field_id VARCHAR(50),
        moisture FLOAT, action VARCHAR(30),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    cur.execute("INSERT INTO readings (field_id, moisture, action) VALUES (%s,%s,%s)",
                (field, moisture, rec["action"]))
    conn.commit(); cur.close(); conn.close()

    cache.setex(f"latest:{field}", 300, rec["action"])
    return jsonify(field_id=field, moisture=moisture, recommendation=rec), 201

@app.get("/recommend/<field_id>")
def latest(field_id):
    REQS.labels("recommend").inc()
    val = cache.get(f"latest:{field_id}")
    if val:
        return jsonify(field_id=field_id, action=val, source="cache")
    return jsonify(error="no recent reading"), 404

@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
