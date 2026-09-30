"""
gui.py — the Ultron-style interface.

Imports the brain from ultron_me.py. Run: python gui.py
"""

import math
import queue
import datetime
import threading

import tkinter as tk

from ultron_em import CONFIG, Agent, Speaker, Listener


# ------------------------------------------------------ 
# ---------------------
# Palette
# ---------------------------------------------------------------------------
BG        = "#0a0a0c"
PANEL     = "#0f0f13"
RED       = (255, 43, 43)
RED_HEX   = "#2bffe3"
ORANGE    = "#35f2ff"
DIM       = "#179aac"
TEXT      = "#d8d8dc"
TEXT_DIM  = "#126A9C"


def blend(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


# ===========================================================================
#  HUD — animated core
# ===========================================================================
class HUD(tk.Canvas):
    STATE_ENERGY = {
        "idle": 0.20,
        "listening": 0.85,
        "thinking": 0.65,
        "speaking": 1.00,
        "error": 0.95,
    }

    def __init__(self, master, size=420):
        super().__init__(master, width=size, height=size,
                         bg=BG, highlightthickness=0, bd=0)
        self.size = size
        self.cx = size / 2
        self.cy = size / 2
        self.t = 0.0
        self.energy = 0.20
        self.state = "idle"
        self.scan = 0.0
        self._animate()

    def _animate(self):
        self.t += 0.033
        self.scan = (self.scan + 2.2) % 360
        target = self.STATE_ENERGY.get(self.state, 0.2)
        self.energy += (target - self.energy) * 0.08
        self._draw()
        self.after(33, self._animate)

    def _draw(self):
        self.delete("all")
        cx, cy = self.cx, self.cy
        R = self.size * 0.40
        bg_rgb = (10, 10, 12)

        for i in range(12, 0, -1):
            r = R + i * 1.8
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             outline=blend(RED, bg_rgb, 1 - i / 14.0), width=1)

        arcs = [
            (R,          1.0, 110, 3, RED_HEX),
            (R * 0.87,  -1.6,  62, 2, ORANGE),
            (R * 0.74,   2.3, 150, 2, RED_HEX),
            (R * 1.06,  -0.7,  40, 2, "#85942F"),
        ]
        for rad, speed, span, w, col in arcs:
            a0 = math.degrees(self.t * speed) % 360
            self.create_arc(cx - rad, cy - rad, cx + rad, cy + rad,
                            start=a0, extent=span, style="arc",
                            outline=col, width=w)
            self.create_arc(cx - rad, cy - rad, cx + rad, cy + rad,
                            start=a0 + 180, extent=span * 0.55, style="arc",
                            outline=col, width=w)

        ticks = 48
        for i in range(ticks):
            ang = math.radians(i * (360 / ticks) + self.t * 8)
            r_out = R * 0.95
            r_in = r_out - (9 if i % 4 == 0 else 4)
            self.create_line(cx + r_out * math.cos(ang),
                             cy + r_out * math.sin(ang),
                             cx + r_in * math.cos(ang),
                             cy + r_in * math.sin(ang),
                             fill=DIM, width=1)

        bars = 56
        for i in range(bars):
            ang = math.radians(i * (360 / bars) + self.t * 14)
            base = R * 1.10
            wobble = abs(math.sin(i * 0.55 + self.t * 5.0))
            h = 3 + 26 * self.energy * wobble
            x1 = cx + base * math.cos(ang)
            y1 = cy + base * math.sin(ang)
            x2 = cx + (base + h) * math.cos(ang)
            y2 = cy + (base + h) * math.sin(ang)
            self.create_line(x1, y1, x2, y2,
                             fill=blend(RED, bg_rgb,
                                        0.25 + 0.5 * (1 - wobble)), width=2)

        sweep = math.radians(self.scan)
        r_scan = R * 0.70
        self.create_line(cx, cy,
                         cx + r_scan * math.cos(sweep),
                         cy + r_scan * math.sin(sweep),
                         fill="#378a8a", width=1)

        core = R * 0.46 * (0.96 + 0.04 * math.sin(self.t * 4.0))
        core += self.energy * R * 0.06

        for i in range(16, 0, -1):
            r = core * (1 + i * 0.06)
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             outline=blend((255, 70, 70), bg_rgb, i / 17.0),
                             width=1)

        iris_r = core * 0.92
        self.create_oval(cx - iris_r, cy - iris_r, cx + iris_r, cy + iris_r,
                         fill="#041a19", outline=RED_HEX, width=2)

        pupil_r = iris_r * (0.52 + 0.10 * self.energy)
        self.create_oval(cx - pupil_r, cy - pupil_r,
                         cx + pupil_r, cy + pupil_r,
                         fill="#1fffbc", outline="")

        hot = pupil_r * 0.55
        self.create_oval(cx - hot, cy - hot, cx + hot, cy + hot,
                         fill="#b0fff4", outline="")

        label = {
            "idle": "STANDBY",
            "listening": "LISTENING",
            "thinking": "PROCESSING",
            "speaking": "SPEAKING",
            "error": "FAULT",
        }.get(self.state, "STANDBY")

        self.create_text(cx, cy + R * 1.42, text=label,
                         fill=RED_HEX if self.state != "idle" else TEXT_DIM,
                         font=("Consolas", 11, "bold"))
        self.create_text(cx, cy + R * 1.58, text="U L T R O N",
                         fill="#239bca", font=("Consolas", 9))


