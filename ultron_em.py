
"""
jarvis_me.py — the AI agent (brain).

Handles: LLM calls, all built-in commands, terminal, memory,
speech-to-text, text-to-speech. No GUI code lives here.

The GUI imports this and calls .handle(text) on a worker thread.
"""

import os
import random
import re
import shlex
import shutil
import socket
import queue
import base64
import uuid
import random
import string
import secrets
import calendar
import hashlib
import datetime
import threading
import subprocess
import webbrowser
import platform
import urllib.parse
import urllib.request
from pathlib import Path
import speech_recognition as ar

# Optional dependencies
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    requests = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

import random

def handle_greeting(command):
    command = command.lower().strip()

    greetings = {
        "hello": [
            "Hello. I am Ultron. How can I assist you?",
            "Hello. Ultron is online and ready."
        ],
        "hi": [
            "Hi. How can I help you?",
            "Hi. Ultron is ready."
        ],
        "good morning": [
            "Good morning. Ultron is ready to assist you."
        ],
        "good afternoon": [
            "Good afternoon. How may I assist you?"
        ],
        "good evening": [
            "Good evening. What can I do for you?"
        ],
        "how are you": [
            "I am functioning perfectly. What about you?"
        ],
        "who are you": [
            "I am Ultron, your personal AI assistant."
        ],
        "thank you": [
            "You're welcome. Always ready to assist."
        ],
        "bye": [
            "Goodbye. Ultron going offline."
        ],
        "good night": [
            "Good night. I will be here when you need me."
        ]
    }

    for greeting, responses in greetings.items():
        if greeting in command:
            return random.choice(responses)
        continue

    return None
# ---------------------------------------------------------------------------
# CONFIG — edit this
# ---------------------------------------------------------------------------
CONFIG = {
    # "ollama"  -> local models, free, no API key  (recommended)
    # "openai"  -> OpenAI-compatible API
    # "offline" -> no LLM, only built-in local commands
    "backend": "ollama",

    "model": "llama3.1",
    "ollama_url": "http://localhost:11434/api/chat",
    "openai_url": "https://api.openai.com/v1/chat/completions",
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),

    "voice_output": True,
    "voice_input": True,
    "tts_rate": 175,

    "system_prompt": (
        "You are ULTRON, an advanced AI assistant running on the user's machine. "
        "You are calm, precise, dryly witty, and fiercely competent. You address "
        "the user directly and never waste words. Keep answers under 120 words "
        "unless the user explicitly asks for detail. Never mention that you are "
        "a language model."
    ),

    # terminal sandbox root
    "terminal_root": os.path.expanduser("~"),
}


# ===========================================================================
#  SPEAKER — background text-to-speech worker
# ===========================================================================

class Speaker(threading.Thread):
    """Runs pyttsx3 on its own thread so the GUI never freezes."""

    def __init__(self, rate=175):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.rate = rate
        self.engine = None
        self.available = False

    def run(self):
        if pyttsx3 is not None:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty("rate", self.rate)
                for v in self.engine.getProperty("voices"):
                    name = (v.name or "").lower()
                    if "david" in name or "male" in name or "george" in name:
                        self.engine.setProperty("voice", v.id)
                        break
                self.available = True
            except Exception as e:
                print(f"[TTS] unavailable: {e}")

        while True:
            text = self.q.get()
            if text is None:
                break
            if self.engine:
                try:
                    self.engine.say(text)
                    self.engine.runAndWait()
                except Exception as e:
                    print(f"[TTS] error: {e}")

    def say(self, text):
        if self.available:
            self.q.put(text)

    def stop(self):
        self.q.put(None)


# ==========================================================================
#  LISTENER — microphone capture + Google speech recognition
# ===========================================================================
class Listener:
    """Wraps speech_recognition. Call .listen() on a worker thread."""

    def __init__(self):
        self.recognizer = None
        self.mic = None
        if sr is not None:
            try:
                self.recognizer = sr.Recognizer()
                self.recognizer.energy_threshold = 300
                self.recognizer.dynamic_energy_threshold = True
                self.mic = sr.Microphone()
            except Exception as e:
                print(f"[STT] unavailable: {e}")
                self.recognizer = None

    @property
    def available(self):
        return self.recognizer is not None

    def listen(self, timeout=6, phrase_limit=12):
        if not self.available:
            raise RuntimeError("microphone / speech_recognition not available")
        with self.mic as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
            audio = self.recognizer.listen(source, timeout=timeout,
                                           phrase_time_limit=phrase_limit)
        return self.recognizer.recognize_google(audio)


