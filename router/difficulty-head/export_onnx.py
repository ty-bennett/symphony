"""Export the difficulty head to fp16 ONNX with feature standardization folded into the first layer, check it
against the PyTorch head with ONNX Runtime on 100 cached feature vectors, and write a JSON sidecar.
  difficulty.onnx: hidden [d_model] -> d (one float; higher = harder; the tier cutoff is applied by the router)"""
import numpy as np
import onnxruntime as ort
import torch

from common import CFG, load_last, load_tok_data, path, read_json, sha256_file, write_json
from train import MLP

OPSET = CFG["deploy"]["onnx_opset"]


class DifficultyGraph(torch.nn.Module):
    def __init__(self, h):
        super().__init__()
        net = MLP(h["d_model"])
        net.load_state_dict(h["state_dict"])
        first = net[0]
        with torch.no_grad():  # fold (x - mean) / std into Linear 1
            first.bias -= first.weight @ (h["mean"] / h["std"])
            first.weight /= h["std"][None, :]
        self.net = torch.nn.Sequential(net[0], net[1], net[3])  # dropout is identity at inference

    def forward(self, hidden):
        return self.net(hidden[None, :])[0, 0]


def ref_difficulty(h, X):
    net = MLP(h["d_model"])
    net.load_state_dict(h["state_dict"])
    with torch.no_grad():
        return net.eval()((torch.as_tensor(X, dtype=torch.float32) - h["mean"]) / h["std"]).squeeze(-1).numpy()


def export(h, x, f):
    torch.onnx.export(DifficultyGraph(h).half().eval(), (torch.as_tensor(x).half(),), str(f), opset_version=OPSET,
                      input_names=["hidden"], output_names=["d"], dynamo=False)


def check(h, X, f):
    """ONNX Runtime fp16 vs PyTorch head (explicit standardization, fp32) on cached vectors."""
    s = ort.InferenceSession(str(f))
    got = np.array([s.run(None, {"hidden": x.astype(np.float16)})[0] for x in X], dtype=np.float32)
    ref = ref_difficulty(h, X)
    err = float(np.abs(got - ref).max())
    assert err <= 1e-2 * max(1.0, float(np.abs(ref).max())), f"ONNX mismatch: max abs err {err}"
    return err


def main():
    out = path("out", "onnx")
    out.mkdir(parents=True, exist_ok=True)
    h = torch.load(path("out", "heads", "difficulty.pt"), weights_only=False)
    split = load_tok_data()[0].split.values
    rows, X = load_last(h["layer"])
    X = np.asarray(X[split[rows] == "val"][:100], dtype=np.float32)
    f = out / "difficulty.onnx"
    export(h, X[0], f)
    err = check(h, X, f)
    print(f"ONNX Runtime vs PyTorch: max abs err {err:.2e}")
    write_json(out / "heads.json", read_json(path("out", "heads", "version.json")) | dict(
        L_d=h["layer"], d_model=h["d_model"], onnx_opset=OPSET, deploy=CFG["deploy"],
        input=dict(name="hidden", shape=[h["d_model"]], dtype="float16",
                   source="output of decoder layer L_d (residual stream, 0-based) at the last prompt token "
                          "(end of the assistant-start prefix, thinking off)"),
        output=dict(name="d", shape=[], dtype="float16", meaning="standardized IRT difficulty; higher = harder"),
        files={f.name: sha256_file(f)}, onnx_check_max_abs_err=err))


if __name__ == "__main__":
    main()
