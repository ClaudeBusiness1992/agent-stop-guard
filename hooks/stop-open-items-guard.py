#!/usr/bin/env python3
"""Stop-Hook: blockiert das Stoppen, wenn die letzte Assistant-Nachricht
offene/unerledigte Punkte ankündigt, statt sie abzuarbeiten.

Max. MAX_BLOCKS Blockaden pro Session als Endlosschleifen-Schutz.
"""
import datetime
import json
import os
import re
import sys

# Budget für die weiche Textregel. Die harte Task-Regel hat ein eigenes,
# deutlich größeres Budget (siehe main) - offene Arbeit wiegt schwerer als
# eine unglücklich formulierte Antwort.
MAX_BLOCKS = 8

# Konkrete Statusaussagen, die einer behaupteten Vollständigkeit widersprechen.
# Sie sind bewusst KEIN eigenständiger Umsetzungsauftrag: In einem angeforderten
# Audit dürfen offene Befunde genannt werden, ohne dass der Audit dadurch eine
# Freigabe zu ihrer Behebung erteilt.
OPEN_STATUS_MARKERS = re.compile(
    r"(noch offen|offene punkte|offener punkt|steht noch aus|stehen noch aus"
    # Abschlussberichte nennen Restarbeit oft als Statuszahl oder mit einem
    # eingeschobenen Adverb (z. B. "7 offen" / "bleibt ehrlich offen").
    # Diese Formen wurden am 01.08.2026 von einer Claude-Hauptsession trotz
    # sieben unerledigter Befunde verwendet und vom Guard nicht erkannt.
    r"|\b[1-9]\d*\s+(?:(?:befunde?|punkte?|aufgaben?)\s+)?"
    r"(?:weiterhin\s+)?offen\b"
    r"|\b[1-9]\d*\s+teilweise\b"
    r"|\b(?:ein|eine|der|die|dieser|diese|\d+)\s+"
    r"(?:punkt|punkte|befund|befunde|aufgabe|aufgaben)\b.{0,60}"
    r"\b(?:bleibt|bleiben|ist|sind)\b.{0,30}\boffen\b"
    r"|unver[aä]ndert offen|nicht behoben|teilweise behoben"
    r"|offen f[üu]r (?:den |die |das )?(?:n[äa]chsten|weiteren) "
    r"(?:ausbau|schritt|durchgang|phase)"
    r"|(?:fehlt|fehlen|bleibt|bleiben) noch\b"
    r"|noch (?:lokal(?:e[rnms]?)?\s+)?umzusetzen\b"
    r"|konnte(?:n)? .{0,120}\bnoch nicht\b"
    r"|fertig (?:sind|ist) nur\b"
    r"|(?:wird|werden)\b.{0,80}\bnoch\b.{0,60}"
    r"(?:umgesetzt|konsolidiert|gebaut|implementiert|gepr[üu]ft|getestet)\b"
    r"|remaining (?:tasks|items|work)|still open"
    r"|not yet (done|complete|implemented)"
    r")",
    re.IGNORECASE,
)

# Eindeutige Ich-Ankündigungen sind dagegen echte Fortsetzungsversprechen.
# Der Guard darf sie stoppen, weil die Session ihre eigene unmittelbar
# angekündigte Handlung noch im laufenden Turn ausführen muss.
WORK_PROMISE_MARKERS = re.compile(
    r"(was ich (jetzt|noch|gleich|danach) (angehe|mache|umsetze|vorhabe)"
    # (Lücke vom 31.07.: Session endete mit "Ich schaue kurz nach" /
    # "Ich arbeite jetzt an X weiter" und stoppte trotzdem)
    r"|ich (schaue|gucke|pr[üu]fe|checke|teste) (kurz|mal|gleich|jetzt|mir das|nach)"
    r"|ich (arbeite|mache) (jetzt|gleich|direkt|nun|sofort) .{0,60}weiter"
    r"|ich (?:mache|arbeite) weiter\b"
    r"|ich arbeite .{0,80}\bjetzt ab\b"
    r"|ich (?:repariere|behebe|korrigiere|ersetze|wiederhole|pr[üu]fe|teste) "
    r"(?:zuerst|jetzt|gleich|direkt|nun|als n[äa]chstes)"
    r"|ich (beginne|starte|fange|lege) (jetzt|gleich|nun|direkt|sofort)"
    r"|ich setze .{0,40}fort"
    r"|ich k[üu]mmere mich (jetzt|gleich|sofort|darum|dann)"
    r"|(i'?ll|i will|let me) (check|take a (quick )?look|look into|get started"
    r"|continue|proceed|now))",
    re.IGNORECASE,
)

