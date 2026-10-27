"""Builds a tiny stand-in Piper voice for testing: real Piper code runs end to
end, but the "model" only outputs a tone whose length follows the text.

Needs piper-tts, onnx and numpy in the Python that runs it:
    <tts-venv>/bin/python tests/make_test_voice.py OUTPUT_DIR
"""
import json
import math
import sys
from pathlib import Path

import onnx
from onnx import TensorProto, helper
from piper.phoneme_ids import DEFAULT_PHONEME_ID_MAP

KEY = "en_GB-testtone-medium"


def build(out_dir: Path) -> Path:
    c = lambda name, value, dtype=TensorProto.INT64: helper.make_node(  # noqa: E731
        "Constant", [], [name], value=helper.make_tensor(name + "_v", dtype, [], [value]))
    nodes = [
        helper.make_node("Shape", ["input"], ["shape"]),
        c("one_i", 1),
        helper.make_node("Gather", ["shape", "one_i"], ["n"], axis=0),
        c("hop", 256),
        helper.make_node("Mul", ["n", "hop"], ["t0"]),
        helper.make_node("Cast", ["t0"], ["t0f"], to=TensorProto.FLOAT),
        helper.make_node("Gather", ["scales", "one_i"], ["ls"], axis=0),
        helper.make_node("Mul", ["t0f", "ls"], ["tf"]),
        helper.make_node("Cast", ["tf"], ["t"], to=TensorProto.INT64),
        c("zero_i", 0),
        helper.make_node("Range", ["zero_i", "t", "one_i"], ["r"]),
        helper.make_node("Cast", ["r"], ["rf"], to=TensorProto.FLOAT),
        c("w", 2 * math.pi * 220 / 22050, TensorProto.FLOAT),
        helper.make_node("Mul", ["rf", "w"], ["phase"]),
        helper.make_node("Sin", ["phase"], ["s"]),
        c("amp", 0.3, TensorProto.FLOAT),
        helper.make_node("Mul", ["s", "amp"], ["a"]),
        helper.make_node("Constant", [], ["shape4"],
                         value=helper.make_tensor("shape4_v", TensorProto.INT64, [4], [1, 1, 1, -1])),
        helper.make_node("Reshape", ["a", "shape4"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes, "testtone",
        [helper.make_tensor_value_info("input", TensorProto.INT64, [1, None]),
         helper.make_tensor_value_info("input_lengths", TensorProto.INT64, [1]),
         helper.make_tensor_value_info("scales", TensorProto.FLOAT, [3])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1, 1, None])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{KEY}.onnx"
    onnx.save(model, str(path))
    cfg = {
        "audio": {"sample_rate": 22050},
        "espeak": {"voice": "en-gb-x-rp"},
        "inference": {"noise_scale": 0.667, "length_scale": 1.0, "noise_w": 0.8},
        "phoneme_type": "espeak",
        "phoneme_id_map": DEFAULT_PHONEME_ID_MAP,
        "num_symbols": 256,
        "num_speakers": 1,
        "piper_version": "test",
    }
    Path(str(path) + ".json").write_text(json.dumps(cfg))
    return path


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
