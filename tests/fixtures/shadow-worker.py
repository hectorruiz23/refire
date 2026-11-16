"""Worker simulado de shadowing: mismo contrato, sin torch ni librosa."""
import json
import math
import sys


def curve(phase):
    out = []
    for i in range(120):
        if i % 23 == 0:
            out.append(None)
        else:
            out.append(round(3.2 * math.sin(i / 9.0 + phase), 2))
    return out


line = sys.stdin.readline()
job = json.loads(line)
assert job.get("job") in ("shadow", "curvas"), job
assert job.get("wav") and job.get("ref"), job

payload = {
    "ok": True,
    "mio": {"f0": curve(0.0), "dur": 1.0, "med": 120.0},
    "ref": {"f0": curve(1.4), "dur": 1.1, "med": 140.0},
}
if job.get("job") == "shadow":
    assert job.get("text"), job
    payload.update({
        "score": 77.0,
        "per": 0.1765,
        "sonidos_bien": 82,
        "transcrito": "EVEN IF IT HAS SIDE EFFECTS WE SHALL STILL SHIP IT",
        "fonemas": [
            {"palabra": "should", "de": "ʊd", "a": "ul"},
            {"palabra": "ship", "de": "ʃɪp", "a": "ʃip"},
        ],
    })
sys.stdout.write(json.dumps(payload) + "\n")