# Ein konkreter lokaler Restpunkt darf nicht durch einen externen Blocker
# verdeckt werden. Diese enge Teilmenge gilt auch bei BLOCKED_ON_USER.
LOCAL_UNFINISHED_MARKERS = re.compile(
    r"(noch lokal(?:e[rnms]?)?\s+umzusetzen\b"
    r"|ich konnte(?:n)? .{0,120}\bnoch nicht\b"
    r"|fertig (?:sind|ist) nur\b)",
    re.IGNORECASE,
)

QUOTED_EXAMPLES = re.compile(
    r"`[^`\n]*`|„[^“\n]*“|“[^”\n]*”|\"[^\"\n]*\""
)


def has_open_work_marker(text):
    """Erkennt Statuswidersprüche und echte eigene Arbeitsankündigungen."""
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return (
        OPEN_STATUS_MARKERS.search(without_examples) is not None
        or WORK_PROMISE_MARKERS.search(without_examples) is not None
    )


def has_open_status_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return OPEN_STATUS_MARKERS.search(without_examples) is not None


def has_work_promise(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return WORK_PROMISE_MARKERS.search(without_examples) is not None


def has_local_unfinished_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return LOCAL_UNFINISHED_MARKERS.search(without_examples) is not None


COMPLETION_ATTESTATION = re.compile(
    r"(?:^|\n)\s*(?:AUFTRAG VOLLSTÄNDIG ERLEDIGT|BLOCKED_ON_USER:\s*\S)",
    re.IGNORECASE,
)


def has_completion_attestation(text):
    return COMPLETION_ATTESTATION.search(text) is not None


FULL_COMPLETION_ATTESTATION = re.compile(
    r"(?:^|\n)\s*AUFTRAG VOLLSTÄNDIG ERLEDIGT\s*(?:$|\n)",
    re.IGNORECASE,
)


def has_full_completion_attestation(text):
    return FULL_COMPLETION_ATTESTATION.search(text) is not None


BLOCKED_ATTESTATION = re.compile(
    r"(?:^|\n)\s*BLOCKED_ON_USER:\s*\S", re.IGNORECASE
)


def has_blocked_attestation(text):
    return BLOCKED_ATTESTATION.search(text) is not None


def last_assistant_text(transcript_path):
    text = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content") or []
                parts = [
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                if parts:
                    text = "\n".join(parts)
    except OSError:
        return None
    return text


def _is_real_user_message(entry):
    """Erkennt Nutzerprompts, aber keine als user serialisierten Tool-Ergebnisse."""
    if entry.get("type") == "response_item":
        payload = entry.get("payload") or {}
        return payload.get("type") == "message" and payload.get("role") == "user"
    message = entry.get("message") or {}
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict) and item.get("type") in ("text", "input_text")
        for item in content
    )


def _is_tool_activity(entry):
    """Providerneutrale Erkennung von Codex- und Claude-Toolaufrufen."""
    if entry.get("type") == "response_item":
        payload = entry.get("payload") or {}
        return payload.get("type") in (
            "custom_tool_call",
            "function_call",
            "local_shell_call",
        )
    message = entry.get("message") or {}
    if message.get("role") != "assistant":
        return False
    content = message.get("content") or []
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "tool_use"
        for item in content
    )


