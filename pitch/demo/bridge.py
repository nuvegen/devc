#!/usr/bin/env python3
"""Gestellte Unterhaltung für die Bildschirmfotos der Präsentation.

Spricht das Chat-Protokoll von devc wie die echte Bridge des Addons claude-code, ruft aber kein Modell auf:
Auf jede Nachricht folgt derselbe feste Ablauf. Nur für `pitch/screenshots.py`.
"""
import json
import sys
import time

SESSION = "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a1b2c"


def emit(kind, **fields):
    sys.stdout.write(json.dumps({"type": kind, **fields}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def tool(number, name, summary, output):
    emit("status", state="working", text=f"{name}: {summary}")
    emit("tool_start", id=f"t{number}", name=name, summary=summary, input=json.dumps({"eingabe": summary}, ensure_ascii=False, indent=2))
    time.sleep(0.2)
    emit("tool_end", id=f"t{number}", ok=True, output=output)


def answer():
    emit("text", text="Ich lasse zuerst die Tests laufen.")
    tool(1, "Bash", "python3 -m unittest -q", "FAIL: test_rabatt_ab_100_euro\nAssertionError: 119.0 != 107.1\n\nRan 8 tests in 0.004s\nFAILED (failures=1)")
    emit("text", text="Ein Test schlägt fehl: Der Rabatt ab 100 € wird nicht abgezogen. In `brutto()` steht der Vergleich falsch herum.")
    tool(2, "Edit", "/work/webshop/src/preise.py", "1 Zeile geändert")
    tool(3, "Bash", "python3 -m unittest -q", "Ran 8 tests in 0.004s\n\nOK")
    emit("text", text="Erledigt. Alle 8 Tests laufen wieder.\n\n"
                      "| Was | Vorher | Nachher |\n|---|---|---|\n| Vergleich in `brutto()` | `netto < 100` | `netto >= 100` |\n| Tests | 7 von 8 | 8 von 8 |\n\n"
                      "Die Änderung liegt in `src/preise.py` und ist noch nicht committet. Du siehst sie in der Kachel **Git**.")
    emit("turn_end", ok=True, duration_ms=41000)
    emit("status", state="idle")


def main():
    if "--sessions" in sys.argv:
        print(json.dumps({"sessions": []}))
        return
    if "--show" in sys.argv or "--stats" in sys.argv:
        return
    sys.stdin.readline()
    emit("ready", agent="Claude Code", version="2.1.287", model="claude-opus-5-5", session=SESSION,
         commands=[{"name": "model", "hint": "<name>", "description": "Modell wählen"}, {"name": "clear", "hint": "", "description": "Verlauf leeren"}])
    emit("status", state="idle")
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if command.get("type") == "send":
            emit("user", text=command["text"])
            emit("status", state="working", text="denkt nach")
            answer()
        elif command.get("type") == "end":
            break


if __name__ == "__main__":
    main()
