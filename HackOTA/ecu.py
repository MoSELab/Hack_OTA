import base64
import json
import socket
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class ECU:
    def __init__(self, name, gateway_host, gateway_port):
        self.name = name
        self.gateway_address = (gateway_host, gateway_port)
        self.root = BASE_DIR / f"{name}_ecu_data"
        self.slots = self.root / "slots"
        self.status_path = self.root / "status.json"
        self.transfer = None
        self.slots.mkdir(parents=True, exist_ok=True)
        if not self.status_path.exists():
            self.save_status({
                "ecu": name,
                "active_slot": "a",
                "version": "1.0.0",
                "last_result": "factory",
                "updated_at": time.time(),
            })

    def load_status(self):
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_status(self, status):
        self.status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def handle(self, packet):
        kind = packet.get("kind")
        if kind == "start":
            self.transfer = {
                "transfer_id": packet.get("transfer_id"),
                "target_ecu": packet.get("target_ecu"),
                "version": packet.get("version", "0"),
                "filename": packet.get("filename", "update.bin"),
                "expected_size": packet.get("expected_size", 0),
                "chunks": {},
            }
            print("Transfer started:", self.transfer)
        elif kind == "data" and self.transfer:
            sequence = packet.get("sequence", 0)
            self.transfer["chunks"][sequence] = base64.b64decode(
                packet.get("data", "")
            )
        elif kind == "end" and self.transfer:
            self.install()

    def install(self):
        old_status = self.load_status()
        active_slot = old_status.get("active_slot", "a")
        new_slot = "b" if active_slot == "a" else "a"
        firmware = b"".join(
            self.transfer["chunks"][sequence]
            for sequence in sorted(self.transfer["chunks"])
        )

        slot_dir = self.slots / new_slot
        slot_dir.mkdir(parents=True, exist_ok=True)
        output_path = slot_dir / Path(self.transfer["filename"]).name
        output_path.write_bytes(firmware)

        status = {
            "ecu": self.name,
            "requested_target": self.transfer["target_ecu"],
            "active_slot": new_slot,
            "version": self.transfer["version"],
            "filename": output_path.name,
            "bytes": len(firmware),
            "expected_size": self.transfer["expected_size"],
            "last_update_id": self.transfer["transfer_id"],
            "last_result": "installed",
            "updated_at": time.time(),
        }
        self.save_status(status)
        self.report(status)
        print("Installed:", status)
        self.transfer = None

    def report(self, status):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(
            json.dumps(status).encode("utf-8"),
            self.gateway_address,
        )
        sender.close()


def main():
    print("=== Raspberry Pi 2: HackOTA ECU ===")
    ecu_name = input("ECU Name [powertrain]: ").strip() or "powertrain"
    listen_host = input("Listen Host [0.0.0.0]: ").strip() or "0.0.0.0"
    listen_port = int(input("ECU UDP Port [17001]: ").strip() or "17001")
    gateway_host = input("Central Gateway Raspberry Pi IP: ").strip()
    if not gateway_host:
        print("Central Gateway IP is required.")
        return
    gateway_port = int(
        input("Gateway Result UDP Port [17999]: ").strip() or "17999"
    )

    ecu = ECU(ecu_name, gateway_host, gateway_port)
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind((listen_host, listen_port))
    print(f"{ecu_name} ECU listening on UDP {listen_port}")
    try:
        while True:
            data, address = receiver.recvfrom(65535)
            try:
                ecu.handle(json.loads(data.decode("utf-8")))
            except Exception as exc:
                print("Packet failed:", address, exc)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        receiver.close()


if __name__ == "__main__":
    main()