def turn_has_tool_activity(transcript_path):
    """Prüft nur den aktuellen Nutzerdurchgang auf tatsächliche Arbeit.

    Nach jedem echten Nutzerprompt wird der vorherige Turn verworfen. Dadurch
    erzwingt der Guard den Audit nur für Arbeits-Turns und nicht für reinen Chat.
    """
    has_tool_activity = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _is_real_user_message(entry):
                    has_tool_activity = False
                    continue
                if _is_tool_activity(entry):
                    has_tool_activity = True
    except OSError:
        return False
    return has_tool_activity


PLAN_FIELD = re.compile(
    r'(?P<key>"?(?:step|status)"?)\s*:\s*'
    r'(?:(?:"(?P<double>(?:\\.|[^"\\])*)")|'
    r"(?:'(?P<single>(?:\\.|[^'\\])*)'))",
    re.IGNORECASE,
)


def _decode_plan_field(match):
    value = match.group("double")
    if value is not None:
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value
    return (match.group("single") or "").replace("\\'", "'")


def _plan_items_from_input(raw):
    """Extrahiert step/status-Paare aus JSON- oder JS-update_plan-Aufrufen."""
    items = []
    current_step = None
    for match in PLAN_FIELD.finditer(raw):
        key = match.group("key").strip('"').lower()
        value = _decode_plan_field(match)
        if key == "step":
            current_step = value
        elif key == "status" and current_step is not None:
            items.append({"step": current_step, "status": value.lower()})
            current_step = None
    return items


def codex_open_plan_items(transcript_path):
    """Liest den jüngsten Codex-update_plan-Stand aus dem Transkript.

    Codex speichert update_plan aktuell als JavaScript in einem exec-Toolcall.
    Der Plan bleibt über Nutzerturns hinweg gültig, bis ein neuer update_plan-
    Aufruf ihn ersetzt.
    """
    latest = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload") or {}
                if payload.get("type") not in ("custom_tool_call", "function_call"):
                    continue
                raw = payload.get("input") or payload.get("arguments") or ""
                if not isinstance(raw, str):
                    raw = json.dumps(raw, ensure_ascii=False)
                name = str(payload.get("name") or "").lower()
                if not (
                    "update_plan" in name
                    or "tools.update_plan" in raw
                    or "functions.update_plan" in raw
                ):
                    continue
                # Tests und Wartung des Guards enthalten absichtlich
                # update_plan-Fixtures, sind aber kein Arbeitsplan der Session.
                if any(
                    token in raw
                    for token in (
                        "agent-stop-guard/",
                        "stop-open-items-guard.py",
                        "test_stop_open_items_guard.py",
                    )
                ):
                    continue
                parsed = _plan_items_from_input(raw)
                if parsed:
                    latest = parsed
    except OSError:
        return []
    return [item for item in latest if item["status"] in ("pending", "in_progress")]


def task_blocked_on_user(task):
    """Nur sauber geparkte pending-Tasks gelten als echte Nutzerblockade."""
    if task.get("status") != "pending":
        return False
    description = str(task.get("description") or "")
    return re.search(r"(?:^|\n)\s*BLOCKED_ON_USER:\s*\S", description, re.I) is not None


def plan_item_blocked_on_user(item):
    """Codex hat kein description-Feld; der Blocker steht daher im Step."""
    return (
        item.get("status") == "pending"
        and re.match(r"\s*BLOCKED_ON_USER:\s*\S", str(item.get("step") or ""), re.I)
        is not None
    )


def last_assistant_phase(transcript_path):
    """Liefert bei Codex die letzte sichtbare Assistant-Phase des Turnverlaufs."""
    phase = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _is_real_user_message(entry):
                    phase = None
                    continue
                if entry.get("type") == "response_item":
                    payload = entry.get("payload") or {}
                    if payload.get("type") == "message" and payload.get("role") == "assistant":
                        phase = payload.get("phase") or phase
                elif entry.get("type") == "event_msg":
                    payload = entry.get("payload") or {}
                    if payload.get("type") == "agent_message":
                        phase = payload.get("phase") or phase
    except OSError:
        return None
    return phase


