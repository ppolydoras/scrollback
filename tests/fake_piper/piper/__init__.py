"""A stand-in for piper-tts used by the offline tests. Writes a short silent WAV."""
import json


class SynthesisConfig:
    def __init__(self, length_scale=None, **kwargs):
        self.length_scale = length_scale


class PiperVoice:
    def __init__(self, rate):
        self.rate = rate

    @staticmethod
    def load(model_path, config_path=None, **kwargs):
        with open(config_path or f"{model_path}.json", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return PiperVoice(cfg["audio"]["sample_rate"])

    def synthesize_wav(self, text, wav_file, syn_config=None, set_wav_format=True):
        if "FAIL" in text:
            raise RuntimeError("asked to fail")
        scale = (syn_config.length_scale if syn_config and syn_config.length_scale else 1.0)
        frames = int(len(text) * 0.06 * self.rate * scale)
        wav_file.setframerate(self.rate)
        wav_file.setsampwidth(2)
        wav_file.setnchannels(1)
        wav_file.writeframes(b"\x00\x00" * frames)
