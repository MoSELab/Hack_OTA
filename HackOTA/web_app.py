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
status_lock = Lock()

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
    input, select, textarea, button { font:inherit; border-radius:7px; }
    input, select, textarea { padding:10px; color:white; background:#0d121b;
      border:1px solid #354057; }
    textarea { min-height:75px; }
    button { padding:9px 12px; border:0; background:#ef3d62; color:white;
      font-weight:bold; cursor:pointer; }
    button.secondary { background:#343e52; }
    .wide { grid-column:span 2; }
    .check { display:flex; align-items:center; }
    table { width:100%; border-collapse:collapse; }
    th, td { padding:11px; text-align:left; border-bottom:1px solid #30394c; }
    .actions { display:flex; gap:6px; }
    pre { white-space:pre-wrap; word-break:break-all; color:#87d7ff; }
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
      <label>Firmware<input type="file" name="firmware" required></label>
      <label>Target ECU
        <select name="target_ecu">
          <option>powertrain</option><option>body</option><option>adas</option>
          <option>unknown-ecu</option>
        </select>
      </label>
      <label>Version<input name="version" value="1.1.0"></label>
      <label>Operator<input name="operator" value="admin"></label>
      <label class="wide">Release Notes
        <textarea name="release_notes">urgent update</textarea>
      </label>
      <label class="check"><input type="checkbox" name="deploy_now">
        업로드 즉시 배포</label>
      <button type="submit">Upload Package</button>
    </form>
  </section>

  <section class="panel">
    <h2>최근 ECU 상태</h2>
    <pre>{{ statuses | tojson(indent=2) }}</pre>
  </section>

  <section class="panel">
    <h2>등록된 패키지</h2>
    <table>
      <thead><tr><th>ID</th><th>File</th><th>Target</th><th>Version</th>
        <th>Operator</th><th>Actions</th></tr></thead>
      <tbody>
      {% for item in packages %}
        <tr>
          <td>{{ item.package_id }}</td><td>{{ item.filename }}</td>
          <td>{{ item.target_ecu }}</td><td>{{ item.version }}</td>
          <td>{{ item.operator }}</td>
          <td class="actions">
            <button onclick="post('/api/deploy/{{ item.package_id }}')">Deploy</button>
            <button onclick="post('/api/replay/{{ item.package_id }}','count=5')">Replay x5</button>
            <button class="secondary"
              onclick="post('/api/delete/{{ item.package_id }}')">Delete</button>
          </td>
        </tr>
      {% else %}
        <tr><td colspan="6">No packages</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </section>
</main>
<script>
async function post(url, body="") {
  const response = await fetch(url, {
    method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:body
  });
  alert(await response.text());
  location.reload();
}
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


def package_records():
    records = []
    for path in UPLOAD_DIR.glob("*.json"):
        metadata = load_metadata(path)
        if metadata:
            records.append(metadata)
    return sorted(records, key=lambda item: item["created_at"], reverse=True)


def on_mqtt_connect(client, userdata, flags, reason_code):
    if reason_code == 0:
        client.subscribe(RESULT_TOPIC, qos=0)
        print("Subscribed:", RESULT_TOPIC)
    else:
        print("MQTT connection failed:", reason_code)


def on_mqtt_message(client, userdata, message):
    try:
        status = json.loads(message.payload.decode("utf-8"))
        ecu_name = status.get("ecu", status.get("target_ecu", "unknown"))
        with status_lock:
            app.config["ECU_STATUS"][ecu_name] = status
        print("ECU result:", status)
    except Exception as exc:
        print("Invalid ECU result:", exc)


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
        "target_ecu": metadata["target_ecu"],
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
    print(
        f"Published {metadata['filename']} version={metadata['version']} "
        f"target={metadata['target_ecu']} update_id={update_id}"
    )
    return update_id


@app.get("/")
def index():
    with status_lock:
        statuses = dict(app.config["ECU_STATUS"])
    return render_template_string(
        PAGE,
        packages=package_records(),
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
        "target_ecu": request.form.get("target_ecu", "powertrain"),
        "version": request.form.get("version", "0.0.0"),
        "release_notes": request.form.get("release_notes", ""),
        "operator": request.form.get("operator", "anonymous"),
        "created_at": time.time(),
    }
    save_metadata(metadata)
    print("UPLOAD:", json.dumps(metadata, ensure_ascii=False))
    if request.form.get("deploy_now") == "on":
        publish_package(package_id)
    return redirect(url_for("index"))


@app.post("/api/deploy/<package_id>")
def deploy(package_id):
    try:
        update_id = publish_package(package_id)
        return jsonify({"status": "published", "update_id": update_id})
    except FileNotFoundError:
        return jsonify({"error": "package not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/replay/<package_id>")
def replay(package_id):
    count = max(1, min(int(request.form.get("count", "3")), 20))
    try:
        update_ids = [publish_package(package_id) for _ in range(count)]
        return jsonify({"status": "published", "update_ids": update_ids})
    except FileNotFoundError:
        return jsonify({"error": "package not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/delete/<package_id>")
def delete(package_id):
    metadata = load_metadata(metadata_path(package_id))
    if metadata:
        (UPLOAD_DIR / metadata["stored_name"]).unlink(missing_ok=True)
        metadata_path(package_id).unlink(missing_ok=True)
    return jsonify({"status": "deleted"})


@app.get("/api/packages")
def packages():
    return jsonify(package_records())


@app.get("/api/ecu-status")
def ecu_status():
    with status_lock:
        return jsonify(dict(app.config["ECU_STATUS"]))


def main():
    print("=== Laptop: HackOTA Web + Update Server ===")
    broker = input("MQTT Broker IP [127.0.0.1]: ").strip() or "127.0.0.1"
    broker_port = int(input("MQTT Broker Port [1883]: ").strip() or "1883")
    username = input("MQTT Username [admin]: ").strip() or "admin"
    password = input("MQTT Password [admin]: ").strip() or "admin"
    web_host = input("Web Host [0.0.0.0]: ").strip() or "0.0.0.0"
    web_port = int(input("Web Port [5000]: ").strip() or "5000")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set(username, password)
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

