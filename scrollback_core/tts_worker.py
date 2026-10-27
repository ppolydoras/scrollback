"""Piper worker. Runs under the Python that has piper-tts installed (usually
the Scrollback TTS venv), keeps one voice loaded and answers JSON requests.

Protocol: one JSON object per line on stdin. Each reply is one line on
stdout that starts with "@@ " followed by JSON. Anything else on stdout is
ignored by the server.

Request:  {"voice": "/path/en_GB-cori-medium.onnx", "text": "...", "out": "/path/x.wav",
           "length_scale": 1.0}
Reply:    {"ok": true, "seconds": 12.3} or {"ok": false, "error": "..."}
"""
import json
import os
import sys
import wave


def reply(obj):
    sys.stdout.write("@@ " + json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    try:
        from piper import PiperVoice
    except Exception as err:  # not installed in this Python
        reply({"ready": False, "error": f"piper-tts is not installed for {sys.executable}: {err}"})
        return
    try:
        from piper import SynthesisConfig
    except ImportError:  # piper-tts before 1.3
        SynthesisConfig = None
    try:
        from importlib.metadata import version
        piper_version = version("piper-tts")
    except Exception:
        piper_version = "unknown"
    reply({"ready": True, "version": piper_version})

    loaded_path, voice = None, None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "ping":
                reply({"ok": True})
                continue
            path = req["voice"]
            if path != loaded_path:
                voice = None  # free the previous model first
                voice = PiperVoice.load(path)
                loaded_path = path
            tmp = req["out"] + ".part"
            length_scale = req.get("length_scale")
            wav = wave.open(tmp, "wb")
            try:
                if hasattr(voice, "synthesize_wav"):
                    cfg = SynthesisConfig(length_scale=length_scale) if SynthesisConfig else None
                    voice.synthesize_wav(req["text"], wav, syn_config=cfg)
                else:
                    voice.synthesize(req["text"], wav, length_scale=length_scale, sentence_silence=0.25)
            except BaseException:
                try:
                    wav.close()
                except Exception:
                    pass  # an empty file cannot be closed cleanly; the real error matters
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            wav.close()
            with wave.open(tmp, "rb") as wav:
                seconds = wav.getnframes() / float(wav.getframerate() or 1)
            os.replace(tmp, req["out"])
            reply({"ok": True, "seconds": round(seconds, 2)})
        except Exception as err:
            reply({"ok": False, "error": f"{type(err).__name__}: {err}"})


if __name__ == "__main__":
    main()