# ===========================================================================
#  APP
# ===========================================================================
class UltronApp:
    
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.q = queue.Queue()

        # brain pieces from jarvis_me.py
        self.speaker = Speaker(cfg["tts_rate"])
        self.speaker.start()
        self.listener = Listener() if cfg["voice_input"] else None
        self.agent = Agent(cfg, lambda k, p: self.q.put((k, p)))

        self.muted = not cfg["voice_output"]

        self._build_ui()
        self._poll()
        self._greet()

    def _build_ui(self):
        r = self.root
        r.title("JARVIS")
        r.configure(bg=BG)
        r.geometry("1120x640")
        r.minsize(940, 560)

        r.columnconfigure(0, weight=0)
        r.columnconfigure(1, weight=1)
        r.rowconfigure(0, weight=1)
        r.rowconfigure(1, weight=0)

        # left: HUD
        left = tk.Frame(r, bg=BG, width=460)
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 6), pady=14)
        left.grid_propagate(False)
        self.hud = HUD(left, size=420)
        self.hud.pack(expand=True)

        # right: chat
        right = tk.Frame(r, bg=PANEL, highlightbackground="#241014",
                         highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 14), pady=14)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        header = tk.Frame(right, bg=PANEL)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        tk.Label(header, text="◈  ULTRON", bg=PANEL, fg=RED_HEX,
                 font=("Consolas", 14, "bold")).pack(side="left")
        self.status_lbl = tk.Label(header, text="● STANDBY", bg=PANEL,
                                   fg=TEXT_DIM, font=("Consolas", 10))
        self.status_lbl.pack(side="right")

        self.chat = tk.Text(right, bg="#08080a", fg=TEXT, bd=0,
                            wrap="word", padx=16, pady=12,
                            insertbackground=RED_HEX,
                            font=("Consolas", 11), state="disabled",
                            selectbackground="#080F0F")
        self.chat.grid(row=1, column=0, sticky="nsew", padx=12)

        self.chat.tag_config("user", foreground="#060c0b",
                             font=("Consolas", 11, "bold"))
        self.chat.tag_config("bot", foreground=TEXT)
        self.chat.tag_config("sys", foreground=TEXT_DIM,
                             font=("Consolas", 9, "italic"))
        self.chat.tag_config("err", foreground="#090f0f")

        bar = tk.Frame(right, bg=PANEL)
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        bar.columnconfigure(0, weight=1)

        self.entry = tk.Entry(bar, bg="#070A0A", fg=TEXT, bd=0,
                              insertbackground=RED_HEX,
                              font=("Consolas", 12))
        self.entry.grid(row=0, column=0, sticky="ew", ipady=9, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.focus_set()

        self.mic_btn = self._btn(bar, "◉ MIC", self._listen,
                                
                                 "#443636", "#2E2525")
        self.mic_btn.grid(row=0, column=1, padx=4)
        if self.listener is None or not self.listener.available:
            self.mic_btn.configure(state="disabled", fg="#3a3a42")

        self.mute_btn = self._btn(
            bar, "🔊 ON" if not self.muted else "🔇 OFF",
            self._toggle_mute, "#121216", "#36363f")
        self.mute_btn.grid(row=0, column=2, padx=4)

        self._btn(bar, "SEND ▸", self._submit, "#0d0f0f", RED_HEX).grid(
            row=0, column=3, padx=(4, 0))

        self.bottom = tk.Label(r, text="", bg=BG, fg=TEXT_DIM,
                               font=("Consolas", 9), anchor="w")
        self.bottom.grid(row=1, column=0, columnspan=2, sticky="ew",
                         padx=16, pady=(0, 8))
        self._update_bottom()

        r.protocol("WM_DELETE_WINDOW", self._close)

    def _btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         activebackground="#0a192a", activeforeground=RED_HEX,
                         bd=0, relief="flat", padx=14, pady=8,
                         font=("Consolas", 10, "bold"), cursor="hand2")

    def _update_bottom(self):
        backend = self.cfg["backend"]
        model = self.cfg["model"] if backend != "offline" else "—"
        stt = "on" if (self.listener and self.listener.available) else "off"
        tts = "muted" if self.muted else (
            "on" if self.speaker.available else "unavailable")
        self.bottom.config(
            text=f"backend={backend}  model={model}  stt={stt}  tts={tts}  "
                 f"term_root={self.cfg.get('terminal_root', '~')}")

    def _append(self, who, text, tag):
        self.chat.configure(state="normal")
        stamp = datetime.datetime.now().strftime("%H:%M")
        self.chat.insert("end", f"[{stamp}] {who}\n", tag)
        self.chat.insert("end", f"{text}\n\n",
                         "bot" if tag == "user" else tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _status(self, text, color=TEXT_DIM):
        self.status_lbl.config(text=f"● {text}", fg=color)

    def _greet(self):
        self._append("SYS", "ULTRON online. All systems nominal.", "sys")
        msg = "Systems online. How are you buddy....?"
        self._append("ULTRON", msg, "bot")
        if not self.muted:
            self.speaker.say(msg)

    def _submit(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._append("YOU", text, "user")
        self.hud.state = "thinking"
        self._status("PROCESSING", RED_HEX)
        threading.Thread(target=self.agent.handle, args=(text,),
                         daemon=True).start()

    def _toggle_mute(self):
        self.muted = not self.muted
        self.mute_btn.config(text="🔊 ON" if not self.muted else "🔇 OFF")
        self._update_bottom()

    def _listen(self):
        if not self.listener or not self.listener.available:
            return
        self.hud.state = "listening"
        self._status("LISTENING", ORANGE)

        def worker():
            try:
                text = self.listener.listen()
                self.q.put(("transcript", text))
            except Exception as e:
                self.q.put(("stt_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()

                if kind == "state":
                    self.hud.state = payload
                    self._status(payload.upper(),
                                 RED_HEX if payload != "idle" else TEXT_DIM)

                elif kind == "reply":
                    self._append("ULTRON", payload, "bot")
                    self.hud.state = "speaking" if not self.muted else "idle"
                    self._status("SPEAKING" if not self.muted else "STANDBY",
                                 RED_HEX if not self.muted else TEXT_DIM)
                    if not self.muted:
                        self.speaker.say(payload)
                        delay = min(14000, 900 + len(payload) * 55)
                        self.root.after(
                            delay,
                            lambda: setattr(self.hud, "state", "idle"))
                        self.root.after(
                            delay, lambda: self._status("STANDBY"))

                elif kind == "transcript":
                    self.entry.delete(0, "end")
                    self.entry.insert(0, payload)
                    self._submit()

                elif kind == "stt_error":
                    self.hud.state = "idle"
                    self._status("STANDBY")
                    self._append("SYS", f"voice input: {payload}", "sys")

        except queue.Empty:
            pass

        self.root.after(50, self._poll)

    def _close(self):
        try:
            self.speaker.stop()
        except Exception:
            pass
        self.root.destroy()


# ===========================================================================
def main():
    root = tk.Tk()
    UltronApp(root, CONFIG)
    root.mainloop()


if __name__ == "__main__":
    main()