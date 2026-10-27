"""Text to speech with Piper, run as a separate worker process.

Scrollback itself stays standard-library only. Piper lives in its own virtual
environment (created by scripts/setup-tts.sh) and Scrollback talks to it through
scrollback_core/tts_worker.py over a pipe. Audio is cached as WAV files.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import HOMEPAGE, __version__, config, db
from .speech import speech_text

log = logging.getLogger("scrollback.tts")

WORKER = Path(__file__).resolve().parent / "tts_worker.py"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES_JSON = f"{HF_BASE}/voices.json?download=true"
VOICE_RX = re.compile(r"^(?P<family>[a-z]{2,3})_(?P<region>[A-Z]{2})-(?P<name>[a-z0-9_]+)-(?P<quality>x_low|low|medium|high)$")
DEFAULT_VOICE = "en_GB-cori-medium"
CACHE_MAX = 500 * 1024 * 1024
CACHE_KEEP = 400 * 1024 * 1024
IDLE_SECONDS = 600
SYNTH_TIMEOUT = 180

# Used when the voice list on Hugging Face cannot be reached.
FALLBACK_VOICES = [
    ("en_GB-cori-medium", "Cori, British English, female"),
    ("en_GB-cori-high", "Cori, British English, female, higher quality"),
    ("en_GB-alan-medium", "Alan, British English, male"),
    ("en_GB-northern_english_male-medium", "Northern English, male"),
    ("en_GB-jenny_dioco-medium", "Jenny, British English, female"),
    ("en_GB-alba-medium", "Alba, Scottish English, female"),
    ("en_US-lessac-medium", "Lessac, American English, female"),
    ("en_US-ryan-high", "Ryan, American English, male"),
]


def voices_dir() -> Path:
    path = config.data_dir() / "voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def audio_dir() -> Path:
    path = config.data_dir() / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def venv_python() -> Path:
    return config.data_dir() / "tts-venv" / "bin" / "python"


def installed_voices() -> list[str]:
    out = []
    for onnx in sorted(voices_dir().glob("*.onnx")):
        if Path(str(onnx) + ".json").exists():
            out.append(onnx.stem)
    return out


def voice_label(key: str) -> str:
    m = VOICE_RX.match(key)
    if not m:
        return key
    name = m.group("name").replace("_", " ").title()
    return f"{name} ({m.group('family')}-{m.group('region')}, {m.group('quality').replace('_', ' ')})"


class Engine:
    def __init__(self) -> None:
        self.lock = threading.Lock()          # one synthesis at a time
        self.proc: subprocess.Popen | None = None
        self.proc_python: str | None = None
        self.piper_version: str | None = None
        self.last_used = 0.0
        self.last_error: str | None = None
        self._python_checked: tuple[float, str | None, str | None] = (0.0, None, None)
        self.jobs: queue.Queue[int] = queue.Queue()
        self.queued: set[int] = set()
        self.download = {"voice": None, "done": 0, "total": 0, "error": None, "active": False}
        self._catalog: tuple[float, list] = (0.0, [])
        self._buf = b""
        threading.Thread(target=self._prefetch_loop, name="tts-prefetch", daemon=True).start()
        threading.Thread(target=self._idle_loop, name="tts-idle", daemon=True).start()

    # -------------------------------------------------------------- discovery

    def find_python(self, force: bool = False) -> tuple[str | None, str | None]:
        """Return (python path, reason if none). Cached for 30 seconds."""
        checked_at, py, reason = self._python_checked
        if not force and time.time() - checked_at < 30:
            return py, reason
        candidates = []
        env = os.environ.get("SCROLLBACK_PIPER_PYTHON")
        if env:
            candidates.append(env)
        candidates += [str(venv_python()), sys.executable]
        py, reason = None, "Piper is not installed. Run scripts/setup-tts.sh once."
        for cand in candidates:
            if not cand or not Path(cand).exists():
                continue
            try:
                ok = subprocess.run([cand, "-c", "import piper"], capture_output=True, timeout=20).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                py, reason = cand, None
                break
        self._python_checked = (time.time(), py, reason)
        return py, reason

    def current_voice(self) -> str | None:
        voices = installed_voices()
        if not voices:
            return None
        chosen = db.get_setting("tts_voice")
        if chosen in voices:
            return chosen
        family = (db.get_setting("language") or "en").split("-")[0]
        for prefix in (f"{family}_GB", f"{family}_"):
            for v in voices:
                if v.startswith(prefix):
                    return v
        return voices[0]

    def speed(self) -> float:
        try:
            return min(1.6, max(0.6, float(db.get_setting("tts_speed") or 1.0)))
        except ValueError:
            return 1.0

    def status(self) -> dict:
        py, reason = self.find_python()
        voice = self.current_voice()
        if py and not voice:
            reason = "No voice installed yet. Pick one in Settings and download it."
        return {
            "available": bool(py and voice),
            "reason": reason,
            "python": py,
            "piper_version": self.piper_version,
            "voices": [{"key": v, "label": voice_label(v)} for v in installed_voices()],
            "voice": voice,
            "speed": self.speed(),
            "worker_running": self.proc is not None and self.proc.poll() is None,
            "last_error": self.last_error,
            "download": dict(self.download),
        }

    # -------------------------------------------------------------- worker process

    def _read_reply(self, timeout: float) -> dict:
        assert self.proc and self.proc.stdout
        fd = self.proc.stdout.fileno()
        deadline = time.time() + timeout
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if line.startswith(b"@@ "):
                    return json.loads(line[3:].decode("utf-8"))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("Piper did not answer in time")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError("Piper stopped unexpectedly. See tts.log in the Scrollback data folder.")
            self._buf += chunk

    def _ensure_worker(self, py: str) -> None:
        if self.proc is not None and self.proc.poll() is None and self.proc_python == py:
            return
        self._stop_worker()
        logf = open(config.data_dir() / "tts.log", "ab")
        self._buf = b""
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [py, "-u", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=logf,
            cwd=str(config.data_dir()), env=env,
        )
        logf.close()
        self.proc_python = py
        hello = self._read_reply(60)
        if not hello.get("ready"):
            self._stop_worker()
            raise RuntimeError(hello.get("error") or "Piper could not start")
        self.piper_version = hello.get("version")
        log.info("Piper %s started with %s", self.piper_version, py)

    def _stop_worker(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        finally:
            if proc.stdout:
                proc.stdout.close()

    def _idle_loop(self) -> None:
        while True:
            time.sleep(30)
            if self.proc is not None and time.time() - self.last_used > IDLE_SECONDS:
                with self.lock:
                    if time.time() - self.last_used > IDLE_SECONDS:
                        log.info("stopping idle Piper worker")
                        self._stop_worker()

    def shutdown(self) -> None:
        with self.lock:
            self._stop_worker()

    # -------------------------------------------------------------- synthesis

    def _cache_path(self, voice: str, speed: float, text: str) -> Path:
        h = hashlib.sha1(f"{voice}|{speed:.2f}|{text}".encode("utf-8")).hexdigest()[:20]
        return audio_dir() / f"{h}.wav"

    def synthesize(self, text: str, voice: str | None = None, speed: float | None = None) -> Path:
        py, reason = self.find_python()
        if voice is not None and voice not in installed_voices():
            raise RuntimeError(f"The voice {voice} is not installed.")
        voice = voice or self.current_voice()
        if not py or not voice:
            raise RuntimeError(reason or "No voice installed yet. Pick one in Settings and download it.")
        speed = min(1.6, max(0.6, speed)) if speed else self.speed()
        out = self._cache_path(voice, speed, text)
        if out.exists():
            os.utime(out)
            return out
        with self.lock:
            if out.exists():
                return out
            self.last_used = time.time()
            try:
                self._ensure_worker(py)
                req = {"voice": str(voices_dir() / f"{voice}.onnx"), "text": text, "out": str(out),
                       "length_scale": round(1.0 / speed, 3)}
                assert self.proc and self.proc.stdin
                self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
                res = self._read_reply(SYNTH_TIMEOUT)
            except (OSError, TimeoutError, RuntimeError, ValueError) as err:
                self._stop_worker()
                self.last_error = str(err)
                raise RuntimeError(str(err)) from err
            finally:
                self.last_used = time.time()
            if not res.get("ok"):
                self.last_error = res.get("error")
                raise RuntimeError(res.get("error") or "Piper failed")
            self.last_error = None
        self._prune_cache()
        return out

    def audio_for_fact(self, fact_id: int) -> Path:
        fact = db.get_fact(fact_id)
        if fact is None:
            raise KeyError("no such post")
        return self.synthesize(speech_text(fact, db.get_setting("language") or "en"))

    def prefetch(self, ids: list[int]) -> None:
        if not self.status()["available"]:
            return
        for fid in ids[:6]:
            if fid not in self.queued:
                self.queued.add(fid)
                self.jobs.put(fid)

    def _prefetch_loop(self) -> None:
        while True:
            fid = self.jobs.get()
            try:
                self.audio_for_fact(fid)
            except Exception as err:
                log.debug("prefetch %s failed: %s", fid, err)
            finally:
                self.queued.discard(fid)

    def _prune_cache(self) -> None:
        files = [(p.stat().st_mtime, p.stat().st_size, p) for p in audio_dir().glob("*.wav")]
        total = sum(s for _, s, _ in files)
        if total <= CACHE_MAX:
            return
        for _, size, path in sorted(files):
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            if total <= CACHE_KEEP:
                break

    # -------------------------------------------------------------- voices

    def catalog(self) -> list[dict]:
        family = (db.get_setting("language") or "en").split("-")[0]
        installed = set(installed_voices())
        fetched_at, voices = self._catalog
        if not voices or time.time() - fetched_at > 86400:
            try:
                req = urllib.request.Request(VOICES_JSON, headers={"User-Agent": f"Scrollback/{__version__} (+{HOMEPAGE})"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                voices = []
                for key, v in data.items():
                    if v.get("num_speakers", 1) != 1 or not VOICE_RX.match(key):
                        continue
                    size = sum(f.get("size_bytes", 0) for name, f in (v.get("files") or {}).items()
                               if name.endswith(".onnx"))
                    lang = v.get("language") or {}
                    voices.append({
                        "key": key,
                        "family": lang.get("family") or key.split("_")[0],
                        "label": f"{v.get('name', key).replace('_', ' ').title()}, "
                                 f"{lang.get('name_english', '')} ({lang.get('country_english', '')}), "
                                 f"{v.get('quality', '').replace('_', ' ')}",
                        "size_mb": round(size / 1e6) if size else None,
                    })
                self._catalog = (time.time(), voices)
            except Exception as err:
                log.info("voice list not reachable: %s", err)
                voices = [{"key": k, "family": k.split("_")[0], "label": label, "size_mb": None}
                          for k, label in FALLBACK_VOICES]
                self._catalog = (time.time() - 86400 + 600, voices)  # try again in 10 minutes
        out = [dict(v, installed=v["key"] in installed) for v in voices if v["family"] == family]
        out.sort(key=lambda v: (v["key"] != DEFAULT_VOICE, not v["key"].startswith(f"{family}_GB"), v["key"]))
        return out

    def start_download(self, key: str) -> None:
        if not VOICE_RX.match(key):
            raise ValueError("voice names look like en_GB-cori-medium")
        if self.download["active"]:
            raise ValueError("another voice is downloading")
        self.download = {"voice": key, "done": 0, "total": 0, "error": None, "active": True}
        threading.Thread(target=self._download, args=(key,), name="voice-download", daemon=True).start()

    def _download(self, key: str) -> None:
        try:
            download_voice(key, progress=self._progress)
            if not db.get_setting("tts_voice"):
                db.set_setting("tts_voice", key)
        except Exception as err:
            self.download["error"] = str(err)
            log.warning("voice download failed: %s", err)
        finally:
            self.download["active"] = False

    def _progress(self, done: int, total: int) -> None:
        self.download["done"], self.download["total"] = done, total


def voice_urls(key: str) -> tuple[str, str]:
    m = VOICE_RX.match(key)
    if not m:
        raise ValueError(f"not a Piper voice name: {key}")
    fam, region, name, quality = m.group("family", "region", "name", "quality")
    base = f"{HF_BASE}/{fam}/{fam}_{region}/{name}/{quality}/{key}"
    return f"{base}.onnx?download=true", f"{base}.onnx.json?download=true"


def download_voice(key: str, progress=None) -> Path:
    model_url, config_url = voice_urls(key)
    target = voices_dir() / f"{key}.onnx"
    headers = {"User-Agent": f"Scrollback/{__version__} (+{HOMEPAGE})"}
    for url, dest in ((config_url, Path(str(target) + ".json")), (model_url, target)):
        part = Path(str(dest) + ".part")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress and dest == target:
                    progress(done, total)
        part.replace(dest)
    return target


def remove_voice(key: str) -> None:
    if not VOICE_RX.match(key):
        raise ValueError("bad voice name")
    for suffix in (".onnx", ".onnx.json"):
        p = voices_dir() / f"{key}{suffix}"
        if p.exists():
            p.unlink()


def clear_cache() -> None:
    shutil.rmtree(audio_dir(), ignore_errors=True)
