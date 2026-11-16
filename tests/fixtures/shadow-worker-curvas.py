"""Worker viejo: solo entiende ``curvas``. Sirve para probar la degradación sin número."""
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
if job.get("job") != "curvas":
    sys.stdout.write(json.dumps({"ok": False, "error": "job desconocido: %r" % job.get("job")}) + "\n")
    sys.exit(0)

assert job.get("wav") and job.get("ref"), job
sys.stdout.write(json.dumps({
    "ok": True,
    "mio": {"f0": curve(0.0), "dur": 1.0, "med": 120.0},
    "ref": {"f0": curve(1.4), "dur": 1.1, "med": 140.0},
}) + "\n")
