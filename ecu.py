import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import can


BASE_DIR = Path(__file__).resolve().parent
ECU_NAME = "cluster_ecu"
STARTUP_CHECK_SECONDS = 3

DEFAULT_CLUSTER_SOURCE = '''\
import tkinter as tk
from datetime import datetime

root = tk.Tk()
root.title("HackOTA Vehicle Cluster")
root.configure(bg="#090d14")
root.attributes("-fullscreen", True)

title = tk.Label(
    root, text="VEHICLE CLUSTER", fg="#72d8ff", bg="#090d14",
    font=("Arial", 32, "bold")
)
title.pack(pady=35)

speed = tk.Label(
    root, text="0 km/h", fg="white", bg="#090d14",
    font=("Arial", 80, "bold")
)
speed.pack(expand=True)

slot = tk.Label(
    root, text="HackOTA A/B Cluster", fg="#ff5f7d", bg="#090d14",
    font=("Arial", 22)
)
slot.pack(pady=20)

clock = tk.Label(root, fg="#b5c0d4", bg="#090d14", font=("Arial", 18))
clock.pack(pady=20)

def update_clock():
    clock.config(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    root.after(1000, update_clock)

root.bind("<Escape>", lambda event: root.destroy())
update_clock()
root.mainloop()
'''


class ECU:
    def __init__(self, can_bus, base_can_id, manage_cluster=True):
        self.name = ECU_NAME
        self.can_bus = can_bus
        self.start_id = base_can_id
        self.data_id = base_can_id + 1
        self.end_id = base_can_id + 2
        self.result_id = base_can_id + 3
        self.root = BASE_DIR / "cluster_ecu_data"
        self.slots = self.root / "slots"
        self.flag_path = self.root / "active_slot.txt"
        self.status_path = self.root / "status.json"
        self.transfer = None
        self.manage_cluster = manage_cluster
        self.running_process = None
        self.prepare_ab_slots()
        if manage_cluster:
            self.running_process = self.launch_slot(self.active_slot())

    def slot_dir(self, slot):
        return self.slots / f"slot_{slot.lower()}"

    def prepare_ab_slots(self):
        for slot in ("A", "B"):
            directory = self.slot_dir(slot)
            directory.mkdir(parents=True, exist_ok=True)
            cluster_path = directory / "cluster.py"
            if not cluster_path.exists():
                cluster_path.write_text(
                    DEFAULT_CLUSTER_SOURCE,
                    encoding="utf-8",
                )
        if not self.flag_path.exists():
            self.flag_path.write_text("A", encoding="utf-8")
        if not self.status_path.exists():
            self.save_status({
                "ecu": self.name,
                "active_slot": "A",
                "version": "1.0.0",
                "last_result": "factory",
                "updated_at": time.time(),
            })

    def active_slot(self):
        slot = self.flag_path.read_text(encoding="utf-8").strip().upper()
        return slot if slot in {"A", "B"} else "A"

    def inactive_slot(self):
        return "B" if self.active_slot() == "A" else "A"

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

    def stop_cluster(self):
        if self.running_process is None or self.running_process.poll() is not None:
            return
        self.running_process.terminate()
        try:
            self.running_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.running_process.kill()

    def launch_slot(self, slot):
        cluster_path = self.slot_dir(slot) / "cluster.py"
        print(f"[BOOT] Slot {slot}: {cluster_path}")
        return subprocess.Popen(
            [sys.executable, str(cluster_path)],
            cwd=self.slot_dir(slot),
        )

    def activate_slot(self, target_slot):
        previous_slot = self.active_slot()
        self.stop_cluster()
        try:
            process = self.launch_slot(target_slot)
            time.sleep(STARTUP_CHECK_SECONDS)
            if process.poll() is not None:
                raise RuntimeError(
                    f"cluster.py exited with code {process.returncode}"
                )
            self.flag_path.write_text(target_slot, encoding="utf-8")
            self.running_process = process
            print(f"[COMMIT] Active Slot: {target_slot}")
            return True
        except Exception as exc:
            print(f"[ROLLBACK] Slot {target_slot}: {exc}")
            self.flag_path.write_text(previous_slot, encoding="utf-8")
            self.running_process = self.launch_slot(previous_slot)
            return False

    def send_result(self, transfer_id, success, active_slot):
        slot_number = 0 if active_slot == "A" else 1
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
        previous_slot = self.active_slot()
        try:
            package = b"".join(
                self.transfer["chunks"][sequence]
                for sequence in sorted(self.transfer["chunks"])
            )
            header_size = struct.unpack(">H", package[:2])[0]
            header = json.loads(package[2:2 + header_size].decode("utf-8"))
            cluster_source = package[2 + header_size:]

            target_slot = self.inactive_slot()
            output_path = self.slot_dir(target_slot) / "cluster.py"
            output_path.write_bytes(cluster_source)

            success = (
                self.activate_slot(target_slot)
                if self.manage_cluster
                else True
            )
            if not self.manage_cluster and success:
                self.flag_path.write_text(target_slot, encoding="utf-8")
            active_slot = target_slot if success else previous_slot
            previous_version = self.load_status().get("version", "1.0.0")

            status = {
                "ecu": self.name,
                "active_slot": active_slot,
                "version": (
                    header.get("version", "0")
                    if success
                    else previous_version
                ),
                "filename": "cluster.py",
                "bytes": len(cluster_source),
                "expected_size": header.get("expected_size", 0),
                "received_chunks": len(self.transfer["chunks"]),
                "announced_chunks": chunk_count,
                "last_update_id": header.get("update_id"),
                "last_result": "installed" if success else "rolled_back",
                "updated_at": time.time(),
            }
            self.save_status(status)
            self.send_result(transfer_id, success, active_slot)
            print("Update result:", status)
        except Exception as exc:
            self.send_result(transfer_id, False, previous_slot)
            print("Install failed:", exc)
        finally:
            self.transfer = None

    def close(self):
        self.stop_cluster()


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
        return can.interface.Bus(interface="socketcan", channel=channel)
    return can.interface.Bus(interface=interface, channel=channel)


def main():
    print("=== Raspberry Pi 2: HackOTA Cluster ECU ===")
    can_interface = input("CAN Interface [socketcan]: ").strip() or "socketcan"
    can_channel = input("CAN Channel [can0]: ").strip() or "can0"
    can_bitrate = int(input("CAN Bitrate [1000000]: ").strip() or "1000000")
    base_can_id = int(
        input("OTA Base CAN ID [0x700]: ").strip() or "0x700",
        0,
    )

    can_bus = open_can_bus(can_interface, can_channel, can_bitrate)
    ecu = ECU(can_bus, base_can_id)
    print(
        f"{ECU_NAME} waiting on CAN IDs "
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
        ecu.close()
        can_bus.shutdown()


if __name__ == "__main__":
    main()
