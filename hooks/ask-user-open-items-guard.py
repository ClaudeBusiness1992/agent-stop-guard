#!/usr/bin/env python3
"""PreToolUse-Hook fuer AskUserQuestion.

Eine Rueckfrage darf einen mehrstufigen Auftrag erst dann anhalten, wenn alle
noch offenen Tasks nachweislich auf die nutzende Person warten. Solange ein unabhaengiger
Task bearbeitbar ist, wird die Frage abgewiesen und die Arbeit geht weiter.
"""

import datetime
import hashlib
import json
import os
import re
import sys


TASKS_ROOT = os.path.expanduser("~/.claude/tasks")
STATE_ROOT = os.path.expanduser("~/.local/state/claude-ask-user-guard")
LOG_PATH = os.path.join(STATE_ROOT, "ask-user-guard.log")
# Ab so vielen erfolglosen Abweisungen MIT UNVERAENDERTER Blockadelage gilt der
# Agent als festgefahren und die Rueckfrage wird durchgelassen. Jede echte
# Aenderung (Task markiert, abgearbeitet, neu angelegt) setzt den Zaehler
# zurueck, weil sie den Fingerabdruck aendert.
DEADLOCK_ATTEMPTS = 3
BLOCKED_MARKER = re.compile(r"(?im)^\s*BLOCKED_ON_USER\s*:")
OPEN_TEXT_MARKERS = re.compile(
    r"(offene?\s+(?:punkte|befunde|tasks|aufgaben)"
    r"|\b[1-9]\d*\s+(?:(?:punkte|befunde|tasks|aufgaben)\s+)?offen\b"
    r"|\b(?:weiter|naechste schritte|nächste schritte)\s*:"
    r"|\brestliche[nr]?\s+(?:punkte|befunde|tasks|aufgaben)\b"
    r"|\b(?:noch nicht|ausstehend|verbleibend)\b)",
    re.IGNORECASE,
)
# Gegengewicht zum groben tool_count-Ersatzbeleg: eine lange Session ist kein
# Beweis fuer offene Arbeit. Sagt der letzte Text erkennbar das Gegenteil,
# darf die blosse Laenge die Rueckfrage nicht mehr abweisen.
DONE_TEXT_MARKERS = re.compile(
    r"(\bfertig\b|\berledigt\b|\babgeschlossen\b|\bverifiziert\b|\bbehoben\b"
    r"|\bumgesetzt\b|\binstalliert\b|\btests? gr[üu]n\b|\bnichts (?:mehr )?offen\b)",
    re.IGNORECASE,
)


def log(session_id, decision, detail):
    try:
        os.makedirs(STATE_ROOT, mode=0o700, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(
                f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
                f"{session_id}\t{decision}\t{detail}\n"
            )
    except OSError:
        pass


def open_tasks(session_id):
    directory = os.path.join(TASKS_ROOT, session_id)
    if not os.path.isdir(directory):
        return []
    tasks = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                task = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if task.get("status") in ("pending", "in_progress"):
            tasks.append(task)
    tasks.sort(key=lambda item: str(item.get("id", "")))
    return tasks


def last_assistant_text(transcript_path):
    text = ""
    if not transcript_path:
        return text
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content") or []
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                if parts:
                    text = "\n".join(parts)
    except OSError:
        return ""
    return text


def transcript_tool_count(transcript_path, limit=6):
    """Kleiner Ersatzbeleg fuer vergessene Tasklisten in langen Sessions."""
    if not transcript_path:
        return 0
    count = 0
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content") or []
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    if item.get("name") == "AskUserQuestion":
                        continue
                    count += 1
                    if count >= limit:
                        return count
    except OSError:
        return 0
    return count


def waits_for_user(task):
    return bool(BLOCKED_MARKER.search(str(task.get("description", ""))))


def deadlock_attempts(session_id, fingerprint):
    """Zaehlt aufeinanderfolgende Abweisungen mit identischer Blockadelage.

    Ohne diesen Zaehler kann ein Agent dauerhaft feststecken: die Rueckfrage
    ist gesperrt, aber die verbleibende Arbeit haengt tatsaechlich an der
    nutzenden Person.
    """
    safe_session_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    path = os.path.join(STATE_ROOT, f"{safe_session_id}.json")
    previous = {}
    try:
        with open(path, encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, json.JSONDecodeError):
        previous = {}
    count = (
        int(previous.get("count", 0)) + 1
        if previous.get("fingerprint") == fingerprint
        else 1
    )
    try:
        os.makedirs(STATE_ROOT, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"fingerprint": fingerprint, "count": count}, handle)
    except OSError:
        pass
    return count