TASKS_ROOT = os.path.expanduser(
    os.environ.get("CLAUDE_TASKS_ROOT", "~/.claude/tasks")
)
STATE_ROOT = os.path.expanduser(
    os.environ.get("AGENT_STOP_GUARD_STATE_DIR", "~/.local/state/agent-stop-guard")
)
LOG_PATH = os.path.join(STATE_ROOT, "guard.log")
# Notausgang für den Fall, dass die Session wirklich nicht weiterkann.
KILL_SWITCH = os.path.join(STATE_ROOT, "disabled")


def safe_session_id(session_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)


def ensure_state_root():
    try:
        os.makedirs(STATE_ROOT, mode=0o700, exist_ok=True)
        os.chmod(STATE_ROOT, 0o700)
    except OSError:
        pass


def audit_pending_path(session_id):
    return os.path.join(STATE_ROOT, f"{safe_session_id(session_id)}-audit-pending")


def audit_pending(session_id):
    return os.path.exists(audit_pending_path(session_id))


def set_audit_pending(session_id):
    try:
        ensure_state_root()
        with open(audit_pending_path(session_id), "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().isoformat(timespec="seconds"))
    except OSError:
        pass


def clear_audit_pending(session_id):
    try:
        os.remove(audit_pending_path(session_id))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def log(session_id, decision, detail):
    try:
        ensure_state_root()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
                f"{session_id}\t{decision}\t{detail}\n"
            )
    except OSError:
        pass


def open_tasks(session_id):
    """Offene Tasks der Session — harte Signalquelle, unabhängig vom Text.

    Eine Antwort kann harmlos klingen und trotzdem Arbeit offen lassen;
    genau daran können längere Sessions trotz klarer Restarbeit hängenbleiben.
    """
    directory = os.path.join(TASKS_ROOT, session_id)
    if not os.path.isdir(directory):
        return []
    found = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                task = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if task.get("status") in ("pending", "in_progress"):
            found.append(task)
    found.sort(key=lambda item: str(item.get("id", "")))
    return found