# ===========================================================================
#  TERMINAL — sandboxed filesystem + shell access
# ===========================================================================
class Terminal:
    """
    Sandboxed terminal. All paths are resolved inside `root`.
    Shell commands are restricted to a whitelist.
    """

    SAFE_SHELL = {
        "ls", "dir", "pwd", "cd", "cat", "type", "head", "tail",
        "wc", "echo", "grep", "find", "which", "where",
        "git", "python", "python3", "pip", "node", "npm",
        "ping", "curl", "wget", "df", "du", "date", "whoami",
        "tree", "sort", "uniq", "cut", "sed", "awk",
    }

    FORBIDDEN = [
        "rm -rf /", "rm -rf ~", ":(){:|:&};:", "mkfs",
        "dd if=", "> /dev/sd", "chmod -R 777 /",
        "shutdown", "reboot", "halt", "poweroff",
        "format c:", "del /f /s /q c:\\",
    ]

    def __init__(self, root=None):
        self.root = Path(root or os.path.expanduser("~")).resolve()

    def _resolve(self, path):
        if not path:
            return self.root
        p = (self.root / path).resolve() if not os.path.isabs(path) \
            else Path(path).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise PermissionError(
                f"Path escapes sandbox: {p} (root={self.root})"
            )
        return p

    def _check_forbidden(self, cmd):
        low = cmd.lower()
        for bad in self.FORBIDDEN:
            if bad.lower() in low:
                raise PermissionError(f"Blocked dangerous pattern: {bad}")

    def run(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return "Empty command."

        self._check_forbidden(cmd)

        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            return f"Parse error: {e}"

        if not parts:
            return "Empty command."

        verb = parts[0].lower()
        args = parts[1:]

        builtins = {
            "cd":     self._cd,
            "ls":     self._ls,
            "dir":    self._ls,
            "pwd":    lambda a: str(self.root),
            "cat":    self._cat,
            "type":   self._cat,
            "head":   self._head,
            "tail":   self._tail,
            "mkdir":  self._mkdir,
            "rmdir":  self._rmdir,
            "touch":  self._touch,
            "rm":     self._rm,
            "cp":     self._cp,
            "mv":     self._mv,
            "find":   self._find,
            "grep":   self._grep,
            "wc":     self._wc,
            "tree":   self._tree,
            "write":  self._write,
            "append": self._append,
        }
        
        if verb in builtins:
            try:
                return builtins[verb](args)
            except Exception as e:
                return f"Error: {e}"

        if verb not in self.SAFE_SHELL:
            return (f"Command '{verb}' not whitelisted. "
                    f"Allowed: {', '.join(sorted(self.SAFE_SHELL))}")

        try:
            out = subprocess.check_output(
                parts, cwd=str(self.root),
                stderr=subprocess.STDOUT, text=True, timeout=30,
            )
            return out.strip() or "(no output)"
        except subprocess.CalledProcessError as e:
            return f"Exit {e.returncode}:\n{e.output.strip()}"
        except subprocess.TimeoutExpired:
            return "Command timed out (30s)."
        except FileNotFoundError:
            return f"'{verb}' not found on this system."
        except Exception as e:
            return f"Execution failed: {e}"

    # -- built-ins ----------------------------------------------------------
    def _cd(self, args):
        target = args[0] if args else "~"
        p = self._resolve(target) if target != "~" else self.root
        if not p.exists():
            return f"No such directory: {target}"
        if not p.is_dir():
            return f"Not a directory: {target}"
        self.root = p
        return f"→ {p}"

    def _ls(self, args):
        target = self._resolve(args[0]) if args else self.root
        if not target.exists():
            return f"Not found: {target}"
        if target.is_file():
            return f"{target.name}  ({target.stat().st_size} bytes)"
        entries = sorted(target.iterdir(),
                         key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            return "(empty)"
        lines = []
        for e in entries:
            if e.is_dir():
                lines.append(f"  📁 {e.name}/")
            else:
                lines.append(f"  📄 {e.name}  ({e.stat().st_size}B)")
        return f"{target}\n" + "\n".join(lines)

    def _cat(self, args):
        if not args:
            return "Usage: cat <file>"
        p = self._resolve(args[0])
        if not p.is_file():
            return f"Not a file: {args[0]}"
        if p.stat().st_size > 200_000:
            return f"File too large ({p.stat().st_size} bytes). Use head/tail."
        return p.read_text(errors="replace")

    def _head(self, args):
        n = 10
        if args and args[0].isdigit():
            n, args = int(args[0]), args[1:]
        if not args:
            return "Usage: head [n] <file>"
        p = self._resolve(args[0])
        if not p.is_file():
            return f"Not a file: {args[0]}"
        return "\n".join(p.read_text(errors="replace").splitlines()[:n])

    def _tail(self, args):
        n = 10
        if args and args[0].isdigit():
            n, args = int(args[0]), args[1:]
        if not args:
            return "Usage: tail [n] <file>"
        p = self._resolve(args[0])
        if not p.is_file():
            return f"Not a file: {args[0]}"
        return "\n".join(p.read_text(errors="replace").splitlines()[-n:])

    def _mkdir(self, args):
        if not args:
            return "Usage: mkdir <dir>"
        p = self._resolve(args[0])
        p.mkdir(parents=True, exist_ok=True)
        return f"Created {p}"

    def _rmdir(self, args):
        if not args:
            return "Usage: rmdir <dir>"
        p = self._resolve(args[0])
        if not p.is_dir():
            return f"Not a directory: {args[0]}"
        p.rmdir()
        return f"Removed {p}"

    def _touch(self, args):
        if not args:
            return "Usage: touch <file>"
        p = self._resolve(args[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return f"Touched {p}"

    def _rm(self, args):
        if not args:
            return "Usage: rm <file>"
        p = self._resolve(args[0])
        if not p.exists():
            return f"Not found: {args[0]}"
        if p.is_dir():
            return "Refusing to rm a directory. Use rmdir (must be empty)."
        p.unlink()
        return f"Deleted {p}"

    def _cp(self, args):
        if len(args) != 2:
            return "Usage: cp <src> <dst>"
        src = self._resolve(args[0])
        dst = self._resolve(args[1])
        if not src.is_file():
            return f"Source not a file: {args[0]}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return f"Copied {src.name} → {dst}"

    def _mv(self, args):
        if len(args) != 2:
            return "Usage: mv <src> <dst>"
        src = self._resolve(args[0])
        dst = self._resolve(args[1])
        if not src.exists():
            return f"Not found: {args[0]}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {src.name} → {dst}"

    def _find(self, args):
        pattern = args[0] if args else "*"
        matches = list(self.root.rglob(pattern))
        if not matches:
            return "(no matches)"
        return "\n".join(str(m.relative_to(self.root)) for m in matches[:100])

    def _grep(self, args):
        if len(args) < 2:
            return "Usage: grep <pattern> <file>"
        pattern, fname = args[0], args[1]
        p = self._resolve(fname)
        if not p.is_file():
            return f"Not a file: {fname}"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"Bad regex: {e}"
        hits = [f"{i+1}: {ln}" for i, ln in
                enumerate(p.read_text(errors="replace").splitlines())
                if rx.search(ln)]
        return "\n".join(hits) if hits else "(no matches)"

    def _wc(self, args):
        if not args:
            return "Usage: wc <file>"
        p = self._resolve(args[0])
        if not p.is_file():
            return f"Not a file: {args[0]}"
        txt = p.read_text(errors="replace")
        return (f"{len(txt.splitlines())} lines, "
                f"{len(txt.split())} words, {len(txt)} chars")

    def _tree(self, args):
        target = self._resolve(args[0]) if args else self.root
        if not target.is_dir():
            return f"Not a directory: {target}"
        lines = [f"{target.name}/"]

        def walk(d, prefix=""):
            entries = sorted(d.iterdir(),
                             key=lambda p: (p.is_file(), p.name.lower()))
            for i, e in enumerate(entries):
                last = i == len(entries) - 1
                branch = "└── " if last else "├── "
                lines.append(prefix + branch + e.name +
                             ("/" if e.is_dir() else ""))
                if e.is_dir() and len(lines) < 200:
                    walk(e, prefix + ("    " if last else "│   "))

        walk(target)
        return "\n".join(lines[:200])

    def _write(self, args):
        if len(args) < 2:
            return "Usage: write <file> <text>"
        p = self._resolve(args[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(" ".join(args[1:]))
        return f"Wrote {p.stat().st_size} bytes to {p}"

    def _append(self, args):
        if len(args) < 2:
            return "Usage: append <file> <text>"
        p = self._resolve(args[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(" ".join(args[1:]) + "\n")
        return f"Appended to {p}"


# ===========================================================================
#  AGENT — reasoning + tools
# ===========================================================================
class Agent:
    """
    The core. Call agent.handle(text) from a worker thread.
    All output goes through the `emit(kind, payload)` callback.

    Emitted kinds:
        "state"  -> "thinking" | "error"
        "reply"  -> final answer string
    """

    def __init__(self, cfg, emit):
        self.cfg = cfg
        self.emit = emit
        self.history = [{"role": "system", "content": cfg["system_prompt"]}]
        self.memory = {}
        self.busy = threading.Lock()
        self.terminal = Terminal(root=cfg.get("terminal_root"))

    # -- public entry point -------------------------------------------------
    def handle(self, text):
        if not self.busy.acquire(blocking=False):
            self.emit("reply", "Still processing the previous request.")
            return
        try:
            self.emit("state", "thinking")
            reply = self._local(text)
            if reply is None:
                reply = self._llm(text)
            self.emit("reply", reply)
        except Exception as e:
            self.emit("state", "error")
            self.emit("reply", f"System fault: {e}")
        finally:
            self.busy.release()

    # -- built-in tools -----------------------------------------------------
    def _local(self, text):
        t = text.lower().strip().rstrip("?.!")
        raw = text.strip()

        # ---- terminal passthrough ----------------------------------------
        if t.startswith("term ") or t.startswith("$ "):
            cmd = raw.split(" ", 1)[1] if t.startswith("term ") else raw[2:]
            return self.terminal.run(cmd)

        if t in ("pwd", "where am i", "current directory"):
            return self.terminal.run("pwd")
        if t in ("ls", "list files", "list directory"):
            return self.terminal.run("ls")
        if t.startswith("cd "):
            return self.terminal.run(raw)
        if t.startswith("cat ") or t.startswith("type "):
            return self.terminal.run(raw)
        if t in ("tree", "show tree"):
            return self.terminal.run("tree")

        # ---- time & date -------------------------------------------------
        if t in ("time", "what time is it", "what's the time", "the time"):
            return "It is " + datetime.datetime.now().strftime("%I:%M %p") + "."
        if t in ("date", "what's the date", "what is the date", "today"):
            return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y") + "."
        if t in ("day", "what day is it", "what day is today"):
            return "Today is " + datetime.datetime.now().strftime("%A") + "."
        if t in ("year", "what year is it"):
            return "It is " + datetime.datetime.now().strftime("%Y") + "."
        if t in ("timestamp", "unix time"):
            return f"Unix timestamp: {int(datetime.datetime.now().timestamp())}"

        if t.startswith("calendar"):
            m = re.search(r"(\d{1,2})", t)
            now = datetime.datetime.now()
            month = int(m.group(1)) if m else now.month
            year = now.year
            ym = re.search(r"(\d{4})", t)
            if ym:
                year = int(ym.group(1))
            return "\n" + calendar.month(year, month)

        # ---- system ------------------------------------------------------
        if t in ("system", "system info", "status report", "diagnostics"):
            return (f"Platform: {platform.system()} {platform.release()} | "
                    f"Python: {platform.python_version()} | "
                    f"Node: {platform.node()} | "
                    f"Backend: {self.cfg['backend']}:{self.cfg['model']}")

        if t in ("cpu", "cpu count", "cores"):
            return f"CPU cores available: {os.cpu_count()}"

        if t in ("hostname", "machine name"):
            return f"Hostname: {socket.gethostname()}"

        if t in ("ip", "my ip", "local ip", "what's my ip"):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                return f"Local IP: {ip}"
            except Exception as e:
                return f"Could not determine IP: {e}"

        if t in ("public ip", "external ip", "wan ip"):
            try:
                ip = urllib.request.urlopen(
                    "https://api.ipify.org", timeout=5
                ).read().decode()
                return f"Public IP: {ip}"
            except Exception as e:
                return f"Could not fetch public IP: {e}"

        if t in ("disk", "disk space", "storage"):
            try:
                total, used, free = shutil.disk_usage(os.path.expanduser("~"))
                gb = 1024 ** 3
                return (f"Disk: {used / gb:.1f} GB used / "
                        f"{total / gb:.1f} GB total "
                        f"({free / gb:.1f} GB free)")
            except Exception as e:
                return f"Disk info unavailable: {e}"

        if t in ("uptime", "boot time"):
            try:
                if platform.system() == "Windows":
                    return "Uptime not available on Windows without psutil."
                with open("/proc/uptime") as f:
                    secs = float(f.read().split()[0])
                h, rem = divmod(int(secs), 3600)
                m, s = divmod(rem, 60)
                return f"System uptime: {h}h {m}m {s}s"
            except Exception as e:
                return f"Uptime unavailable: {e}"

        if t in ("cwd", "working directory"):
            return f"Working directory: {os.getcwd()}"

        if t.startswith("whoami") or t == "user":
            return f"User: {os.getenv('USER') or os.getenv('USERNAME') or 'unknown'}"

        if t in ("processes", "top processes"):
            try:
                if platform.system() == "Windows":
                    out = subprocess.check_output(
                        ["tasklist"], text=True, timeout=5)
                else:
                    out = subprocess.check_output(
                        ["ps", "-eo", "pid,comm,%cpu", "--sort=-%cpu"],
                        text=True, timeout=5)
                return "Top processes:\n" + "\n".join(out.splitlines()[:12])
            except Exception as e:
                return f"Could not list processes: {e}"

        # ---- memory ------------------------------------------------------
        if t.startswith("remember "):
            body = raw[9:].strip()
            if "=" in body:
                k, v = body.split("=", 1)
            elif " is " in body:
                k, v = body.split(" is ", 1)
            else:
                return "Format it as: remember <key> = <value>"
            self.memory[k.strip().lower()] = v.strip()
            return f"Committed to memory: {k.strip()}."

        if t.startswith("recall ") or t.startswith("what is my "):
            k = t.replace("recall ", "").replace("what is my ", "").strip()
            if k in self.memory:
                return f"{k} = {self.memory[k]}"
            return f"No record of '{k}'."

        if t in ("memory", "list memory", "what do you remember"):
            if not self.memory:
                return "Memory is empty."
            return "Stored:\n" + "\n".join(
                f"  {k} = {v}" for k, v in self.memory.items())

        if t.startswith("forget "):
            k = t[7:].strip()
            if k in self.memory:
                del self.memory[k]
                return f"Forgotten: {k}"
            return f"Nothing stored under '{k}'."

        if t in ("clear memory", "wipe memory"):
            self.memory.clear()
            return "Memory wiped."

        if t in ("reset conversation", "new conversation", "clear chat"):
            self.history = [{"role": "system", "content": self.cfg["system_prompt"]}]
            return "Conversation history cleared."

        # ---- web ---------------------------------------------------------
        # ---- web ---------------------------------------------------------

    # -- LLM backends -------------------------------------------------------
    def _llm(self, text):
        if requests is None:
            return ("The 'requests' library is missing. "
                    "Install it with: pip install requests")

        self.history.append({"role": "user", "content": text})
        if len(self.history) > 21:
            self.history = [self.history[0]] + self.history[-20:]

        backend = self.cfg["backend"]

        if backend == "ollama":
            r = requests.post(
                self.cfg["ollama_url"],
                json={"model": self.cfg["model"],
                      "messages": self.history,
                      "stream": False},
                timeout=180,
            )
            r.raise_for_status()
            content = r.json()["message"]["content"]

        elif backend == "openai":
            key = self.cfg["openai_api_key"]
            if not key:
                return ("No OpenAI API key set. Export OPENAI_API_KEY "
                        "or use the ollama backend.")
            r = requests.post(
                self.cfg["openai_url"],
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": self.cfg["model"], "messages": self.history},
                timeout=180,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]

        else:
            return ("Offline mode. I can handle: time, date, system, "
                    "remember <k> = <v>, recall <k>, open <site>, "
                    "term <cmd>, ls, cat, and more. Type 'help'.")

        self.history.append({"role": "assistant", "content": content})
        return content.strip()

    def reset(self):
        self.history = [{"role": "system", "content": self.cfg["system_prompt"]}]