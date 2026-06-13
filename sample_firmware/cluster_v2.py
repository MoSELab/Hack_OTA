import tkinter as tk
from datetime import datetime


root = tk.Tk()
root.title("HackOTA Cluster v2")
root.configure(bg="#07131c")
root.attributes("-fullscreen", True)

title = tk.Label(
    root,
    text="CLUSTER v2",
    fg="#67e8f9",
    bg="#07131c",
    font=("Arial", 34, "bold"),
)
title.pack(pady=40)

speed = tk.Label(
    root,
    text="0 km/h",
    fg="#f8fafc",
    bg="#07131c",
    font=("Arial", 86, "bold"),
)
speed.pack(expand=True)

mode = tk.Label(
    root,
    text="A/B OTA UPDATED",
    fg="#4ade80",
    bg="#07131c",
    font=("Arial", 24, "bold"),
)
mode.pack(pady=20)

clock = tk.Label(
    root,
    fg="#94a3b8",
    bg="#07131c",
    font=("Arial", 18),
)
clock.pack(pady=25)


def update_clock():
    clock.config(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    root.after(1000, update_clock)


root.bind("<Escape>", lambda event: root.destroy())
update_clock()
root.mainloop()