def block(session_id, reason, detail, limit, kind="text"):
    # Getrennte Zähler je Regel: die harte Task-Regel darf das Budget der
    # weichen Textregel nicht aufbrauchen und umgekehrt.
    suffix = "" if kind == "text" else f"-{kind}"
    ensure_state_root()
    counter_file = os.path.join(
        STATE_ROOT, f"{safe_session_id(session_id)}-blocks{suffix}"
    )
    try:
        with open(counter_file, encoding="utf-8") as f:
            count = int(f.read().strip() or "0")
    except (OSError, ValueError):
        count = 0
    if count >= limit:
        log(session_id, "durchgelassen", f"Budget {limit} erschöpft: {detail}")
        return False
    try:
        with open(counter_file, "w", encoding="utf-8") as f:
            f.write(str(count + 1))
    except OSError:
        pass
    print(json.dumps({"decision": "block", "reason": reason}))
    log(session_id, f"blockiert({count + 1}/{limit})", detail)
    return True


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    session_id = payload.get("session_id") or "unknown"
    if os.path.exists(KILL_SWITCH):
        log(session_id, "aus", f"Notausgang {KILL_SWITCH} aktiv")
        return

    # Codex liefert die letzte Antwort direkt mit; Claude Code nur den Transcript-Pfad
    text = payload.get("last_assistant_message")
    if not text:
        transcript_path = payload.get("transcript_path")
        if transcript_path:
            text = last_assistant_text(transcript_path)
    text = text or ""

    transcript_path = payload.get("transcript_path")
    attested = has_completion_attestation(text)
    blocked_attested = has_blocked_attestation(text)

    # Eine Commentary-Nachricht ist per Definition ein Zwischenstand. Codex
    # darf einen Turn niemals dort beenden, unabhängig davon, welche Verben die
    # Nachricht verwendet oder ob in diesem kurzen Fortsetzungsturn schon ein
    # Werkzeug lief.
    if transcript_path and last_assistant_phase(transcript_path) == "commentary":
        reason = (
            "ZWISCHENNACHRICHT IST KEIN ABSCHLUSS: Dein letzter sichtbarer "
            "Beitrag hat die Codex-Phase 'commentary'. Führe die darin "
            "angekündigte Arbeit JETZT aus. Beende den Turn erst nach der "
            "tatsächlichen Umsetzung und Verifikation mit einer finalen "
            "Abschlussnachricht."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        log(session_id, "blockiert(commentary)", "Turn endete auf Commentary-Phase")
        return

    # Berichte ÜBER den Guard selbst (zitieren die Marker-Phrasen) nicht blocken
    guard_report = "stop-guard" in text.lower()

    # 1. Harte Regel: offene Tasks. Zählt unabhängig vom Antworttext und
    #    bekommt ein großes Budget - solange Arbeit offen ist, wird gearbeitet.
    pending = open_tasks(session_id)
    actionable_tasks = [task for task in pending if not task_blocked_on_user(task)]
    parked_tasks = [task for task in pending if task_blocked_on_user(task)]
    if actionable_tasks:
        listing = "\n".join(
            f"- #{task.get('id')} [{task.get('status')}] {task.get('subject')}"
            for task in actionable_tasks[:12]
        )
        more = (
            f"\n… und {len(actionable_tasks) - 12} weitere"
            if len(actionable_tasks) > 12 else ""
        )
        reason = (
            f"STOPP VERWEIGERT: {len(actionable_tasks)} selbstständig "
            f"bearbeitbare Aufgabe(n) dieser Session sind "
            f"noch offen:\n{listing}{more}\n\n"
            "Arbeite sie JETZT der Reihe nach ab, ohne Zwischenfrage. "
            "Setze jede erledigte Aufgabe per TaskUpdate auf completed. "
            "Wenn eine Aufgabe wirklich blockiert ist (nur die nutzende Person kann sie "
            "lösen), setze sie auf pending und beschreibe im description-Feld "
            "den Blocker mit einer eigenen Zeile 'BLOCKED_ON_USER: <Beleg>' — "
            "und arbeite alle übrigen Aufgaben trotzdem weiter ab. Eine "
            "Teilblockade beendet niemals den ganzen Lauf."
        )
        if block(
            session_id, reason, f"{len(actionable_tasks)} offene Tasks", 25, kind="tasks"
        ):
            return

    # Codex besitzt kein persistentes Claude-Task-Verzeichnis. Sein jüngster
    # update_plan-Aufruf ist deshalb das harte strukturierte Signal. Ohne
    # diese Prüfung kann ein lokaler in_progress-Punkt neben einem externen
    # API-Blocker fälschlich als vollständig blockiert erscheinen.
    plan_pending = codex_open_plan_items(transcript_path) if transcript_path else []
    actionable_plan = [
        item for item in plan_pending if not plan_item_blocked_on_user(item)
    ]
    parked_plan = [item for item in plan_pending if plan_item_blocked_on_user(item)]
    if actionable_plan:
        listing = "\n".join(
            f"- [{item['status']}] {item['step']}" for item in actionable_plan[:12]
        )
        more = (
            f"\n… und {len(actionable_plan) - 12} weitere"
            if len(actionable_plan) > 12 else ""
        )
        reason = (
            f"STOPP VERWEIGERT: Der aktuelle Codex-Plan enthält "
            f"{len(actionable_plan)} selbstständig bearbeitbare offene "
            f"Aufgabe(n):\n{listing}{more}\n\n"
            "Arbeite alle unabhängigen Punkte JETZT ab und aktualisiere den "
            "Plan. Nur ein tatsächlich von der nutzenden Person abhängiger Punkt darf pending "
            "bleiben; beginne dessen Step dafür exakt mit "
            "'BLOCKED_ON_USER: <konkreter Input und Beleg>'. Ein allgemeines "
            "BLOCKED_ON_USER im Abschluss verdeckt keinen lokalen pending- "
            "oder in_progress-Punkt."
        )
        if block(
            session_id,
            reason,
            f"{len(actionable_plan)} offene Codex-Planpunkte",
            25,
            kind="plans",
        ):
            return

    if (parked_tasks or parked_plan) and not blocked_attested:
        reason = (
            "BLOCKER-BESTÄTIGUNG FEHLT: Strukturierte Tasks beziehungsweise "
            "Planpunkte sind mit 'BLOCKED_ON_USER:' geparkt. Beende den Turn "
            "erst nach Abarbeitung aller unabhängigen Punkte und nenne dann "
            "den konkret benötigten Nutzerinput mit rohem Beleg in einer "
            "eigenen Abschlusszeile 'BLOCKED_ON_USER: ...'."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        log(session_id, "blockiert(blocker-attestierung)", "geparkter Blocker ohne Abschlusszeile")
        return

    # 2. Weiche Textregel mit Scope-Schutz. Reine Befunde, Empfehlungen,
    # Optionen und spätere Ausbauschritte sind kein Umsetzungsauftrag. Geblockt
    # werden nur eigene unmittelbare Arbeitsversprechen, ein konkreter lokaler
    # Rest neben BLOCKED_ON_USER oder ein Widerspruch zur Vollständigkeitszeile.
    text_requires_continuation = bool(
        text
        and (
            has_work_promise(text)
            or (blocked_attested and has_local_unfinished_marker(text))
            or (
                has_full_completion_attestation(text)
                and has_open_status_marker(text)
            )
        )
    )
    if text_requires_continuation and not guard_report:
        # Eine blockierte Arbeitsankündigung muss zugleich die harte
        # Audit-Sperre setzen. Andernfalls kann der automatisch fortgesetzte
        # Stopversuch (`stop_hook_active=true`) mit einer bloßen Aufzählung
        # offener Punkte durchrutschen, weil die erste Rückgabe den regulären
        # Abschlussaudit darunter noch nicht erreicht hat.
        set_audit_pending(session_id)
        reason = (
            "Deine letzte Antwort enthält ein eigenes unmittelbares "
            "Arbeitsversprechen oder widerspricht ihrer strukturierten "
            "Abschlusszeile. Prüfe ausschließlich den ORIGINALAUFTRAG und führe "
            "nur darin bereits beauftragte Restarbeit aus. Der Hook erweitert "
            "den Nutzerauftrag und deine Schreibberechtigung ausdrücklich nicht: "
            "Empfehlungen, Optionen, Auditbefunde, mögliche spätere Schritte "
            "und nicht beauftragte Verbesserungen dürfen nicht allein wegen "
            "dieser Meldung umgesetzt werden. War nur Analyse oder Beratung "
            "beauftragt, korrigiere den Abschlussbericht statt Dateien oder "
            "Systemzustand zu verändern."
        )
        if block(session_id, reason, "Ankündigungsmuster im Text", MAX_BLOCKS):
            return

    # 3. Abschlussaudit für echte Arbeits-Turns. Worterkennung allein lässt
    #    sich durch einen glatt formulierten Teilabschluss umgehen. Sobald in
    #    diesem Nutzerdurchgang Werkzeuge liefen, verweigern Claude und Codex
    #    deshalb einen Audit, wenn noch keine strukturierte, selbst geprüfte
    #    Abschlusszeile vorliegt. Eine bereits attestierte Antwort wird nicht
    #    zu einem zweiten identischen Werkzeug-/Auditdurchgang gezwungen.
    has_tool_activity = bool(
        transcript_path and turn_has_tool_activity(transcript_path)
    )
    pending_audit = audit_pending(session_id)

    # Ein echter neuer Nutzerturn darf eine verwaiste Sperre aus einem zuvor
    # abgebrochenen Audit ersetzen. Innerhalb der automatisch erzeugten
    # Fortsetzung ist `stop_hook_active` dagegen wahr und die Sperre bleibt.
    if pending_audit and not payload.get("stop_hook_active", False):
        clear_audit_pending(session_id)
        pending_audit = False

    if pending_audit and not has_tool_activity and not attested:
        reason = (
            "AUDIT NOCH NICHT AUSGEFÜHRT: Seit der erzwungenen Fortsetzung "
            "wurde kein Werkzeug benutzt. Eine Ankündigung wie 'ich repariere "
            "zuerst' ist keine Fortsetzung der Arbeit. Führe JETZT den nächsten "
            "konkreten Werkzeugschritt aus und arbeite den Originalauftrag bis "
            "zum verifizierten Ergebnis weiter ab."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        log(session_id, "blockiert(audit-offen)", "kein Werkzeug seit Audit")
        return

    if (
        has_tool_activity
        and not payload.get("stop_hook_active", False)
        and not pending_audit
        and not attested
    ):
        reason = (
            "ABSCHLUSSAUDIT ERFORDERLICH: In diesem Turn wurden Werkzeuge "
            "verwendet. Prüfe den ORIGINALAUFTRAG jetzt Punkt für Punkt gegen "
            "den tatsächlichen Stand. Erfasse jede noch offene Arbeit als Task "
            "und erledige alle selbstständig bearbeitbaren Punkte sofort. "
            "Prüfe die relevanten Tests/Laufzeitnachweise. Antworte erst danach "
            "abschließend. Eine bloße Wiederholung des bisherigen Berichts oder "
            "das Umbenennen von Restarbeit in 'nächster Ausbau' besteht diesen "
            "Audit nicht. Der abschließende Bericht muss nach dem Audit eine "
            "eigene Zeile 'AUFTRAG VOLLSTÄNDIG ERLEDIGT' enthalten. Falls "
            "wirklich jede Restarbeit extern blockiert ist, verwende stattdessen "
            "'BLOCKED_ON_USER: <konkreter benötigter Input mit Beleg>'. Der "
            "Audit bezieht sich ausschließlich auf den erteilten "
            "Originalauftrag. Er autorisiert keine Umsetzung bloßer "
            "Empfehlungen, Optionen oder zusätzlich entdeckter Verbesserungen."
        )
        set_audit_pending(session_id)
        print(json.dumps({"decision": "block", "reason": reason}))
        log(session_id, "blockiert(audit)", "erster Stop nach Tool-Aktivität")
        return


    if pending_audit and has_tool_activity and not attested:
        reason = (
            "ABSCHLUSSBESTÄTIGUNG FEHLT: Der Werkzeuglauf nach dem Audit ist "
            "vorhanden, aber der Bericht bestätigt den Originalauftrag nicht "
            "strukturiert. Prüfe alle Akzeptanzpunkte erneut. Wenn alles erledigt "
            "ist, sende den belegten Abschluss mit einer eigenen Zeile "
            "'AUFTRAG VOLLSTÄNDIG ERLEDIGT'. Bei einem vollständigen externen "
            "Blocker verwende 'BLOCKED_ON_USER: <konkreter Input mit Beleg>'. "
            "Restarbeit und eine Vollständigkeitsbestätigung dürfen nicht "
            "gleichzeitig vorkommen."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        log(session_id, "blockiert(attestierung)", "Werkzeuglauf ohne Abschlussbestätigung")
        return

    if pending_audit and attested:
        clear_audit_pending(session_id)
        log(session_id, "audit-erfüllt", "strukturierte Abschlussbestätigung erkannt")

    log(session_id, "durchgelassen", "keine offenen Tasks, kein Muster")


if __name__ == "__main__":
    main()
