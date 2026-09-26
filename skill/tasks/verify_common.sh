#!/usr/bin/env bash
# Gemeinsamer Verifier: kopiert das Arbeitsverzeichnis, legt die versteckten Tests
# daneben, führt alle Tests aus und schreibt verify.json.
# Aufruf: verify_common.sh <task_dir> <workdir> <out.json>
set -u
TASK_DIR="$1"; WORK="$2"; OUT="$3"
TMP="$(mktemp -d)"
cp -a "$WORK/." "$TMP/"
cp "$TASK_DIR"/hidden/*.py "$TMP/"
cd "$TMP"
python3 - "$OUT" <<'PY'
import json, sys, unittest, io
out = sys.argv[1]
def run(pattern):
    suite = unittest.defaultTestLoader.discover(".", pattern=pattern)
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return r.testsRun, r.testsRun - len(r.failures) - len(r.errors)
h_run, h_ok = run("test_hidden*.py")
v_run, v_ok = run("test_[!h]*.py")
score = (h_ok / h_run) if h_run else 0.0
res = {"passed": h_run > 0 and h_ok == h_run, "score": round(score, 4),
       "checks": {"hidden_total": h_run, "hidden_ok": h_ok, "own_tests_total": v_run, "own_tests_ok": v_ok,
                  "own_tests_present": v_run > 0}}
json.dump(res, open(out, "w"), indent=2)
print(json.dumps(res))
PY
rc=$?
rm -rf "$TMP"
exit $rc
