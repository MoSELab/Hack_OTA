import json
import struct
import subprocess
import time
from pathlib import Path

import can


BASE_DIR = Path(__file__).resolve().parent


class ECU:
    def __init__(self, name, can_bus, base_can_id):
        self.name = name
        self.can_bus = can_bus
        self.start_id = base_can_id
        self.data_id = base_can_id + 1
        self.end_id = base_can_id + 2
        self.result_id = base_can_id + 3
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

    def send_result(self, transfer_id, success, active_slot):
        slot_number = 0 if active_slot == "a" else 1
        payload = struct.pack(
            ">HBB",
            transfer_id,
            1 if success else 0,
            slot_number,
        )
        self.can_bus.send(can.Message(
            arbitration_id=self.result_id,
            data=payload,
            is_extended_id=False,
        ))
        print(f"CAN TX 0x{self.result_id:03X}: {payload.hex(' ')}")

    def handle_frame(self, message):
        data = bytes(message.data)
        if message.arbitration_id == self.start_id and len(data) >= 6:
            transfer_id, expected_size = struct.unpack(">HI", data[:6])
            self.transfer = {
                "transfer_id": transfer_id,
                "expected_size": expected_size,
                "chunks": {},
            }
            print("Transfer started:", self.transfer)
            return

        if not self.transfer:
            return

        if message.arbitration_id == self.data_id and len(data) >= 4:
            transfer_id, sequence = struct.unpack(">HH", data[:4])
            if transfer_id == self.transfer["transfer_id"]:
                self.transfer["chunks"][sequence] = data[4:]
            return

        if message.arbitration_id == self.end_id and len(data) >= 4:
            transfer_id, chunk_count = struct.unpack(">HH", data[:4])
            if transfer_id == self.transfer["transfer_id"]:
                self.install(chunk_count)

    def install(self, chunk_count):
        transfer_id = self.transfer["transfer_id"]
        try:
            package = b"".join(
                self.transfer["chunks"][sequence]
                for sequence in sorted(self.transfer["chunks"])
            )
            header_size = struct.unpack(">H", package[:2])[0]
            header = json.loads(
                package[2:2 + header_size].decode("utf-8")
            )
            firmware = package[2 + header_size:]

            old_status = self.load_status()
            active_slot = old_status.get("active_slot", "a")
            new_slot = "b" if active_slot == "a" else "a"
            slot_dir = self.slots / new_slot
            slot_dir.mkdir(parents=True, exist_ok=True)
            output_path = slot_dir / Path(
                header.get("filename", "update.bin")
            ).name
            output_path.write_bytes(firmware)

            status = {
                "ecu": self.name,
                "requested_target": header.get("target_ecu"),
                "active_slot": new_slot,
                "version": header.get("version", "0"),
                "filename": output_path.name,
                "bytes": len(firmware),
                "expected_size": header.get("expected_size", 0),
                "received_chunks": len(self.transfer["chunks"]),
                "announced_chunks": chunk_count,
                "last_update_id": header.get("update_id"),
                "last_result": "installed",
                "updated_at": time.time(),
            }
            self.save_status(status)
            self.send_result(transfer_id, True, new_slot)
            print("Installed:", status)
        except Exception as exc:
            active_slot = self.load_status().get("active_slot", "a")
            self.send_result(transfer_id, False, active_slot)
            print("Install failed:", exc)
        finally:
            self.transfer = None


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
    print("=== Raspberry Pi 2: HackOTA ECU ===")
    ecu_name = input("ECU Name [powertrain]: ").strip() or "powertrain"
    can_interface = input("CAN Interface [socketcan]: ").strip() or "socketcan"
    can_channel = input("CAN Channel [can0]: ").strip() or "can0"
    can_bitrate = int(input("CAN Bitrate [1000000]: ").strip() or "1000000")
    base_can_id = int(
        input("OTA Base CAN ID [0x700]: ").strip() or "0x700",
        0,
    )

    can_bus = open_can_bus(can_interface, can_channel, can_bitrate)
    ecu = ECU(ecu_name, can_bus, base_can_id)
    print(
        f"{ecu_name} ECU waiting on CAN IDs "
        f"0x{ecu.start_id:03X}-0x{ecu.end_id:03X}"
    )
    try:
        while True:
            message = can_bus.recv(timeout=1.0)
            if message is not None:
                ecu.handle_frame(message)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        can_bus.shutdown()


if __name__ == "__main__":
    main()
