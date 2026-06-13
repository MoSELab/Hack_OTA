import base64
import json
import time
import uuid
from pathlib import Path
from threading import Lock

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, redirect, render_template_string, request, url_for


NOTICE_TOPIC = "hackota/update/notice"
FILE_TOPIC = "hackota/update/file"
RESULT_TOPIC = "hackota/update/result"

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "hackota_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = "hackota-secret"
app.config["MQTT_CLIENT"] = None
app.config["ECU_STATUS"] = {}
app.config["LOGS"] = []
status_lock = Lock()
log_lock = Lock()

PAGE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HackOTA Web + Update Server</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #0b0e14; color: #edf1f7; }
    main { width: min(1100px, 94vw); margin: 35px auto; }
    header { display:flex; justify-content:space-between; align-items:start; }
    h1 { margin: 4px 0 8px; }
    .danger { color:#ff8ca1; background:#581323; padding:8px 12px;
      border-radius:999px; font-weight:bold; }
    .panel { margin:18px 0; padding:20px; background:#151a25;
      border:1px solid #30394c; border-radius:14px; }
    .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
    label { display:grid; gap:6px; color:#aeb8ca; }
    input, textarea, button { font:inherit; border-radius:7px; }
    input, textarea { padding:10px; color:white; background:#0d121b;
      border:1px solid #354057; }
    textarea { min-height:75px; }
    button { padding:9px 12px; border:0; background:#ef3d62; color:white;
      font-weight:bold; cursor:pointer; }
    .wide { grid-column:span 2; }
    pre { white-space:pre-wrap; word-break:break-all; color:#87d7ff; }
    .log-box { max-height:420px; overflow:auto; background:#090d14;
      border:1px solid #293247; border-radius:9px; padding:12px; }
    .log-entry { padding:8px 4px; border-bottom:1px solid #222b3c;
      font-family:Consolas,monospace; font-size:14px; }
    .log-time { color:#77849a; }
    .log-level { color:#ff7892; font-weight:bold; margin:0 8px; }
    @media(max-width:800px) {
      .grid { grid-template-columns:1fr; }
      .wide { grid-column:auto; }
      header { display:block; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <small>TRAINING SYSTEM</small>
      <h1>HackOTA Web + Update Server</h1>
      <p>Laptop에서 Firmware 등록과 MQTT 배포를 수행합니다.</p>
    </div>
    <span class="danger">INSECURE MODE</span>
  </header>

  <section class="panel">
    <h2>Firmware 등록</h2>
    <form action="/upload" method="post" enctype="multipart/form-data" class="grid">
      <label>Cluster Update (`cluster.py`)
        <input type="file" name="firmware" required>
      </label>
      <label>Version<input name="version" value="1.1.0"></label>
      <label>Operator<input name="operator" value="admin"></label>
      <label class="wide">Release Notes
        <textarea name="release_notes">urgent update</textarea>
      </label>
      <button type="submit">Upload and Publish</button>
    </form>
  </section>

  <section class="panel">
    <h2>최근 ECU 상태</h2>
    <pre id="ecu-status">{{ statuses | tojson(indent=2) }}</pre>
  </section>

  <section class="panel">
    <h2>Update Server Log</h2>
    <div id="logs" class="log-box"></div>
  </section>
</main>
<script>
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  })[ch]);
}
async function refreshDashboard() {
  const [logResponse, statusResponse] = await Promise.all([
    fetch("/api/logs"), fetch("/api/ecu-status")
  ]);
  const logs = await logResponse.json();
  const statuses = await statusResponse.json();
  document.getElementById("logs").innerHTML = logs.map(item =>
    `<div class="log-entry"><span class="log-time">${escapeHtml(item.time)}</span>` +
    `<span class="log-level">${escapeHtml(item.level)}</span>` +
    `${escapeHtml(item.message)}</div>`
  ).join("");
  document.getElementById("ecu-status").textContent =
    JSON.stringify(statuses, null, 2);
}
refreshDashboard();
setInterval(refreshDashboard, 2000);
</script>
</body>
</html>
"""


def metadata_path(package_id):
    return UPLOAD_DIR / f"{package_id}.json"


def load_metadata(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_metadata(metadata):
    metadata_path(metadata["package_id"]).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_log(level, message):
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
    }
    with log_lock:
        app.config["LOGS"].insert(0, entry)
        del app.config["LOGS"][200:]
    print(f"[{level}] {message}")


def on_mqtt_connect(client, userdata, flags, reason_code):
    if reason_code == 0:
        client.subscribe(RESULT_TOPIC, qos=0)
        add_log("MQTT", f"Connected and subscribed: {RESULT_TOPIC}")
    else:
        add_log("ERROR", f"MQTT connection failed: {reason_code}")


def on_mqtt_message(client, userdata, message):
    try:
        status = json.loads(message.payload.decode("utf-8"))
        ecu_name = status.get("ecu", "cluster_ecu")
        with status_lock:
            app.config["ECU_STATUS"][ecu_name] = status
        add_log(
            "ECU",
            f"result={status.get('last_result')} "
            f"slot={status.get('active_slot')} "
            f"version={status.get('version')}",
        )
    except Exception as exc:
        add_log("ERROR", f"Invalid ECU result: {exc}")


def publish_package(package_id):
    metadata = load_metadata(metadata_path(package_id))
    if not metadata:
        raise FileNotFoundError("package not found")

    client = app.config["MQTT_CLIENT"]
    if client is None:
        raise RuntimeError("MQTT client is not connected")

    firmware = (UPLOAD_DIR / metadata["stored_name"]).read_bytes()
    update_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    notice = {
        "type": "notice",
        "update_id": update_id,
        "package_id": package_id,
        "version": metadata["version"],
        "filename": metadata["filename"],
        "size": len(firmware),
        "release_notes": metadata["release_notes"],
        "operator": metadata["operator"],
        "created_at": metadata["created_at"],
    }
    client.publish(NOTICE_TOPIC, json.dumps(notice), qos=0, retain=True)
    time.sleep(0.15)
    client.publish(FILE_TOPIC, json.dumps({
        **notice,
        "type": "file",
        "data": base64.b64encode(firmware).decode("ascii"),
    }), qos=0)
    add_log(
        "PUBLISH",
        f"Published {metadata['filename']} version={metadata['version']} "
        f"update_id={update_id}",
    )
    return update_id


@app.get("/")
def index():
    with status_lock:
        statuses = dict(app.config["ECU_STATUS"])
    return render_template_string(
        PAGE,
        statuses=statuses,
    )


@app.post("/upload")
def upload():
    firmware = request.files.get("firmware")
    if firmware is None or not firmware.filename:
        return jsonify({"error": "firmware is required"}), 400

    package_id = uuid.uuid4().hex[:10]
    filename = firmware.filename
    stored_name = f"{package_id}_{Path(filename).name}"
    firmware.save(UPLOAD_DIR / stored_name)
    metadata = {
        "package_id": package_id,
        "filename": filename,
        "stored_name": stored_name,
        "version": request.form.get("version", "0.0.0"),
        "release_notes": request.form.get("release_notes", ""),
        "operator": request.form.get("operator", "anonymous"),
        "created_at": time.time(),
    }
    save_metadata(metadata)
    add_log(
        "UPLOAD",
        f"Stored {filename} version={metadata['version']} "
        f"operator={metadata['operator']}",
    )
    try:
        publish_package(package_id)
    except Exception as exc:
        add_log("ERROR", f"Publish failed for {filename}: {exc}")
        return jsonify({"error": str(exc)}), 500
    return redirect(url_for("index"))


@app.get("/api/logs")
def logs():
    with log_lock:
        return jsonify(list(app.config["LOGS"]))


@app.get("/api/ecu-status")
def ecu_status():
    with status_lock:
        return jsonify(dict(app.config["ECU_STATUS"]))


def main():
    print("=== Laptop: HackOTA Web + Update Server ===")
    broker = (
        input("MQTT Broker IP [210.123.37.150]: ").strip()
        or "210.123.37.150"
    )
    broker_port = int(input("MQTT Broker Port [1883]: ").strip() or "1883")
    web_host = input("Web Host [0.0.0.0]: ").strip() or "0.0.0.0"
    web_port = int(input("Web Port [5000]: ").strip() or "5000")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message
    client.connect(broker, broker_port)
    client.loop_start()
    app.config["MQTT_CLIENT"] = client
    try:
        app.run(host=web_host, port=web_port, debug=True, use_reloader=False)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
