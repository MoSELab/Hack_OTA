import base64
import json
import socket
import threading
import time

import paho.mqtt.client as mqtt


NOTICE_TOPIC = "hackota/update/notice"
FILE_TOPIC = "hackota/update/file"
RESULT_TOPIC = "hackota/update/result"


class CentralGateway:
    def __init__(self, mqtt_client, ecu_host, ecu_port, result_port):
        self.mqtt_client = mqtt_client
        self.ecu_address = (ecu_host, ecu_port)
        self.result_port = result_port
        self.last_notice = None
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on_connect(self, client, userdata, flags, reason_code):
        if reason_code == 0:
            client.subscribe([(NOTICE_TOPIC, 0), (FILE_TOPIC, 0)])
            print("Subscribed to HackOTA update topics.")
        else:
            print("MQTT connection failed:", reason_code)

    def on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic == NOTICE_TOPIC:
                self.last_notice = payload
                print("Notice accepted:", payload)
            else:
                self.forward_file(payload)
        except Exception as exc:
            print("Gateway processing failed:", exc)

    def send_to_ecu(self, payload):
        self.sender.sendto(
            json.dumps(payload).encode("utf-8"),
            self.ecu_address,
        )

    def forward_file(self, payload):
        firmware = base64.b64decode(payload.get("data", ""))
        transfer_id = payload.get("update_id", str(time.time()))
        self.send_to_ecu({
            "kind": "start",
            "transfer_id": transfer_id,
            "target_ecu": payload.get("target_ecu", "powertrain"),
            "version": payload.get("version", "0"),
            "filename": payload.get("filename", "update.bin"),
            "expected_size": payload.get("size", len(firmware)),
        })
        sequence = 0
        for offset in range(0, len(firmware), 384):
            self.send_to_ecu({
                "kind": "data",
                "transfer_id": transfer_id,
                "sequence": sequence,
                "data": base64.b64encode(
                    firmware[offset:offset + 384]
                ).decode("ascii"),
            })
            sequence += 1
        self.send_to_ecu({
            "kind": "end",
            "transfer_id": transfer_id,
            "chunk_count": sequence,
        })
        print(
            f"Forwarded update_id={transfer_id} "
            f"target={payload.get('target_ecu')} bytes={len(firmware)}"
        )

    def listen_for_results(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        receiver.bind(("0.0.0.0", self.result_port))
        print("Listening for ECU results on UDP", self.result_port)
        while True:
            data, address = receiver.recvfrom(65535)
            try:
                result = json.loads(data.decode("utf-8"))
                result["gateway_received_from"] = address[0]
                self.mqtt_client.publish(
                    RESULT_TOPIC,
                    json.dumps(result),
                    qos=0,
                )
                print("ECU result published:", result)
            except Exception as exc:
                print("Invalid ECU result:", exc)


def main():
    print("=== Raspberry Pi 1: HackOTA Central Gateway ===")
    broker = input("MQTT Broker IP (Laptop IP): ").strip()
    if not broker:
        print("MQTT Broker IP is required.")
        return
    broker_port = int(input("MQTT Broker Port [1883]: ").strip() or "1883")
    username = input("MQTT Username [gateway]: ").strip() or "gateway"
    password = input("MQTT Password [gateway]: ").strip() or "gateway"
    ecu_host = input("ECU Raspberry Pi IP: ").strip()
    if not ecu_host:
        print("ECU Raspberry Pi IP is required.")
        return
    ecu_port = int(input("ECU UDP Port [17001]: ").strip() or "17001")
    result_port = int(
        input("Gateway Result UDP Port [17999]: ").strip() or "17999"
    )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set(username, password)
    gateway = CentralGateway(
        client,
        ecu_host,
        ecu_port,
        result_port,
    )
    client.on_connect = gateway.on_connect
    client.on_message = gateway.on_message
    client.connect(broker, broker_port)
    threading.Thread(
        target=gateway.listen_for_results,
        daemon=True,
    ).start()
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