def clear_deadlock_state(session_id):
    safe_session_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    try:
        os.remove(os.path.join(STATE_ROOT, f"{safe_session_id}.json"))
    except OSError:
        pass


def deny(session_id, reason, detail, fingerprint):
    """Weist ab - es sei denn, dieselbe Lage wurde schon mehrfach abgewiesen."""
    attempts = deadlock_attempts(session_id, fingerprint)
    if attempts >= DEADLOCK_ATTEMPTS:
        log(
            session_id,
            "zugelassen",
            f"Zirkelbrecher nach {attempts} identischen Abweisungen ({detail})",
        )
        return {}
    log(session_id, "abgewiesen", f"{detail}; Versuch {attempts}")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "systemMessage": reason,
    }


def evaluate(payload):
    if payload.get("tool_name") != "AskUserQuestion":
        return {}

    session_id = payload.get("session_id") or "unknown"
    pending = open_tasks(session_id)
    actionable = [task for task in pending if not waits_for_user(task)]

    if not pending:
        assistant_text = last_assistant_text(payload.get("transcript_path"))
        tool_count = transcript_tool_count(payload.get("transcript_path"))
        says_open = bool(OPEN_TEXT_MARKERS.search(assistant_text))
        # Die blosse Laenge einer Session beweist keine offene Arbeit. Nennt
        # der letzte Text die Arbeit erkennbar abgeschlossen, darf der
        # Ersatzbeleg nicht mehr allein abweisen.
        says_done = bool(DONE_TEXT_MARKERS.search(assistant_text))
        if says_open or (tool_count >= 6 and not says_done):
            reason = (
                "RUECKFRAGE NOCH NICHT ZULASSEN: Die bisherige Session zeigt "
                "mehrstufige oder weitere offene Arbeit, hat aber keine offene "
                "Taskliste. Lege die mehrstufige Arbeit jetzt als Tasks an. "
                "Markiere nur wirklich von der nutzenden Person abhaengige Tasks im "
                "description-Feld mit 'BLOCKED_ON_USER:' und arbeite danach "
                "alle unabhaengigen Tasks zuerst ab. Frage erst, wenn kein "
                "anderer Task mehr bearbeitbar ist."
            )
            return deny(
                session_id,
                reason,
                f"mehrstufige Arbeit ohne Taskliste; tool_count={tool_count}",
                "keine-taskliste",
            )
        clear_deadlock_state(session_id)
        log(session_id, "zugelassen", "keine Taskliste und kein Offen-Signal")
        return {}

    if not actionable:
        clear_deadlock_state(session_id)
        log(
            session_id,
            "zugelassen",
            f"{len(pending)} offene Tasks, alle explizit auf Nutzer wartend",
        )
        return {}

    listing = "\n".join(
        f"- #{task.get('id')} [{task.get('status')}] {task.get('subject')}"
        for task in actionable[:12]
    )
    more = (
        f"\n… und {len(actionable) - 12} weitere"
        if len(actionable) > 12
        else ""
    )
    reason = (
        f"RUECKFRAGE NOCH NICHT ZULASSEN: {len(actionable)} bearbeitbare "
        f"Aufgabe(n) sind offen:\n{listing}{more}\n\n"
        "Pruefe zuerst, welche Tasks ohne Rueckfrage fortgesetzt werden koennen, und "
        "arbeite sie jetzt ab. Tasks, die wirklich eine Entscheidung oder "
        "Handlung brauchen, bleiben pending/in_progress und erhalten im "
        "description-Feld eine eigene Zeile mit 'BLOCKED_ON_USER:' plus dem "
        "konkreten benoetigten Input. Stelle die Rueckfrage erst, wenn kein "
        "anderer Task mehr bearbeitbar ist. Eine Teilblockade stoppt niemals "
        "die gesamte To-do-Liste."
    )
    fingerprint = hashlib.sha256(
        "|".join(str(task.get("id")) for task in actionable).encode("utf-8")
    ).hexdigest()[:16]
    return deny(
        session_id,
        reason,
        f"{len(actionable)} bearbeitbare Tasks",
        fingerprint,
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    print(json.dumps(evaluate(payload)))


if __name__ == "__main__":
    main()
