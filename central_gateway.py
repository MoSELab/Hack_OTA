import base64
import hashlib
import json
import struct
import subprocess
import threading
import time

import can
import paho.mqtt.client as mqtt


NOTICE_TOPIC = "hackota/update/notice"
FILE_TOPIC = "hackota/update/file"
RESULT_TOPIC = "hackota/update/result"


class CentralGateway:
    def __init__(self, mqtt_client, can_bus, base_can_id):
        self.mqtt_client = mqtt_client
        self.can_bus = can_bus
        self.start_id = base_can_id
        self.data_id = base_can_id + 1
        self.end_id = base_can_id + 2
        self.result_id = base_can_id + 3
        self.last_notice = None
        self.pending = {}

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

    def send_can(self, arbitration_id, data):
        self.can_bus.send(can.Message(
            arbitration_id=arbitration_id,
            data=data,
            is_extended_id=False,
        ))
        print(f"CAN TX 0x{arbitration_id:03X}: {bytes(data).hex(' ')}")

    def forward_file(self, payload):
        firmware = base64.b64decode(payload.get("data", ""))
        update_id = payload.get("update_id", str(time.time()))
        transfer_id = int.from_bytes(
            hashlib.sha256(update_id.encode("utf-8")).digest()[:2],
            "big",
        ) or 1

        header = json.dumps({
            "update_id": update_id,
            "version": payload.get("version", "0"),
            "filename": "cluster.py",
            "expected_size": payload.get("size", len(firmware)),
        }).encode("utf-8")
        package = struct.pack(">H", len(header)) + header + firmware
        chunks = [
            package[offset:offset + 4]
            for offset in range(0, len(package), 4)
        ]
        self.pending[transfer_id] = {
            "update_id": update_id,
            "version": payload.get("version", "0"),
            "filename": "cluster.py",
            "bytes": len(firmware),
        }

        self.send_can(
            self.start_id,
            struct.pack(">HI", transfer_id, len(package)),
        )
        for sequence, chunk in enumerate(chunks):
            self.send_can(
                self.data_id,
                struct.pack(">HH", transfer_id, sequence) + chunk,
            )
            time.sleep(0.005)
        self.send_can(
            self.end_id,
            struct.pack(">HH", transfer_id, len(chunks)),
        )
        print(
            f"Forwarded update_id={update_id} "
            f"bytes={len(firmware)}"
        )

    def listen_for_results(self):
        print(f"Waiting for ECU result on CAN ID 0x{self.result_id:03X}")
        while True:
            message = self.can_bus.recv(timeout=1.0)
            if message is None or message.arbitration_id != self.result_id:
                continue
            if len(message.data) < 4:
                continue

            transfer_id, status, slot_number = struct.unpack(
                ">HBB",
                bytes(message.data[:4]),
            )
            metadata = self.pending.pop(transfer_id, {})
            result = {
                **metadata,
                "ecu": "cluster_ecu",
                "status": "installed" if status == 1 else "failed",
                "last_result": "installed" if status == 1 else "failed",
                "active_slot": "a" if slot_number == 0 else "b",
                "transfer_id": transfer_id,
                "updated_at": time.time(),
            }
            self.mqtt_client.publish(
                RESULT_TOPIC,
                json.dumps(result),
                qos=0,
            )
            print("ECU result published:", result)


def configure_socketcan(channel, bitrate):
    commands = [
        ["sudo", "ip", "link", "set", channel, "down"],
        [
            "sudo", "ip", "link", "set", channel,
            "type", "can", "bitrate", str(bitrate),
        ],
        ["sudo", "ip", "link", "set", channel, "up"],
    ]
    for command in commands:
        print("RUN:", " ".join(command))
        result = subprocess.run(command, check=False)
        if result.returncode != 0 and "down" not in command:
            raise RuntimeError(
                f"CAN interface setup failed: {' '.join(command)}"
            )


def open_can_bus(interface, channel, bitrate):
    if interface == "socketcan":
        configure_socketcan(channel, bitrate)
        return can.interface.Bus(
            interface="socketcan",
            channel=channel,
        )
    return can.interface.Bus(interface=interface, channel=channel)


def main():
    print("=== Raspberry Pi 1: HackOTA Central Gateway ===")
    broker = (
        input("MQTT Broker IP [210.123.37.150]: ").strip()
        or "210.123.37.150"
    )
    broker_port = int(input("MQTT Broker Port [1883]: ").strip() or "1883")
    can_interface = input("CAN Interface [socketcan]: ").strip() or "socketcan"
    can_channel = input("CAN Channel [can0]: ").strip() or "can0"
    can_bitrate = int(input("CAN Bitrate [1000000]: ").strip() or "1000000")
    base_can_id = int(
        input("OTA Base CAN ID [0x700]: ").strip() or "0x700",
        0,
    )

    can_bus = open_can_bus(can_interface, can_channel, can_bitrate)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    gateway = CentralGateway(client, can_bus, base_can_id)
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
        can_bus.shutdown()


if __name__ == "__main__":
    main()
