"""Test: standalone/sync_env.py - reports missing/stale/unknown settings,
never prints secret values, never changes existing lines, backs up before
appending, and is a no-op once .env is complete."""
import contextlib
import io
import sys
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

sys.path.insert(0, str(_harness.ROOT / "standalone"))
import sync_env

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

d = Path(_harness.tmp("syncenv"))
d.mkdir(exist_ok=True)
for old in d.glob(".env*"):
    old.unlink()
example = d / ".env.example"
env = d / ".env"
example.write_text(
    "# Keys\nGROQ_API_KEY=\n\n"
    "# Chat model\nGROQ_CHAT_MODEL=qwen/qwen3.8-27b\n\n"
    "# Language hint for Whisper\n# (second comment line)\nSTT_LANGUAGE=en\n\n"
    "# Gate settings, a group of two\nSPEECH_GATE_ENABLED=true\nSPEECH_GATE_MIN_SPEECH_MS=150\n"
    "# directly after a setting - must not attach to the gate group\nLOG_LEVEL=INFO\n",
    encoding="utf-8",
)
original = ("GROQ_API_KEY=gsk_TOPSECRET_abc123\nGROQ_CHAT_MODEL=llama-3.3-70b-versatile\n"
            "END_TURN_AFTER_FINAL_SAY=true\nMY_TYPO=1\n")
env.write_text(original, encoding="utf-8")

def run(*extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sync_env.main(["--env", str(env), "--example", str(example), *extra])
    return code, out.getvalue()

code, out = run()
check("report: lists the missing settings", all(k in out for k in ("STT_LANGUAGE=en", "SPEECH_GATE_ENABLED=true", "SPEECH_GATE_MIN_SPEECH_MS=150", "LOG_LEVEL=INFO")))
check("report: flags the removed Groq model", "STALE  GROQ_CHAT_MODEL=llama-3.3-70b-versatile" in out)
check("report: flags the replaced setting", "OLD    END_TURN_AFTER_FINAL_SAY" in out)
check("report: unknown setting listed by name", "MY_TYPO" in out)
check("report: secret value never printed", "TOPSECRET" not in out)
check("report: nothing changed on disk", env.read_text(encoding="utf-8") == original)
check("report: exit 1 when something needs attention", code == 1)

code, out = run("--apply", "--yes")
text = env.read_text(encoding="utf-8")
backups = list(d.glob(".env.bak-*"))
check("apply: existing lines untouched (file starts with the original)", text.startswith(original))
check("apply: stale value NOT rewritten", "GROQ_CHAT_MODEL=llama-3.3-70b-versatile" in text and text.count("GROQ_CHAT_MODEL=") == 1)
check("apply: backup saved and equals the original", len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original)
added = text[len(original):]
check("apply: missing settings appended with their comments",
      "# Language hint for Whisper\n# (second comment line)\nSTT_LANGUAGE=en" in added and "SPEECH_GATE_MIN_SPEECH_MS=150" in added)
check("apply: a group's shared comment written once", added.count("# Gate settings, a group of two") == 1)
check("apply: comment right after a setting stays with the next setting only",
      "# directly after a setting - must not attach to the gate group\nLOG_LEVEL=INFO" in added
      and added.index("# directly after") > added.index("SPEECH_GATE_MIN_SPEECH_MS=150"))

code, out = run()
check("after apply: nothing missing", "has every setting in .env.example" in out)
code, out = run("--apply", "--yes")
check("second apply is a no-op (no duplicate lines, no new backup)",
      env.read_text(encoding="utf-8") == text and len(list(d.glob(".env.bak-*"))) == 1)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
