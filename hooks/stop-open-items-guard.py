#!/usr/bin/env python3
"""Stop-Hook: blockiert das Stoppen, wenn die letzte Assistant-Nachricht
offene/unerledigte Punkte ankündigt, statt sie abzuarbeiten.

Max. MAX_BLOCKS Blockaden pro Session als Endlosschleifen-Schutz.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys

# Budget für die weiche Textregel. Ein unsicheres Sprachmuster darf genau eine
# Korrektur anfordern, aber niemals eine Folge umformulierter Abschlussberichte
# erzwingen. Harte strukturierte Tasks und Pläne haben eigene Budgets.
MAX_BLOCKS = 1
MAX_STRUCTURED_BLOCKS = 1
STATE_TTL_DAYS = 30
MAX_LOG_BYTES = 1024 * 1024
LOG_RETAIN_BYTES = 512 * 1024
GENERATED_STATE_FILE = re.compile(
    r"^.+-[0-9a-f]{10}-(?:blocks(?:-[A-Za-z0-9_.-]+)?|audit-pending|follow-through-pending)$"
)

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
    r"|\bnoch nichts\b.{0,80}\b"
    r"(?:repariert|behoben|umgesetzt|ge[äa]ndert|erledigt|getestet|gefixt|gel[öo]st)\b"
    r"|\b(?:wurde|wurden|ist|sind)\b.{0,100}\b(?:noch\s+)?nicht\b.{0,50}\b"
    r"(?:repariert|behoben|umgesetzt|ge[äa]ndert|erledigt|ver[öo]ffentlicht|gefixt|gel[öo]st|verifiziert|abgeschlossen)\b"
    r"|konnte(?:n)? .{0,120}\bnoch nicht\b"
    r"|fertig (?:sind|ist) nur\b"
    r"|(?:wird|werden)\b.{0,80}\bnoch\b.{0,60}"
    r"(?:umgesetzt|konsolidiert|gebaut|implementiert|gepr[üu]ft|getestet|abgeschlossen)\b"
    r"|remaining (?:tasks|items|work)|still open"
    r"|not yet (?:done|complete|implemented|fixed|resolved|verified)"
    r")",
    re.IGNORECASE,
)

# Enges Signal dafuer, dass nicht nur ein Befund oder spaeterer Backlog
# beschrieben wird, sondern der aktuell besprochene Arbeitsumfang nach eigener
# Aussage noch nicht fertig ist. Diese Regel schliesst die Luecke, in der eine
# Session auf die Kontrollfrage "alle Aufgaben erledigt?" mit "Nein" und einer
# Restliste antwortete und trotzdem stoppte.
CURRENT_SCOPE_INCOMPLETE_MARKERS = re.compile(
    r"(\A\s*(?:nein[,;:]?\s*)?noch nicht(?:\s+(?:alles|vollst[aä]ndig|ganz|fertig|bereit|abgeschlossen|erledigt|umgesetzt|behoben|gefixt|gel[öo]st|sauber|passend|durch))?(?:[.!]|\s*$)"
    # Reale Icons-Lücke vom 17.08.2026: Ein eingeschobener Zustand kann die
    # Kopula vom Abschlussprädikat trennen ("ist damit begonnen, aber noch
    # nicht abgeschlossen"). Das ist weiterhin klar unfertiger aktueller
    # Umfang, auch ohne ein zweites "ist" unmittelbar vor "noch nicht".
    r"|\bnoch nicht\s+(?:vollst[aä]ndig|ganz|fertig|bereit|abgeschlossen|erledigt|umgesetzt|behoben|gefixt|gel[öo]st|sauber|passend|durch)\b"
    # Ein aktiver Arbeitsturn endete mit "Als Nächstes folgen die 129
    # motivischen Reparaturen". Im read-only-/Berichtsfall greift diese Regel
    # nicht, weil main sie zusätzlich an den aktiven Ausführungsscope bindet.
    r"|\bals n[aä]chstes\s+(?:folgt|folgen|kommt|kommen)\b"
    # Reale Chargenarbeit vom 17.08.2026: Nach einer vom Hook erzwungenen
    # Fortsetzung endete die Icon-Session erneut mit "99 Motivkorrekturen sind
    # noch offen". Das allgemeine Offen-Muster reichte hier nicht, weil die
    # harte aktuelle-Scope-Regel nur eine engere Teilmenge auswertet.
    r"|\b[1-9]\d*\s+[a-zäöüß][a-zäöüß0-9_-]*(?:\s+[a-zäöüß][a-zäöüß0-9_-]*){0,3}"
    r"\s+(?:ist|sind|bleibt|bleiben)\s+noch\s+offen\b"
    r"|\bdie\s+(?:[üu]brigen|restlichen|verbleibenden)\s+[1-9]\d*\s+"
    r"[a-zäöüß][a-zäöüß0-9_-]*(?:\s+[a-zäöüß][a-zäöüß0-9_-]*){0,3}\s+"
    r"(?:folgt|folgen|kommt|kommen|steht|stehen)\s+noch\b"
    r"|\b(?:vorhandene|aktuelle|beauftragte)\s+unfertige\s+"
    r"(?:[äa]nderung|umsetzung)\b"
    r"|\b(?:ist|sind|wurde|wurden)\b.{0,100}\bnoch nicht\b.{0,60}\b"
    r"(?:implementiert|einklappbar|separat\s+erkannt|umgesetzt|fertig|verifiziert|gepr[üu]ft|getestet|behoben|gefixt|gel[öo]st|abgeschlossen|korrigiert|geladen|eingerichtet|[üu]bernommen|eingebunden|bereinigt|migriert|gebaut|beendet|funktionsf[äa]hig|sauber|online|erreichbar|verbunden)\b"
    r"|\bnoch nicht\b.{0,40}\b(?:sauber|vollst[aä]ndig|richtig|korrekt)\s+(?:verifiziert|gepr[üu]ft|getestet|implementiert|umgesetzt|behoben|gefixt|gel[öo]st|konfiguriert|eingebettet|abgeschlossen)\b"
    r"|\b(?:[üu]bernimmt|[üu]bernommen|funktioniert|l[äa]dt|klappt)\b.{0,60}\bnoch nicht\b"
    r"|\b(?:noch|immer noch)\s+(?:ein\s+)?(?:restkonflikt|restproblem|restfehler|restpunkt|konflikt|problem|fehler|bug)\b"
    r"|\b(?:export|build|render\w*|test\w*)\s+(?:l[äa]uft|dauert|braucht)\s+noch\b"
    r"|\bbraucht gerade ungew[öo]hnlich lange\b"
    r"|\b(?:nein[,;:]?\s*)?noch nicht alles\b"
    r"|noch nicht (?:alle|s[aä]mtliche|vollst[aä]ndig|komplett)\s+"
    r"(?:gesamt)?aufgaben\b"
    r"|\b(?:es|hier|daf[üu]r|vor\b.{0,80})\s+fehlen noch\b"
    r"|\bfehlen noch\s*:\s*"
    r"|\b(?:auftrag|aufgabe|prozess|app|anwendung|projekt|umsetzung)\b"
    r".{0,100}\b(?:insgesamt\s+)?noch nicht\b.{0,80}"
    r"\b(?:fertig|abgeschlossen|erledigt|bereit|build[- ]?fertig|"
    r"ver[öo]ffentlichungs[- ]?fertig|release[- ]?ready)\b"
    r"|\bbleibt(?: weiterhin)?\b.{0,100}\b(?:ein|der|dieser|separater)\s+"
    r"offene[rnms]?\s+(?:punkt|aufgabe|arbeit)\b"
    r"|\bnoch nichts\b.{0,80}\b"
    r"(?:repariert|behoben|umgesetzt|ge[äa]ndert|erledigt|getestet|gefixt|gel[öo]st)\b"
    r"|\b(?:fix|auftrag|aufgabe|reparatur|umsetzung|[äa]nderung)\b"
    r".{0,100}\b(?:wurde|wurden|ist|sind)\b.{0,40}\b(?:noch\s+)?nicht\b"
    r".{0,50}\b(?:repariert|behoben|umgesetzt|erledigt|ver[öo]ffentlicht|gefixt|gel[öo]st)\b)",
    re.IGNORECASE | re.DOTALL,
)

COMPLETION_CHECK_PROMPT = re.compile(
    r"(?:\balles\b.{0,50}\b"
    r"(?:erledigt|fertig|abgeschlossen|umgesetzt|behoben|gefixt|gel[öo]st)\b"
    r"|\b(?:alle|s[aä]mtliche)\b.{0,50}\b"
    r"(?:erledigt|fertig|abgeschlossen|umgesetzt|behoben|gefixt|gel[öo]st)\b"
    r"|\b(?:ist|sind)\b.{0,40}\b(?:alles|alle|das|der|die|es)\b.{0,40}"
    r"\b(?:fertig|erledigt|abgeschlossen|umgesetzt|behoben|gefixt|gel[öo]st|bereit)\b"
    r"|\bhast\s*(?:du)?\b.{0,50}\b(?:gefixt|gefixed|behoben|gel[öo]st|repariert|erledigt|fertig|gemacht|abgeschlossen)\b"
    r"|\bbist\s*(?:du)?\b.{0,40}\b(?:fertig|durch|bereit|soweit)\b"
    r"|\b(?:wie weit bist du|wie ist der stand|wie sieht es aus|was ist der status|wie ist der status)\b"
    r"|\bkann\s*(?:ich|man)\b.{0,60}\b(?:weiterarbeiten|testen|sehen|ausprobieren|starten)\b"
    r"|\b(?:funktioniert|geht|l[äa]uft|klappt|tut)\s+(?:es|das)\s+(?:jetzt|schon)\b"
    r"|\b(?:fixt|machst)\s*du\b.{0,50}\b(?:das|fertig|weiter)\b"
    r"|\b(?:mach|machst du)\s+(?:das|bitte|deine aufgaben|deinen teil)?\s*(?:noch\s+)?fertig\b"
    r"|\bdu sollst das fertig machen\b"
    r"|\bkannst\s+(?:du\s+)?weiter\s*machen\b"
    r"|\bmach\s+(?:bitte\s+)?weiter\b"
    r"|\bnoch\b.{0,40}\b(?:aufgaben|punkte|arbeit)\b.{0,30}\boffen\b"
    r"|\b(?:fertig|erledigt|abgeschlossen|umgesetzt|behoben|gefixt|gel[öo]st)\s*\?\s*$)",
    re.IGNORECASE | re.DOTALL,
)

READ_ONLY_SCOPE_PROMPT = re.compile(
    r"\b(?:read[- ]?only|nur\s+(?:pr[üu]fen|analysieren|bewerten|ansehen|"
    r"nachsehen)|audit(?:ieren)?|review(?:en)?|analyse|bestandsaufnahme|"
    r"keine\s+(?:[äa]nderungen|umsetzung|werkzeuge)|nichts\s+"
    r"(?:[äa]ndern|umsetzen)|(?:antworte|antwort)\b.{0,80}\b"
    r"(?:ausschlie[ßs]lich|nur)\b.{0,40}\bstatus|"
    r"(?:nutze|verwende|starte)\b.{0,60}\bkeine\s+"
    r"(?:werkzeuge|agents?|prozesse))\b",
    re.IGNORECASE | re.DOTALL,
)

USER_EXECUTION_REQUEST = re.compile(
    r"\b(?:repariere|behebe|fixe|implementiere|integriere|baue|erstelle|"
    r"[äa]ndere|setze\s+um|arbeite\b.{0,40}\b(?:ab|weiter)|mach(?:e)?\b"
    r".{0,40}\b(?:fertig|weiter)|sorge\b.{0,80}\bdaf[üu]r)\b",
    re.IGNORECASE | re.DOTALL,
)

# Kurze Betriebs- und Versionsfragen prüfen nur den aktuellen Zustand. Sie
# erteilen weder eine Startfreigabe noch reaktivieren sie zuvor pausierte
# Arbeit. Strukturierte aktuelle Tasks/Pläne und offene Werkzeugläufe werden
# weiterhin unabhängig davon geprüft.
STATUS_OBSERVATION_PROMPT = re.compile(
    r"(?:\b(?:geht|l[äa]uft|funktioniert|klappt)\s+(?:es|das)\s+jetzt\b"
    r"|\b(?:welche|was\s+f[üu]r\s+eine|ist\s+die)\s+version\b"
    r"|\b(?:router|runtime|dienst|container|server)\b.{0,60}"
    r"\b(?:aktiv|gestartet|gestoppt|online|offline|erreichbar|version|status)\b"
    r"|\b(?:status|stand)\s+(?:von|des|der)\b)",
    re.IGNORECASE | re.DOTALL,
)


def user_requests_status_observation(text):
    raw = str(text or "")
    return bool(
        STATUS_OBSERVATION_PROMPT.search(raw)
        and not USER_EXECUTION_REQUEST.search(raw)
    )

# Eine ausdrücklich gewünschte Fragerunde ist ein dialogischer Auftrag: Der
# Agent erklärt genau einen Punkt, stellt genau eine Frage und wartet dann auf
# die Antwort. Ein Stop an dieser Stelle ist kein Abbruch offener Arbeit.
DIALOGUE_ROUND_ENABLE = re.compile(
    r"\b(?:"
    r"(?:geh(?:e)?|mach(?:e)?|klär(?:e)?|erklär(?:e)?)\b.{0,80}\b"
    r"(?:jeden\s+punkt\s+einzeln|punkt\s+f[üu]r\s+punkt|frage\s+f[üu]r\s+frage)"
    r"|(?:immer\s+)?(?:nur\s+)?eine\s+frage\s+(?:zur\s+zeit|pro\s+(?:nachricht|runde)|nach\s+der\s+anderen)"
    r"|nicht\s+(?:so\s+)?viele\s+(?:punkte|fragen)\s+auf\s+einmal"
    r"|(?:starte|beginne|mach(?:e)?)\b.{0,60}\bfragerunde"
    r"|fragerunde\b.{0,40}\b(?:starten|beginnen|machen)"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)
DIALOGUE_ROUND_DISABLE = re.compile(
    r"\b(?:"
    r"(?:beende|stoppe)\s+(?:jetzt\s+)?(?:die\s+)?fragerunde"
    r"|keine\s+weiteren\s+fragen"
    r"|ohne\s+(?:weitere\s+)?r[üu]ckfragen"
    r"|(?:mach|erledige|bearbeite|arbeite)\b.{0,60}\b(?:alles|den\s+rest)\s+(?:auf\s+einmal|selbstst[aä]ndig|durch|ab)"
    r"|entscheide\s+(?:den\s+rest\s+)?selbst"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)
# Eine Fragerunde ist an den laufenden Dialogauftrag gebunden, nicht an die
# gesamte Session. Ein späterer eigenständiger Arbeitsauftrag beendet sie auch
# dann, wenn Nick die alte Runde nicht mit einer besonderen Formel schließt.
# Kurze Antworten wie "A", "ja" oder "vier Stunden" treffen diese enge Regel
# bewusst nicht.
DIALOGUE_TASK_RESET = re.compile(
    r"(?:\b(?:neuer|anderer|nächster|naechster)\s+"
    r"(?:auftrag|aufgabe|thema|scope)\b"
    r"|\b(?:alle|sämtliche|saemtliche|diese|die)\b.{0,100}\b"
    r"(?:müssen|muessen|sollen)\b.{0,100}\b"
    r"(?:integriert|umgesetzt|behoben|repariert|gefixt|geprüft|geprueft|"
    r"abgeschlossen|fertig)\b"
    r"|\bbitte\s+fix(?:en|e|t)?\b"
    r"|^\s*(?:bitte\s+)?(?:implementiere|integriere|repariere|behebe|"
    r"fix(?:e|en|t)?|baue|erstelle|"
    r"ändere|aendere|prüfe|pruefe|analysiere|übernimm|uebernimm|"
    r"arbeite|sorge)\b)",
    re.IGNORECASE | re.DOTALL,
)
# In Fragerunden stehen die Auswahloptionen regelmäßig nach der Frage. Eine
# echte Frage muss daher nicht die letzte Zeile sein, braucht aber ein
# erkennbares Fragewort oder eine typische Verb-Erststellung. Ein beliebiges
# Fragezeichen in einem Zitat, Dateinamen oder Statusbericht genügt nicht.
DIRECT_QUESTION = re.compile(
    r"(?:^|[\n.!]\s*)"
    r"(?:"
    r"(?:was|wer|wen|wem|welch\w*|wie|warum|weshalb|"
    r"wo(?:mit|bei|zu|f[üu]r|gegen|durch|von|rauf|r[üu]ber|hin|her)?|wann|"
    r"wieviel|wie\s+viel|wie\s+lange)\b"
    r"|(?:soll\w*|darf\w*|kann\w*|könn\w*|koenn\w*|möcht\w*|moecht\w*|"
    r"will\w*|ist|sind|hat|haben|braucht|passt|gilt|geht|funktioniert|"
    r"should|shall|can|could|would|do|does|is|are|has|have|what|which|"
    r"how|why|where|when|who)\b"
    r")"
    r"[^?\n]{0,500}\?",
    re.IGNORECASE | re.MULTILINE,
)
QUOTED_DIRECT_QUESTION_CONTEXT = re.compile(
    r"\b(?:beantworte|entscheide|wähle|waehle|antworte)\b[^?\n]{0,120}"
    r"(?:was|wer|wen|wem|welch\w*|wie|warum|weshalb|"
    r"wo(?:mit|bei|zu|f[üu]r|gegen|durch|von|rauf|r[üu]ber|hin|her)?|wann|"
    r"soll\w*|darf\w*|kann\w*|möcht\w*|moecht\w*|will\w*|ist|sind)\b"
    r"[^?\n]{0,300}\?",
    re.IGNORECASE | re.MULTILINE,
)
USER_PROMISES_FUTURE_INPUT = re.compile(
    r"(?:"
    r"(?=.*\b(?:gleich|sobald|nachher|bald)\b)"
    r"(?=.*\b(?:workflow|entwurf|bereich|text|datei|pfad|info(?:rmation)?|"
    r"input|antwort)\b)"
    r"(?=.*\b(?:schick\w*|send\w*|liefer\w*|bekomm\w*|reich\w*|geb\w*)\b)"
    r"|(?=.*\b(?:lasse|lass)\b.{0,80}\b(?:testen|pr[üu]fen|gegenchecken)\b)"
    r"(?=.*\b(?:melde\s+mich|gebe\s+bescheid|schicke\s+das\s+ergebnis)\b)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
DIALOGUE_RESOLUTION_MARKERS = re.compile(
    r"\b(?:fragerunde|entscheidung(?:en)?|punkte?)\b.{0,100}\b"
    r"(?:abgeschlossen|gekl[äa]rt|beantwortet|zur\s+(?:getrennten\s+)?"
    r"umsetzung\s+bereit)\b|\bkeine\s+weitere\s+frage\b",
    re.IGNORECASE | re.DOTALL,
)


def dialogue_round_active(transcript_path):
    """Liest die jüngste taskgebundene Fragerunden-Anweisung der Session.

    Kurze Antworten wie "vier Stunden" oder "weiter" beenden den Modus nicht.
    Eine ausdrückliche Gegenanweisung oder ein erkennbar neuer Arbeitsauftrag
    hebt ihn auf. Damit kann eine alte Fragerunde keine spätere Task blockieren.
    """
    active = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    not _is_real_user_message(entry)
                    or _is_automatic_user_message(entry)
                ):
                    continue
                user_text = _user_message_text(entry)
                if DIALOGUE_ROUND_DISABLE.search(user_text):
                    active = False
                elif active and DIALOGUE_TASK_RESET.search(user_text):
                    active = False
                elif DIALOGUE_ROUND_ENABLE.search(user_text):
                    active = True
    except OSError:
        return False
    return active


def asks_direct_question(text):
    raw = str(text or "")
    if QUOTED_DIRECT_QUESTION_CONTEXT.search(raw):
        return True
    without_examples = QUOTED_EXAMPLES.sub("", raw)
    return DIRECT_QUESTION.search(without_examples) is not None


def user_promises_future_input(text):
    return USER_PROMISES_FUTURE_INPUT.search(str(text or "")) is not None


def dialogue_round_resolved(text):
    return DIALOGUE_RESOLUTION_MARKERS.search(str(text or "")) is not None

# Eigenmächtiges Abbrechen oder Vertagen ist nur zusammen mit einem bereits
# belegten Arbeitslauf ein Fortsetzungssignal. Dadurch bleiben reine
# Statusfragen und Read-only-Befunde ohne Änderungsauftrag unbeeinträchtigt.
EXECUTION_DEFERRAL_MARKERS = re.compile(
    r"(?:\bich\s+[äa]ndere\s+(?:jetzt\s+)?bewusst\s+nichts\b"
    r"|\bich\s+nehme\s+(?:jetzt\s+)?bewusst\s+keine\s+[äa]nderungen\s+vor\b"
    r"|\b(?:nach|ab)\s+\d{1,2}(?::\d{2})?\s*uhr\b.{0,100}\b"
    r"(?:erg[aä]nzt|implementiert|umgesetzt|getestet|aktiviert)\b)",
    re.IGNORECASE | re.DOTALL,
)

# Eindeutige Ich-Ankündigungen sind dagegen echte Fortsetzungsversprechen.
# Der Guard darf sie stoppen, weil die Session ihre eigene unmittelbar
# angekündigte Handlung noch im laufenden Turn ausführen muss.
WORK_PROMISE_MARKERS = re.compile(
    r"(was ich (jetzt|noch|gleich|danach) (angehe|mache|umsetze|vorhabe)"
    # (Lücke vom 31.07.: Session endete mit "Ich schaue kurz nach" /
    # "Ich arbeite jetzt an X weiter" und stoppte trotzdem)
    r"|ich (schaue|gucke|pr[üu]fe|checke|teste) (kurz|mal|gleich|jetzt|mir das|nach)"
    # Auch trennbare Konstruktionen wie "Ich schaue mir jetzt gezielt X an"
    # sind unmittelbare Arbeitsversprechen und dürfen nicht idle enden.
    r"|ich (?:schaue|gucke|pr[üu]fe|checke|teste) mir "
    r"(?:(?:jetzt|gleich|nun|direkt|kurz|mal)\s+)?[^\n.!?]{0,120}\b(?:an|nach)\b"
    r"|ich (arbeite|mache) (jetzt|gleich|direkt|nun|sofort) .{0,60}weiter"
    r"|ich (?:mache|arbeite) weiter\b"
    r"|ich arbeite .{0,80}\bjetzt\b.{0,40}\bab\b"
    # Lücke vom 08.08.2026: Ein Arbeitsversprechen braucht kein Adverb, und die
    # deutsche Trennung schiebt die Vorsilbe ans Satzende — beliebig weit weg
    # vom Verb. "Ich arbeite die der Reihe nach ab", "Solange mache ich die
    # Reparaturen fertig" und "Ich mache mit der Zeilenausrichtung weiter"
    # beendeten vier Turns, ohne dass der Guard anschlug. Deshalb wird hier das
    # Muster Verb + beliebiges Mittelfeld + Vorsilbe erfasst statt einzelner
    # Formulierungen. Verneinungen im Mittelfeld schließen den Treffer aus.
    r"|ich (?:mache|arbeite|gehe|fahre|setze|bringe|ziehe|hole|f[üu]hre"
    r"|schlie[ßs]e|warte|starte|baue|r[äa]ume)\s"
    r"(?:(?!\bnicht\b|\bkein)[^\n]){0,80}"
    r"\b(?:weiter|fort|fertig|ab(?!\s+und\s+zu\b)|durch|zu ende|neu|auf|weg)\b"
    r"|\b(?:mach|mache)\s+ich(?:\s+(?:jetzt|gleich|sofort|direkt|nun))?(?:[.!]|\s*$|\s*,\s*ich\b)"
    r"|\b(?:wird|werde ich)\s+(?:jetzt|gleich|sofort|direkt|nun)\s+(?:gemacht|erledigt|umgesetzt)\b"
    r"|\bich\s+(?:muss|werde|sollte)\s+(?:das|es|die\s+aufgabe|den\s+punkt|nun|jetzt|noch|gleich|direkt|sofort)?\s*[^\n.!?]{0,80}\b"
    r"(?:abschlie[ßs]en|umsetzen|machen|beheben|reparieren|l[öo]sen|fixen|anpassen|pr[üu]fen|testen|fertigstellen|erledigen|einrichten|starten|bereinigen|bauen)\b"
    r"|\bich\s+(?:schlie[ßs]e|passe|richte|erg[äa]nze|[üu]bernehme|starte|baue|ziehe|gleiche|r[äa]ume|binde|beseitige|behebe|korrigiere|optimiere|verifiziere|exportiere|bereinige|aktualisiere|migriere|installiere)\s+"
    r"(?:(?!\bnicht\b|\bkein)[^\n.!?]){0,60}\b"
    r"(?:jetzt|nun|gleich|direkt|sofort|noch|neu|ab|an|ein|aus|weg|auf|dorthin|heran|separat|um)\b"
    # Vorangestelltes Objekt kehrt die Wortstellung um ("Das prüfe ich").
    # Verneinungen sind ausgenommen — "das mache ich nicht" ist eine Absage,
    # kein Fortsetzungsversprechen.
    r"|(?:^|[.!?;:]\s|\n|—\s|,\s)"
    r"(?:das|die|den|dies|dieses|solange|danach|anschlie[ßs]end|zuerst|erst|dann)"
    r"\b[^\n.!?]{0,60}\b"
    r"(?:mache|pr[üu]fe|teste|baue|erledige|repariere|kl[äa]re|schaue|arbeite|hole|[üu]bernehme|starte|schlie[ßs]e|fixe)"
    r"\s+ich\b(?!\s*(?:nicht|nie|ungern|kaum))"
    r"|ich (?:repariere|behebe|korrigiere|ersetze|wiederhole|pr[üu]fe|teste|baue|starte|[üu]bernehme|erg[äa]nze|schlie[ßs]e) "
    r"(?:zuerst|jetzt|gleich|direkt|nun|als n[äa]chstes|danach|noch)"
    r"|ich (beginne|starte|fange|lege) (jetzt|gleich|nun|direkt|sofort)"
    r"|ich setze .{0,40}fort"
    r"|ich k[üu]mmere mich (jetzt|gleich|sofort|darum|dann)"
    r"|(?:i'\''?ll|i will|let me|i am going to|i need to)\s+"
    r"(?:check|take a (?:quick )?look|look into|get started|continue|proceed|now|finish|complete|fix|update|implement|resolve|verify))",
    re.IGNORECASE,
)

# Unbegründete Rückfragen an den Nutzer statt selbstständiger Abarbeitung.
UNWARRANTED_QUESTION_MARKERS = re.compile(
    r"(?:\b(?:soll|sollen)\s+(?:ich|wir|die|das)\b[^\n?]{0,250}\?"
    r"|\bm[öo]chtest\s+du\b[^\n?]{0,250}\?"
    r"|\bwillst\s+du\b[^\n?]{0,250}\?"
    r"|\bwie\s+m[öo]chtest\s+du\s+(?:vorgehen|das\s+haben|weitermachen)\b"
    r"|\b(?:sag|gib|schreib)\s+(?:mir\s+)?(?:kurz\s+)?bescheid\b"
    r"|\blass\s+mich\s+wissen\b"
    r"|\b(?:should\s+i|shall\s+we|would\s+you\s+like\s+me\s+to)\b[^\n?]{0,250}\?)",
    re.IGNORECASE | re.MULTILINE,
)

# Ein konkreter lokaler Restpunkt darf nicht durch einen externen Blocker
# verdeckt werden. Diese enge Teilmenge gilt auch bei BLOCKED_ON_USER.
LOCAL_UNFINISHED_MARKERS = re.compile(
    r"(noch lokal(?:e[rnms]?)?\s+umzusetzen\b"
    r"|ich konnte(?:n)? .{0,120}\bnoch nicht\b"
    r"|fertig (?:sind|ist) nur\b)",
    re.IGNORECASE,
)

# Zitierte Beispiele sind keine eigenen Zusagen. Öffnendes und schließendes
# Zeichen müssen dafür NICHT vom selben Typ sein: Chat- und Editorautokorrektur
# erzeugt regelmäßig gemischte Paare wie „…" (deutsches Auf, gerades Zu). Vor
# dem 08.08.2026 blieben genau diese Zitate stehen und wurden anschließend als
# Arbeitsversprechen der eigenen Session gewertet — der Guard blockierte einen
# Bericht, der fremde Sätze nur belegte.
QUOTED_EXAMPLES = re.compile(
    r"`[^`\n]*`"
    r"|„[^„“”\"\n]*[“”\"]"
    r"|“[^„“”\"\n]*[”\"]"
    r"|\"[^„“”\"\n]*[\"”]"
)

FOREIGN_SCOPE_REFERENCE = re.compile(
    r"\b(?:in|bei)\s+(?:jener|einer\s+anderen|der\s+anderen|fremden)\s+"
    r"(?:[a-zäöüß0-9_-]+\s+){0,3}(?:session|sitzung|pane|panel|projekt)\b",
    re.IGNORECASE,
)


def without_foreign_scope_segments(text):
    """Entfernt nur Sätze, die ausdrücklich eine fremde Arbeitssitzung melden.

    Der Kontaktagent darf nach einer abgeschlossenen Guard-Reparatur den noch
    offenen Stand der beobachteten Fremdsitzung nennen. Dieser Status gehört
    nicht automatisch zum Arbeitsumfang des aktuellen Turns.
    """
    segments = re.split(r"(?<=[.!?])\s+|\n{2,}", str(text or ""))
    return "\n".join(
        segment for segment in segments
        if segment and not FOREIGN_SCOPE_REFERENCE.search(segment)
    )


def has_open_work_marker(text):
    """Erkennt Statuswidersprüche und echte eigene Arbeitsankündigungen."""
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return (
        OPEN_STATUS_MARKERS.search(without_examples) is not None
        or WORK_PROMISE_MARKERS.search(without_examples) is not None
    )


def has_open_status_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", without_foreign_scope_segments(text))
    return OPEN_STATUS_MARKERS.search(without_examples) is not None


def has_current_scope_incomplete_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", without_foreign_scope_segments(text))
    return CURRENT_SCOPE_INCOMPLETE_MARKERS.search(without_examples) is not None


def has_execution_deferral_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return EXECUTION_DEFERRAL_MARKERS.search(without_examples) is not None


def has_work_promise(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return WORK_PROMISE_MARKERS.search(without_examples) is not None


def has_unwarranted_question(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return UNWARRANTED_QUESTION_MARKERS.search(without_examples) is not None


def has_local_unfinished_marker(text):
    without_examples = QUOTED_EXAMPLES.sub("", text)
    return LOCAL_UNFINISHED_MARKERS.search(without_examples) is not None


BLOCKED_ATTESTATION_LINE = re.compile(
    r"(?:^|\n)\s*(?:[-*•]\s*)?BLOCKED_ON_USER:\s*(?P<detail>[^\n]*)",
    re.IGNORECASE,
)
NON_SPECIFIC_BLOCKERS = re.compile(
    r"^(?:sp[aä]ter|unbekannt|unklar|offen|todo|tbd|n/?a|keine ahnung|wartet)"
    r"[.!\s]*$",
    re.IGNORECASE,
)
WEAK_BLOCKER_PHRASES = re.compile(
    r"\b(?:ich m[öo]chte|vielleicht|irgendwann|allgemeine freigabe|"
    r"keine lust|sp[aä]ter machen)\b",
    re.IGNORECASE,
)
BLOCKER_EVIDENCE = re.compile(
    r"\b(?:fehl(?:t|en|end\w*)|ben[öo]tig\w*|brauch(?:e|t|en)|muss|erforderlich|nur (?:nick|der nutzer|"
    r"die nutzerin)|nicht erreichbar|nicht verf[üu]gbar|abgelehnt|gesperrt|"
    r"timeout|error|fehler|http\s*[45]\d\d|enodata|eacces|permission|"
    r"physisch\w*|registriert\w*|installier\w*|bedien\w*|\w*freigab\w*|entscheid\w*|"
    r"eingabe|antwort|zugang|anmeld\w*|login|2fa|passwort|w[aä]hl\w*|"
    r"best[aä]tig\w*|bereitstell\w*|"
    r"liefer\w*|zuleit\w*|entwurf\w*|"
    r"schl[üu]ssel|key|eas_status_(?:finished|failed)|"
    r"is_for_ios_simulator_false|(?:idevice_tool|usbmuxd_socket|usb_device_bus)_missing)\b",
    re.IGNORECASE,
)
TAILSCALE_PC_CONNECTION_QUESTION = re.compile(
    r"(?=.*\b(?:pc|rechner|computer)\b)"
    r"(?=.*\b(?:tailscale|tailnet)\b)"
    r"(?=.*\b(?:verbunden|verbindung|online|erreichbar)\b)",
    re.IGNORECASE | re.DOTALL,
)

# Ein Kontaktagent darf einen ausdrücklichen Bildprüfauftrag nicht an Nick
# zurückgeben, nur weil sein aktuell gepinntes Modell keine Bildeingaben
# versteht. Die eigene Modellgrenze löst stattdessen eine taskgebundene
# multimodale Routerübergabe aus. Metafragen zur Modellfähigkeit bleiben
# davon unberührt.
IMAGE_TASK_PROMPT = re.compile(
    r"(?=.*\b(?:bild\w*|foto\w*|screenshot\w*|grafik\w*|image\w*|video\w*)\b)"
    r"(?=.*\b(?:siehst?|sehen|ansehen|schau\w*|guck\w*|pr[üu]f\w*|"
    r"analys\w*|beschreib\w*|erkenn\w*|bewert\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_CAPABILITY_META_PROMPT = re.compile(
    r"(?=.*\bmodell\w*\b)(?=.*\b(?:unterst[üu]tz\w*|f[äa]hig\w*|"
    r"capabilit\w*|kann\s+(?:das\s+)?modell)\b)",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_CAPABILITY_DEFLECTION = re.compile(
    r"(?:\b(?:kann|konnte)\b.{0,100}\bbild\w*\b.{0,100}\bnicht\b"
    r".{0,60}\b(?:ansehen|sehen|pr[üu]fen|analysieren|auswerten|verarbeiten)\b"
    r"|\bmodell\w*\b.{0,120}\bunterst[üu]tzt\b.{0,60}\bkeine?\b"
    r".{0,40}\bbild(?:er|[- ]?eingaben?)?\b)"
    r"(?=.*\b(?:multimodal\w*|router\w*|bild[- ]?eingab\w*|vision\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)


def needs_multimodal_handoff(user_text, assistant_text):
    concrete_router_rejection = re.search(
        r"\b(?:herdr-?router|routerplan|router plan)\b.{0,160}"
        r"\b(?:parked|geparkt|abgelehnt|executiongate)\b",
        assistant_text or "", re.IGNORECASE | re.DOTALL,
    )
    return bool(
        IMAGE_TASK_PROMPT.search(user_text or "")
        and not IMAGE_CAPABILITY_META_PROMPT.search(user_text or "")
        and not concrete_router_rejection
        and (
            IMAGE_CAPABILITY_DEFLECTION.search(assistant_text or "")
            or re.search(
                r"\b(?:agy|multimodal\w*|fachroute|bildroute|videoroute)\b"
                r".{0,120}\b(?:nicht bereit|nicht verf[üu]gbar|fehlt|kann nicht)\b",
                assistant_text or "", re.IGNORECASE | re.DOTALL,
            )
        )
    )


def needs_integration_configuration_preflight(assistant_text):
    text = assistant_text or ""
    claim = re.search(
        r"\b(?:integration|verbindung|api)\b.{0,160}"
        r"\b(?:nicht konfiguriert|unkonfiguriert|nicht nutzbar|fehlt|fehlen)\b",
        text, re.IGNORECASE | re.DOTALL,
    )
    if not claim:
        return False
    effective = re.search(
        r"\b(?:effektivcheck|laufzeitpfad|runtime)\b.{0,180}"
        r"\b(?:configured|authorized|usable|false|fehlermeldung|error)\b",
        text, re.IGNORECASE | re.DOTALL,
    )
    return not bool(effective)


def needs_durable_bug_media_bundle(user_text, assistant_text, has_media=None):
    request = re.search(r"\b(?:bug|fehler)\b", user_text or "", re.IGNORECASE)
    if has_media is None:
        has_media = bool(re.search(
            r"\b(?:video\w*|screenshot\w*|bild\w*|foto\w*)\b",
            user_text or "", re.IGNORECASE,
        ))
    media = has_media
    if not (request and media):
        return False
    evidence = assistant_text or ""
    required = [
        r"\bbug[- ]?id\b",
        r"\bkanonisch\w*\b.{0,40}\bprojekt",
        r"\bsha-?256\b.{0,8}[a-f0-9]{64}\b",
        r"\bmime\b.{0,20}\b(?:image|video)/",
        r"\b(?:syncthing|sync)\b.{0,80}\b(?:bytegleich|ansichtskopie)\b",
    ]
    return not all(re.search(pattern, evidence, re.IGNORECASE | re.DOTALL) for pattern in required)


def needs_audit_sync_evidence(assistant_text):
    text = assistant_text or ""
    finished = re.search(
        r"\b(?:audit|architekturbericht|reviewbericht)\w*\b.{0,100}"
        r"\b(?:fertig|fertiggestellt|abgeschlossen|aktualisiert)\b",
        text, re.IGNORECASE | re.DOTALL,
    )
    if not finished:
        return False
    return not (
        re.search(r"\bkanonisch\w*\b", text, re.IGNORECASE)
        and re.search(r"\b(?:syncthing|sync)\b.{0,80}\b(?:bytegleich|ansichtskopie)\b", text, re.IGNORECASE | re.DOTALL)
        and re.search(r"\bsha-?256\b.{0,8}[a-f0-9]{64}\b", text, re.IGNORECASE)
    )


def needs_complete_visual_acceptance(user_text, assistant_text):
    if not IMAGE_TASK_PROMPT.search(user_text or ""):
        return False
    text = assistant_text or ""
    if not re.search(r"\b(?:visuell\w*\s+pass|freigegeben|sieht .{0,20}gut)\b", text, re.IGNORECASE):
        return False
    dimensions = [
        r"safe area|statusleiste", r"marke|logo|lesbarkeit",
        r"[üu]berlagerung|clipping", r"navigation|footer",
        r"eingabe|control", r"stil|icon",
    ]
    return not (
        all(re.search(pattern, text, re.IGNORECASE) for pattern in dimensions)
        and re.search(r"\bsha-?256\b.{0,8}[a-f0-9]{64}\b", text, re.IGNORECASE)
    )

EXPLICIT_RELEASE_AUTHORIZATION = re.compile(
    r"(?=.*\b(?:ota|eas[- ]?update|preview[- ]?update|deploy(?:ment|en)?|"
    r"ver[öo]ffentlich(?:en|ung)|release)\b)"
    r"(?=.*\b(?:kannst|darfst|sollst|mach(?:en)?|f[üu]hr(?:e|en)|"
    r"ver[öo]ffentlich\w*|freig(?:abe|egeben)|hauptsitzung|hauptsession)\b)",
    re.IGNORECASE | re.DOTALL,
)
RELEASE_SESSION_DEFLECTION = re.compile(
    r"(?=.*\b(?:ota|eas[- ]?update|preview[- ]?update|deploy(?:ment|en)?|"
    r"ver[öo]ffentlich(?:en|ung)|release)\b)"
    r"(?=.*(?:\breleaseberechtigt\w*\s+(?:haupt)?sitzung\b|"
    r"\bandere\w*\s+(?:releaseberechtigt\w*\s+)?(?:haupt)?sitzung\b|"
    r"\b(?:workflow|arbeitsablauf|rolle|diese\w*\s+sitzung)\b.{0,180}"
    r"\b(?:untersagt|verbietet|darf\w*\s+nicht|erlaubt\w*\s+nicht)\b|"
    r"\b(?:untersagt|verbietet|darf\w*\s+nicht|erlaubt\w*\s+nicht)\b.{0,180}"
    r"\b(?:workflow|arbeitsablauf|rolle|diese\w*\s+sitzung)\b))",
    re.IGNORECASE | re.DOTALL,
)


def blocked_on_user_detail(text):
    """Liefert nur einen konkreten, nicht bloß behaupteten Nutzerblocker."""
    match = BLOCKED_ATTESTATION_LINE.search(text)
    if not match:
        return None
    detail_lines = [match.group("detail").strip()]
    # Terminal- und Chatoberflächen brechen lange Abschlusszeilen häufig hart
    # um. Der Blocker ist deshalb der gesamte Absatz, nicht nur die erste
    # physische Zeile nach dem Marker.
    remainder = text[match.end():].lstrip("\r\n")
    for line in remainder.splitlines():
        stripped = line.strip()
        if not stripped:
            break
        if re.match(r"(?:BLOCKED_ON_USER:|AUFTRAG VOLLSTÄNDIG ERLEDIGT)", stripped, re.I):
            break
        detail_lines.append(stripped)
    detail = " ".join(part for part in detail_lines if part).strip()
    if (
        len(detail) < 12
        or NON_SPECIFIC_BLOCKERS.fullmatch(detail)
        or WEAK_BLOCKER_PHRASES.search(detail)
        or TAILSCALE_PC_CONNECTION_QUESTION.search(detail)
        or not BLOCKER_EVIDENCE.search(detail)
    ):
        return None
    return detail


def has_blocker_attestation_line(text):
    return BLOCKED_ATTESTATION_LINE.search(text or "") is not None


def contradicts_explicit_release_authorization(user_text, assistant_text):
    """Verhindert erfundene Worker-/Sitzungsgrenzen nach Release-Freigabe."""
    return bool(
        EXPLICIT_RELEASE_AUTHORIZATION.search(user_text or "")
        and RELEASE_SESSION_DEFLECTION.search(assistant_text or "")
    )


def has_completion_attestation(text):
    return (
        has_full_completion_attestation(text)
        or blocked_on_user_detail(text) is not None
    )


FULL_COMPLETION_ATTESTATION = re.compile(
    r"(?:^|\n)\s*AUFTRAG VOLLSTÄNDIG ERLEDIGT\s*(?:$|\n)",
    re.IGNORECASE,
)


def has_full_completion_attestation(text):
    return FULL_COMPLETION_ATTESTATION.search(text) is not None


def has_blocked_attestation(text):
    return blocked_on_user_detail(text) is not None


PHYSICAL_DEVICE_BLOCKER = re.compile(
    r"(?=.*\b(?:physisch(?:e[nmrs]?|en)?|registriert(?:e[nmrs]?|en)?|"
    r"nativ(?:e[nmrs]?|en)?|installier\w*)\b)"
    r"(?=.*\b(?:iphone|ios[- ]ger[aä]t|ger[aä]t)\b)"
    r"(?=.*\b(?:[öo]ffnen|ge[öo]ffnet|starten|ausf[üu]hren|testen|pr[üu]fen|"
    r"bedien\w*|teil(?:en|t)|testergebnis|"
    r"(?:ger[aä]te)?absturz|crash|hardware)\b)",
    re.IGNORECASE | re.DOTALL,
)


def requires_physical_device_validation(text):
    """Erkennt belegte Blocker, die ein echtes iOS-Gerät voraussetzen.

    Ein Browser-Geräterahmen prüft Layout und Bedienung, kann aber einen nur
    nativ reproduzierbaren Start oder Absturz auf Hardware nicht verifizieren.
    Die Ausnahme bleibt deshalb an eine konkrete BLOCKED_ON_USER-Zeile und an
    mehrere unabhängige Hardware-Signale gebunden.
    """
    detail = blocked_on_user_detail(text)
    return bool(detail and PHYSICAL_DEVICE_BLOCKER.search(detail))


EXTERNAL_EAS_BUILD_PENDING = re.compile(
    r"(?=.*\b(?:eas|expo|ios)[- ]?(?:ios[- ]?)?build\b|"
    r"(?=.*expo\.dev/.*/builds/))"
    r"(?=.*\b(?:noch in bearbeitung|in bearbeitung|build l[äa]uft|"
    r"wird (?:gerade |aktuell )?(?:erstellt|gebaut)|queued|in progress|pending)\b)",
    re.IGNORECASE | re.DOTALL,
)


def waits_for_external_eas_build(text):
    """Erkennt einen bereits gestarteten, extern weiterlaufenden EAS-Build.

    Dieser Zustand ist weder ein fehlender Browser-Nachweis noch Nutzerinput.
    Die Ausnahme ist bewusst eng auf EAS/Expo-Buildsprache plus einen expliziten
    laufenden Status begrenzt; lokale Restarbeit wird dadurch nicht verdeckt.
    """
    return bool(
        text
        and EXTERNAL_EAS_BUILD_PENDING.search(text)
        and not has_work_promise(text)
        and not has_local_unfinished_marker(text)
    )


ASSISTANT_TEXT_TYPES = ("text", "output_text")


def _assistant_message_content(entry):
    """Liefert die Content-Liste einer Assistant-Nachricht aus altem
    (entry.type == "assistant") oder neuem Codex-Format
    (entry.type == "response_item" mit payload message assistant).

    Codex 0.147 serialisiert sichtbaren Assistenten-Text als output_text und
    nicht mehr als text. Ohne diese Fallunterscheidung blieb die letzte
    Assistenten-Antwort für Codex-Transkripte immer leer, wodurch alle
    textbasierten Restarbeitsregeln des Guards nicht mehr griffen.
    """
    if entry.get("type") == "assistant":
        return (entry.get("message") or {}).get("content") or []
    payload = entry.get("payload") or {}
    if (
        entry.get("type") == "response_item"
        and payload.get("type") == "message"
        and payload.get("role") == "assistant"
    ):
        return payload.get("content") or []
    return []


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
                content = _assistant_message_content(entry)
                if not content:
                    continue
                if isinstance(content, str):
                    parts = [content]
                else:
                    parts = [
                        c.get("text", "")
                        for c in content
                        if isinstance(c, dict) and c.get("type") in ASSISTANT_TEXT_TYPES
                    ]
                if parts:
                    text = "\n".join(parts)
    except OSError:
        return None
    return text


def turn_assistant_text(transcript_path):
    """Liefert den gesamten sichtbaren Assistenten-Text des aktuellen Nutzerturns."""
    messages = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
                    messages.clear()
                    continue
                content = _assistant_message_content(entry)
                if not content:
                    continue
                if isinstance(content, str):
                    messages.append(content)
                else:
                    parts = [
                        c.get("text", "")
                        for c in content
                        if isinstance(c, dict) and c.get("type") in ASSISTANT_TEXT_TYPES
                    ]
                    if parts:
                        messages.append("\n".join(parts))
    except OSError:
        return ""
    return "\n".join(messages)


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


def _user_message_text(entry):
    """Liest sichtbaren Nutzertext aus Codex- oder Claude-Transkripten."""
    if entry.get("type") == "response_item":
        message = entry.get("payload") or {}
    else:
        message = entry.get("message") or {}
    if message.get("role") != "user":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") in ("text", "input_text")
    ).strip()


def _is_stop_hook_prompt(entry):
    """Erkennt nur die von Codex injizierte automatische Stop-Fortsetzung."""
    return _user_message_text(entry).lstrip().lower().startswith("<hook_prompt")


def _is_codex_internal_context(entry):
    """Erkennt von Codex injizierte Goal-/Laufzeitkontexte.

    Diese Einträge werden technisch als User-Nachricht serialisiert, sind aber
    weder eine neue Antwort von Nick noch eine neue Dialogrunde.
    """
    return _user_message_text(entry).lstrip().lower().startswith(
        "<codex_internal_context"
    )


def _is_automatic_user_message(entry):
    return _is_stop_hook_prompt(entry) or _is_codex_internal_context(entry)


def last_actual_user_text(transcript_path):
    """Letzter echter Prompt, ohne vom Stop-Hook injizierte Fortsetzung."""
    latest = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not _is_real_user_message(entry):
                    continue
                candidate = _user_message_text(entry)
                if not candidate or _is_automatic_user_message(entry):
                    continue
                latest = candidate
    except OSError:
        return ""
    return latest


def _user_message_has_media(entry):
    """Erkennt echte Medienblöcke, nicht bloße Wörter wie „Video“."""
    message = (
        entry.get("payload") or {}
        if entry.get("type") == "response_item"
        else entry.get("message") or {}
    )
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        mime = str(item.get("mime_type") or item.get("mimeType") or "").lower()
        if kind in (
            "input_image", "input_video", "image", "video", "attachment",
        ) or mime.startswith(("image/", "video/")):
            return True
    return False


def last_actual_user_has_media(transcript_path):
    """Medienstatus ausschließlich des jüngsten echten Nutzerturns."""
    latest = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    not _is_real_user_message(entry)
                    or _is_automatic_user_message(entry)
                ):
                    continue
                latest = _user_message_has_media(entry)
    except OSError:
        return False
    return latest


def user_asked_completion_check(transcript_path):
    return bool(COMPLETION_CHECK_PROMPT.search(last_actual_user_text(transcript_path)))


def user_requested_read_only_scope(transcript_path):
    return bool(READ_ONLY_SCOPE_PROMPT.search(last_actual_user_text(transcript_path)))


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
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
                    has_tool_activity = False
                    continue
                if _is_tool_activity(entry):
                    has_tool_activity = True
    except OSError:
        return False
    return has_tool_activity


def session_has_tool_activity(transcript_path):
    """Prüft, ob in der bisherigen Session überhaupt Werkzeugaktivität stattfand."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _is_tool_activity(entry):
                    return True
    except OSError:
        return False
    return False


def continuation_has_tool_activity(transcript_path):
    """Prüft Arbeit nach der jüngsten automatisch injizierten Stop-Fortsetzung.

    Sobald der Hook-Prompt im Transkript steht, zählt ausschließlich danach
    gestartete Arbeit. Ohne sichtbaren Hook-Prompt bleibt aus Kompatibilitäts-
    gründen die normale Turn-Erkennung maßgeblich.
    """
    hook_prompt_seen = False
    has_tool_activity = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _is_real_user_message(entry):
                    if _is_stop_hook_prompt(entry):
                        hook_prompt_seen = True
                        has_tool_activity = False
                    elif _is_codex_internal_context(entry):
                        continue
                    else:
                        hook_prompt_seen = False
                        has_tool_activity = False
                    continue
                if hook_prompt_seen and _is_tool_activity(entry):
                    has_tool_activity = True
    except OSError:
        return False
    return (
        has_tool_activity
        if hook_prompt_seen
        else turn_has_tool_activity(transcript_path)
    )


RUNNING_CELL = re.compile(
    r"^\s*Script running with cell ID\s+([A-Za-z0-9._:-]+)(?:\s|$)", re.I
)
CELL_ID_FIELD = re.compile(
    r"(?:cell_id|cellId)\s*[\"']?\s*[:=]\s*[\"'](?P<id>[A-Za-z0-9._:-]+)[\"']",
    re.I,
)


def _payload_text(payload):
    value = payload.get("output")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item.get("text", "")
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def codex_open_exec_cells(transcript_path):
    """Findet im aktuellen Nutzerturn gestartete, noch nicht abgeholte Exec-Cells."""
    open_cells = set()
    wait_calls = {}
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
                    open_cells.clear()
                    wait_calls.clear()
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload") or {}
                kind = payload.get("type")
                if kind in ("custom_tool_call", "function_call"):
                    name = str(payload.get("name") or "").lower()
                    raw = payload.get("input") or payload.get("arguments") or ""
                    if not isinstance(raw, str):
                        raw = json.dumps(raw, ensure_ascii=False)
                    if name.endswith("wait") or "tools.wait(" in raw:
                        match = CELL_ID_FIELD.search(raw)
                        if match and payload.get("call_id"):
                            wait_calls[payload["call_id"]] = match.group("id")
                elif kind in ("custom_tool_call_output", "function_call_output"):
                    output = _payload_text(payload)
                    running = RUNNING_CELL.match(output)
                    if running:
                        open_cells.add(running.group(1))
                    call_id = payload.get("call_id")
                    waited_cell = wait_calls.pop(call_id, None)
                    if waited_cell and not RUNNING_CELL.match(output):
                        open_cells.discard(waited_cell)
    except OSError:
        return []
    return sorted(open_cells)


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


def _requirement_key(step):
    """Stabile, inhaltsbasierte ID fuer einen Planpunkt.

    Die ID bleibt ueber replace-all-update_plan-Aufrufe stabil. Eine spaetere
    Wiedereroeffnung erhaelt zusaetzlich eine neue Generation im Ledger.
    """
    normalized = re.sub(r"\s+", " ", str(step or "").strip().casefold())
    return "req-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _plan_call_succeeded(payload):
    if payload.get("is_error") is True or payload.get("isError") is True:
        return False
    status = str(payload.get("status") or "").lower()
    if status in ("error", "failed", "rejected"):
        return False
    output = _payload_text(payload).strip().lower()
    return not (
        output.startswith("error:")
        or '"iserror":true' in output.replace(" ", "")
        or '"is_error":true' in output.replace(" ", "")
    )


def codex_requirement_ledger(transcript_path):
    """Reduziert erfolgreiche Planereignisse zum aktuellen Plan-Snapshot.

    update_plan ist eine Replace-all-Schnittstelle. Deshalb ersetzt jeder
    erfolgreiche Aufruf den vorherigen Snapshot vollständig; ausgelassene,
    erledigte oder verworfene Zwischenpunkte dürfen nicht als Phantomarbeit
    fortleben. Toolcalls mit call_id werden erst nach einem erfolgreichen
    korrelierten Toolresultat übernommen. Alte Transkripte ohne call_id bleiben
    rückwärtskompatibel auswertbar. Requirement-IDs und Generationen bleiben
    für Punkte stabil, die im aktuellen Snapshot tatsächlich wiederkehren.
    """
    events = []
    pending_calls = {}
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
                kind = payload.get("type")
                if kind in ("custom_tool_call", "function_call"):
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
                    if any(token in raw for token in (
                        "agent-stop-guard/",
                        "stop-open-items-guard.py",
                        "test_stop_open_items_guard.py",
                    )):
                        continue
                    parsed = _plan_items_from_input(raw)
                    if not parsed:
                        continue
                    call_id = payload.get("call_id")
                    if call_id:
                        pending_calls[call_id] = parsed
                    else:
                        events.append((parsed, ["legacy-transcript-plan-call"]))
                elif kind in ("custom_tool_call_output", "function_call_output"):
                    call_id = payload.get("call_id")
                    parsed = pending_calls.pop(call_id, None)
                    if parsed is not None and _plan_call_succeeded(payload):
                        events.append((parsed, [f"tool-result:{call_id}"]))
    except OSError:
        return []

    ledger = {}
    generations = {}
    previous_status = {}
    for event_index, (items, evidence_refs) in enumerate(events, start=1):
        next_ledger = {}
        for item in items:
            requirement_id = _requirement_key(item["step"])
            current = ledger.get(requirement_id)
            status = item["status"]
            generation = generations.get(requirement_id, 0)
            if generation == 0:
                generation = generations.get(requirement_id, 0) + 1
                generations[requirement_id] = generation
            elif (
                status in ("pending", "in_progress")
                and previous_status.get(requirement_id) in ("completed", "superseded")
            ):
                generation += 1
                generations[requirement_id] = generation
            next_ledger[requirement_id] = {
                "id": requirement_id,
                "generation": generation,
                "step": item["step"],
                "status": status,
                "lastEvent": event_index,
                "evidenceRefs": list(evidence_refs),
            }
            previous_status[requirement_id] = status
        for superseded_id in set(ledger) - set(next_ledger):
            previous_status[superseded_id] = "superseded"
        ledger = next_ledger

    return sorted(ledger.values(), key=lambda item: (item["lastEvent"], item["id"]))


def codex_open_plan_items(transcript_path):
    """Liefert alle offenen Requirements der aktuellen Ledger-Generation."""
    return [
        {"step": item["step"], "status": item["status"]}
        for item in codex_requirement_ledger(transcript_path)
        if item["status"] in ("pending", "in_progress")
    ]


def codex_plan_updated_in_current_turn(transcript_path):
    """Belegt einen erfolgreichen update_plan-Aufruf seit Nicks letztem Prompt.

    Ein Codex-Plan ist eine turnlokale Arbeitsanzeige, kein dauerhaftes Goal.
    Deshalb darf ein Plan aus einem früheren Nutzerturn den Abschluss eines
    späteren, engeren Auftrags nicht blockieren. Automatische Hook- und
    interne Kontextnachrichten beginnen dagegen keinen neuen Nutzerturn.
    """
    pending_calls = {}
    successful_update = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
                    pending_calls = {}
                    successful_update = False
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload") or {}
                kind = payload.get("type")
                if kind in ("custom_tool_call", "function_call"):
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
                    if any(token in raw for token in (
                        "agent-stop-guard/",
                        "stop-open-items-guard.py",
                        "test_stop_open_items_guard.py",
                    )):
                        continue
                    if not _plan_items_from_input(raw):
                        continue
                    call_id = payload.get("call_id")
                    if call_id:
                        pending_calls[call_id] = True
                    else:
                        successful_update = True
                elif kind in ("custom_tool_call_output", "function_call_output"):
                    call_id = payload.get("call_id")
                    if pending_calls.pop(call_id, None) and _plan_call_succeeded(payload):
                        successful_update = True
    except OSError:
        return False
    return successful_update


def task_blocked_on_user(task):
    """Nur sauber geparkte pending-Tasks gelten als echte Nutzerblockade."""
    if task.get("status") != "pending":
        return False
    description = str(task.get("description") or "")
    return blocked_on_user_detail(description) is not None


def plan_item_blocked_on_user(item):
    """Codex hat kein description-Feld; der Blocker steht daher im Step."""
    return (
        item.get("status") == "pending"
        and blocked_on_user_detail(str(item.get("step") or "")) is not None
    )


PERIODIC_MONITOR_STEP = re.compile(
    r"(?=.*\b(?:alle\s+\d+\s*(?:sekunden|minuten|stunden)|periodisch|"
    r"fortlaufend|kontinuierlich|laufend)\b)"
    r"(?=.*\b(?:monitor\w*|[üu]berwach\w*|\w*kapazit[äa]t\w*|status)\b)",
    re.IGNORECASE | re.DOTALL,
)
PERIODIC_LOCAL_WORK = re.compile(
    r"\b(?:reparier\w*|implementier\w*|integrier\w*|baue?\w*|"
    r"fertigstell\w*|weiter\s+reparier\w*)\b",
    re.IGNORECASE,
)


def plan_item_is_periodic_monitor(item):
    step = str(item.get("step") or "")
    return bool(
        item.get("status") in ("pending", "in_progress")
        and PERIODIC_MONITOR_STEP.search(step)
        and not PERIODIC_LOCAL_WORK.search(step)
    )


def active_durable_monitors():
    """Liefert nur eng registrierte, noch laufende Nutzerprozesse.

    Die optionale Registry ist reine Evidenz; der Hook startet selbst keine
    Prozesse. Ein Eintrag gilt nur mit gleicher UID, lebender PID und einem
    tatsächlich in der Prozess-Commandline gebundenen, vertrauenswürdigen Pfad.
    """
    registry = os.path.join(STATE_ROOT, "durable-monitors.json")
    try:
        with open(registry, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    trusted_roots = tuple(os.path.realpath(path) + os.sep for path in (
        os.path.expanduser("~/.local/share/herdr-router"),
        os.path.expanduser("~/.local/bin"),
    ))
    active = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        declared_path = entry.get("path")
        if not isinstance(pid, int) or pid <= 1 or not isinstance(declared_path, str):
            continue
        real_path = os.path.realpath(declared_path)
        if not any(real_path.startswith(root) for root in trusted_roots):
            continue
        proc_root = f"/proc/{pid}"
        try:
            if os.stat(proc_root).st_uid != os.getuid():
                continue
            with open(os.path.join(proc_root, "cmdline"), "rb") as f:
                command = f.read(16 * 1024).replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace"
                )
        except OSError:
            continue
        if real_path not in command:
            continue
        active.append({"pid": pid, "path": real_path})
    return active


UNICODE_HYPHENS = str.maketrans({
    "‐": "-",  # U+2010 HYPHEN
    "‑": "-",  # U+2011 NON-BREAKING HYPHEN
    "‒": "-",  # U+2012 FIGURE DASH
    "–": "-",  # U+2013 EN DASH
    "—": "-",  # U+2014 EM DASH
    "−": "-",  # U+2212 MINUS SIGN
})


def normalize_visual_text(text):
    """Vereinheitlicht typografische Bindestriche in sichtbaren Nachweisen."""
    return str(text or "").lower().translate(UNICODE_HYPHENS)


MOBILE_UI_PROOF_CLAIM = re.compile(
    r"(?:mobile(?:n|r)?\s+ui[- ]pr[üu]fung|mobile\s+(?:ansicht|ui).{0,80}"
    r"(?:gepr[üu]ft|getestet|nachweis)|iphone[- ]nachweis)",
    re.IGNORECASE | re.DOTALL,
)


def claims_mobile_ui_proof(text):
    """Nur eine ausdrückliche mobile Beweisbehauptung aktiviert das harte Gate.

    Mobile/Expo-Werkzeuge und die bloße Nennung einer Viewport-Größe sind kein
    Grund, beim Stoppen ungefragte Zusatzarbeit zu erzwingen. Die Projektregel
    bleibt eine Arbeitsanweisung; der Hook korrigiert nur einen ausdrücklich
    behaupteten, aber ungültigen mobilen Nachweis.
    """
    return bool(MOBILE_UI_PROOF_CLAIM.search(normalize_visual_text(text)))


NATIVE_IOS_PROOF_BLOCKER = re.compile(
    r"\b(?:nicht verbunden|verbindung fehlgeschlagen|wartet auf (?:eine )?verbindung|"
    r"noch nicht (?:geladen|geb[üu]ndelt)|bundle fehlgeschlagen|"
    r"development[- ]build nicht (?:erreichbar|geladen))\b",
    re.IGNORECASE,
)


def has_native_ios_device_proof(text):
    """Akzeptiert einen geladenen iOS-Development-Build als stärkeren Nachweis.

    Ein bloß gestarteter Metro-Server oder ein wartender Development-Build
    reicht nicht. Der Abschluss muss zugleich einen verbundenen iOS-
    Development-Build, Metro und das tatsächlich erfolgte Bündeln oder Laden
    belegen. Damit hat ein echter nativer Gerätelauf Vorrang vor einer
    nachgebildeten Browserhülle, ohne reine Startmeldungen hochzustufen.
    """
    normalized = normalize_visual_text(text)
    if NATIVE_IOS_PROOF_BLOCKER.search(normalized):
        return False
    development_build = bool(
        re.search(r"\bios[- ]development[- ]build\b", normalized)
        or re.search(r"\bdevelopment[- ]build\b.{0,40}\bios\b", normalized)
    )
    connected = bool(
        re.search(
            r"\bverbunden\w*\b.{0,100}\bios[- ]development[- ]build\b|"
            r"\bios[- ]development[- ]build\b.{0,100}\bverbunden\w*\b|"
            r"\bauf\b.{0,80}\b(?:iphone|ios[- ]ger[aä]t)\b",
            normalized,
        )
    )
    loaded = bool(
        re.search(
            r"\b(?:neu\s+)?(?:geb[üu]ndelt|geladen)\b|"
            r"\b(?:re)?bundled\b|\b(?:re)?loaded\b|\breload(?:ed)?\b",
            normalized,
        )
    )
    return development_build and connected and "metro" in normalized and loaded


def mobile_ui_frame_state(transcript_path, assistant_text=""):
    """Erkennt Mobile-UI-Prüfungen und den geforderten Vollrahmen-Nachweis."""
    has_tool_activity = False
    platform_signal = False
    visual_signal = False
    inspected_visual_artifact = False
    visual_inspection_pending = False
    frame_text = normalize_visual_text(assistant_text)
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
                    has_tool_activity = False
                    platform_signal = False
                    visual_signal = False
                    inspected_visual_artifact = False
                    visual_inspection_pending = False
                    frame_text = normalize_visual_text(assistant_text)
                    continue
                raw = json.dumps(entry, ensure_ascii=False).lower()
                if _is_tool_activity(entry):
                    has_tool_activity = True
                    guard_maintenance = any(
                        token in raw
                        for token in (
                            "agent-stop-guard/",
                            "stop-open-items-guard.py",
                            "test_stop_open_items_guard.py",
                        )
                    )
                    if guard_maintenance:
                        continue
                    if any(
                        token in raw
                        for token in (
                            "npx expo",
                            "expo start",
                            "expo export",
                            "react-native",
                            "app.tsx",
                        )
                    ):
                        platform_signal = True
                    if any(
                        token in raw
                        for token in (
                            "screenshot",
                            "playwright",
                            "chromium",
                            "browser",
                            "vorschau",
                            "preview",
                            "view_image",
                        )
                    ):
                        visual_signal = True
                    payload = entry.get("payload") or {}
                    tool_name = str(payload.get("name") or "").lower()
                    tool_input = str(payload.get("input") or payload.get("arguments") or "").lower()
                    if "view_image" in tool_name or "view_image" in tool_input:
                        visual_inspection_pending = True
                    frame_text += "\n" + normalize_visual_text(raw)
                elif visual_inspection_pending and (
                    entry.get("type") == "custom_tool_call_output"
                    or '"type": "tool_result"' in raw
                    or '"type":"tool_result"' in raw
                ):
                    inspected_visual_artifact = True
                    visual_inspection_pending = False
                elif entry.get("type") in ("event_msg", "response_item"):
                    payload = entry.get("payload") or {}
                    if payload.get("type") in ("agent_message", "message"):
                        frame_text += "\n" + normalize_visual_text(raw)
    except OSError:
        return False, False

    mobile_ui_work = has_tool_activity and platform_signal and visual_signal
    device = "iphone-15-pro" in frame_text or "iphone 15 pro" in frame_text
    frame = any(
        token in frame_text
        for token in (
            "geräterahmen",
            "geraeterahmen",
            "deviceframe",
            "device frame",
            "iphone-15-pro-rahmen",
            "iphone 15 pro rahmen",
        )
    )
    island = "dynamic island" in frame_text
    status = "statusleiste" in frame_text or "status bar" in frame_text
    # Ein sauberer Abschluss darf den bereits geprüften Bildbeleg auch direkt
    # benennen. Die konkrete Tool-Serialisierung von view_image unterscheidet
    # sich zwischen Codex und Claude und ist deshalb kein zuverlässiges Gate.
    # Akzeptiert wird nur eine detaillierte Attestierung mit Vollrahmen,
    # exakter App-Fläche und einem konkreten Bildartefakt.
    assistant_lower = normalize_visual_text(assistant_text)
    exact_app_size = bool(
        re.search(r"393\s*[×x]\s*852\s*(?:css[- ]?pixel|pixel)", assistant_lower)
    )
    # Chatoberflächen brechen lange Pfade mitten in Verzeichnis- und
    # Dateinamen um. Für den Belegabgleich werden diese reinen Layoutumbrüche
    # innerhalb des auf "Beleg:" folgenden Absatzes entfernt.
    evidence_match = re.search(
        r"(?:beleg|bildbeleg|screenshot)\s*:\s*(?P<path>[^\n]*(?:\n(?!\s*\n)[^\n]*){0,8})",
        assistant_lower,
    )
    normalized_evidence = ""
    if evidence_match:
        normalized_evidence = re.sub(r"\s+", "", evidence_match.group("path"))
    normalized_assistant = re.sub(r"\s+", "", assistant_lower)
    image_evidence = bool(
        re.search(r"\.png\b", normalized_evidence)
        or re.search(r"\.png\b", normalized_assistant)
    )
    explicit_attestation = (
        device and frame and island and status and exact_app_size and image_evidence
    )
    return mobile_ui_work, (
        inspected_visual_artifact or explicit_attestation
    ) and device and frame and island and status


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
                if (
                    _is_real_user_message(entry)
                    and not _is_automatic_user_message(entry)
                ):
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
    raw = str(session_id)
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:80]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned}-{digest}"


def ensure_state_root():
    try:
        os.makedirs(STATE_ROOT, mode=0o700, exist_ok=True)
        os.chmod(STATE_ROOT, 0o700)
    except OSError:
        pass


def prune_state():
    """Entfernt ausschließlich veraltete Guard-Zähler und Audit-Marker."""
    cutoff = datetime.datetime.now().timestamp() - STATE_TTL_DAYS * 86400
    try:
        names = os.listdir(STATE_ROOT)
    except OSError:
        return
    for name in names:
        if name in (os.path.basename(LOG_PATH), os.path.basename(KILL_SWITCH)):
            continue
        if not GENERATED_STATE_FILE.fullmatch(name):
            continue
        path = os.path.join(STATE_ROOT, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def clear_block_counters(session_id):
    """Startet das Schleifenbudget für einen echten neuen Nutzerturn neu."""
    prefix = f"{safe_session_id(session_id)}-blocks"
    try:
        names = os.listdir(STATE_ROOT)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(STATE_ROOT, name))
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


def followthrough_pending_path(session_id):
    return os.path.join(
        STATE_ROOT,
        f"{safe_session_id(session_id)}-follow-through-pending",
    )


def followthrough_pending(session_id):
    return os.path.exists(followthrough_pending_path(session_id))


def followthrough_pending_state(session_id):
    try:
        with open(followthrough_pending_path(session_id), encoding="utf-8") as f:
            value = f.read().strip()
    except OSError:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        # Rückwärtskompatibilität für alte Dateien, die nur einen Zeitstempel
        # enthielten: Nach dem Upgrade werden sie fail-open verworfen.
        return {"kind": "hard", "turnFingerprint": None}
    if not isinstance(parsed, dict):
        return {"kind": "hard", "turnFingerprint": None}
    kind = parsed.get("kind")
    return {
        "kind": kind if kind in ("soft", "hard") else "hard",
        "turnFingerprint": parsed.get("turnFingerprint"),
    }


def followthrough_pending_kind(session_id):
    state = followthrough_pending_state(session_id)
    return state.get("kind") if state else None


def set_followthrough_pending(session_id, kind="hard", turn_fingerprint=None):
    if kind not in ("soft", "hard"):
        raise ValueError("Follow-through-Art muss soft oder hard sein")
    try:
        ensure_state_root()
        with open(followthrough_pending_path(session_id), "w", encoding="utf-8") as f:
            json.dump({
                "kind": kind,
                "createdAt": datetime.datetime.now().isoformat(timespec="seconds"),
                "turnFingerprint": turn_fingerprint,
            }, f, sort_keys=True)
    except OSError:
        pass


def clear_followthrough_pending(session_id):
    try:
        os.remove(followthrough_pending_path(session_id))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def log(session_id, decision, detail):
    try:
        ensure_state_root()
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            with open(LOG_PATH, "rb") as source:
                source.seek(-LOG_RETAIN_BYTES, os.SEEK_END)
                retained = source.read()
            newline = retained.find(b"\n")
            if newline >= 0:
                retained = retained[newline + 1:]
            with open(LOG_PATH, "wb") as target:
                target.write(retained)
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


def block(session_id, reason, detail, limit, kind="text", fingerprint=None):
    # Getrennte Zähler je Regel: die harte Task-Regel darf das Budget der
    # weichen Textregel nicht aufbrauchen und umgekehrt.
    suffix = "" if kind == "text" else f"-{kind}"
    ensure_state_root()
    counter_file = os.path.join(
        STATE_ROOT, f"{safe_session_id(session_id)}-blocks{suffix}"
    )
    fingerprint = fingerprint or hashlib.sha256(detail.encode("utf-8")).hexdigest()
    try:
        with open(counter_file, encoding="utf-8") as f:
            stored = json.load(f)
        count = int(stored.get("count", 0)) if stored.get("fingerprint") == fingerprint else 0
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        count = 0
    if count >= limit:
        log(session_id, "durchgelassen", f"Budget {limit} erschöpft: {detail}")
        return False
    try:
        with open(counter_file, "w", encoding="utf-8") as f:
            json.dump({"count": count + 1, "fingerprint": fingerprint}, f)
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

    if payload.get("hook_event_name") == "PreToolUse":
        sleep_guard_path = os.path.expanduser(
            "~/.claude/hooks/pretool-sleep-guard.py"
        )
        try:
            spec = importlib.util.spec_from_file_location("pretool_sleep_guard", sleep_guard_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            print(json.dumps(module.evaluate(payload)))
        except (OSError, AttributeError, ImportError):
            pass
        return

    session_id = payload.get("session_id") or "unknown"
    prune_state()
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
    last_user_text = last_actual_user_text(transcript_path) if transcript_path else ""
    current_turn_text = turn_assistant_text(transcript_path) if transcript_path else text
    dialogue_text = "\n".join(
        part for part in (current_turn_text, text) if part
    )
    # Der letzte echte Nutzerprompt bleibt während aller automatisch
    # injizierten Stop-Fortsetzungen stabil. Er ist deshalb der verlässliche
    # Turn-Schlüssel, selbst wenn ein Provider jeden Retry fälschlich erneut
    # mit stop_hook_active=false kennzeichnet.
    turn_fingerprint = hashlib.sha256(
        last_user_text.encode("utf-8")
    ).hexdigest()
    blocked_attested = has_blocked_attestation(text)
    dialogue_round = bool(
        transcript_path and dialogue_round_active(transcript_path)
    )
    question_wait = bool(asks_direct_question(dialogue_text))

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
        if block(
            session_id,
            reason,
            "Turn endete auf Commentary-Phase",
            3,
            kind="commentary",
            fingerprint=turn_fingerprint,
        ):
            return

    # Ein von Codex ausgelagerter Exec-Lauf bleibt Teil des aktuellen Auftrags.
    # Solange seine Cell nicht mit dem Wait-Werkzeug bis zur Beendigung
    # abgeholt wurde, darf der Kontaktagent den Turn nicht abschließen und den
    # Router-Supervisor unbeaufsichtigt zurücklassen.
    open_cells = codex_open_exec_cells(transcript_path) if transcript_path else []
    if open_cells:
        reason = (
            "HINTERGRUNDLAUF NOCH AKTIV: Im aktuellen Nutzerturn laufen noch "
            f"nicht abgeholte Werkzeug-Cells: {', '.join(open_cells)}. "
            "Warte jede Cell mit dem vorgesehenen Wait-Werkzeug bis zur "
            "tatsächlichen Beendigung ab und werte erst danach Ergebnis, "
            "Routerstatus und gesicherte Ausgabe aus. Unveränderte Polls werden "
            "nicht als Minutenprotokoll kommentiert; eine separate Statusabfrage "
            "oder Textbehauptung beendet den Hintergrundlauf nicht."
        )
        if block(
            session_id,
            reason,
            f"offene Exec-Cells: {', '.join(open_cells)}",
            25,
            kind="exec-cells",
            fingerprint=hashlib.sha256("\n".join(open_cells).encode("utf-8")).hexdigest(),
        ):
            return

    # Eine ausdrücklich erteilte Release-Freigabe darf nicht durch eine frei
    # erfundene Worker-, Rollen- oder Sitzungsgrenze in einen Nutzerblocker
    # umgedeutet werden. Echte technische Ablehnungen bleiben möglich, müssen
    # aber mit ihrem konkreten Provider-, Login-, Berechtigungs- oder
    # Laufzeitbeleg benannt werden.
    if contradicts_explicit_release_authorization(last_user_text, text):
        reason = (
            "SCHEINBLOCKER WIDERSPRICHT DER NUTZERFREIGABE: Der letzte "
            "Nutzerprompt erteilt beziehungsweise bestätigt die Release-/OTA-"
            "Freigabe. Deine Antwort behauptet stattdessen ohne technischen "
            "Beleg, diese Sitzung oder Rolle dürfe nicht veröffentlichen. "
            "Übernimm keine Worker-Beschränkung für die Hauptsitzung. Prüfe "
            "Kanal, Runtime, Anmeldung und enthaltenen Stand und führe die "
            "freigegebene Veröffentlichung aus. Falls ein echter externer "
            "Blocker auftritt, belege ihn konkret mit der tatsächlichen "
            "Provider-, Login-, Berechtigungs- oder Laufzeitfehlermeldung."
        )
        if block(
            session_id,
            reason,
            "Release-Freigabe durch unbelegte Sitzungsgrenze zurückgewiesen",
            8,
            kind="release-deflection",
            fingerprint=turn_fingerprint,
        ):
            return

    if has_blocker_attestation_line(text) and not blocked_attested and not dialogue_round:
        reason = (
            "UNGÜLTIGER NUTZERBLOCKER: Die Abschlusszeile BLOCKED_ON_USER ist "
            "leer, vage, optional oder nicht durch einen konkret benötigten "
            "Nutzerinput beziehungsweise einen realen Fehler belegt. Arbeite "
            "den beauftragten Umfang weiter ab. Wenn wirklich nur die nutzende "
            "Person fortfahren kann, nenne exakt die fehlende Entscheidung, "
            "Eingabe, Berechtigung oder den unveränderten technischen Beleg."
        )
        if block(
            session_id,
            reason,
            "formal vorhandener, aber unbelegter BLOCKED_ON_USER",
            8,
            kind="invalid-blocker",
            fingerprint=turn_fingerprint,
        ):
            return

    if (
        not blocked_attested
        and needs_multimodal_handoff(last_user_text, text)
    ):
        reason = (
            "MULTIMODALE ÜBERGABE ERFORDERLICH: Nick hat einen konkreten "
            "Bildauftrag gestellt und deine Antwort bestätigt, dass nur das "
            "aktuell gepinnte Modell keine Bildeingaben unterstützt. Das ist "
            "kein Nutzerblocker. Behalte den Nutzerkontakt, lies das aktuelle "
            "Router-Schema und delegiere den unveränderten Bildauftrag "
            "taskgebunden mit der passenden Bild- oder Screenshot-Modalität "
            "an ein freigegebenes multimodales Modell. Frage Nick nur dann "
            "erneut, wenn ein realer Provider-, Daten-, Sandbox- oder "
            "Berechtigungsfehler die sichere Übergabe konkret verhindert."
        )
        if block(
            session_id,
            reason,
            "Bildauftrag wegen eigener Modellgrenze zurückgegeben",
            1,
            kind="multimodal-handoff",
            fingerprint=turn_fingerprint,
        ):
            return

    # Eine echte, aktuelle Frage wartet auf Nick. Sie darf weder durch eine
    # ältere Task-/Plananzeige noch durch Textheuristiken in eine künstliche
    # Fortsetzungsschleife umgedeutet werden. Ein laufender Prozess bleibt
    # davon unberührt; der Hook beendet oder bereinigt ihn nicht.
    if question_wait:
        clear_audit_pending(session_id)
        clear_followthrough_pending(session_id)
        log(session_id, "durchgelassen", "aktuelle Frage wartet auf Nutzerantwort")
        return

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
            session_id,
            reason,
            f"{len(actionable_tasks)} offene Tasks",
            MAX_STRUCTURED_BLOCKS,
            kind="tasks",
            fingerprint=hashlib.sha256(
                json.dumps(actionable_tasks, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        ):
            return

    # Codex besitzt kein persistentes Claude-Task-Verzeichnis. Sein jüngster
    # update_plan-Aufruf ist deshalb das harte strukturierte Signal. Ohne
    # diese Prüfung kann ein lokaler in_progress-Punkt neben einem externen
    # API-Blocker fälschlich als vollständig blockiert erscheinen.
    plan_pending = (
        codex_open_plan_items(transcript_path)
        if transcript_path and codex_plan_updated_in_current_turn(transcript_path)
        else []
    )
    durable_monitors = active_durable_monitors()
    monitored_plan = [
        item for item in plan_pending
        if plan_item_is_periodic_monitor(item) and durable_monitors
    ]
    actionable_plan = [
        item for item in plan_pending
        if not plan_item_blocked_on_user(item) and item not in monitored_plan
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
            MAX_STRUCTURED_BLOCKS,
            kind="plans",
            fingerprint=hashlib.sha256(
                json.dumps(actionable_plan, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        ):
            return

    # Ein lokal korrekt strukturierter BLOCKED_ON_USER-Planpunkt ist selbst
    # der maßgebliche Nachweis. Eine zusätzliche, sichtbare Abschlusszeile
    # würde nur erneut formale Marker erzwingen und widerspräche dem
    # dialogischen Abschlussmodell. Ungültige Abschlusszeilen werden weiter
    # oberhalb separat zurückgewiesen.

    evidence_gap = None
    if needs_integration_configuration_preflight(text):
        evidence_gap = (
            "INTEGRATIONSSTATUS NICHT BELEGT: Prüfe vor einer Blockade den "
            "kanonischen Laufzeitpfad sekretfrei auf configured, authorized "
            "und usable; ein fehlender Wert im Worktree genügt nicht."
        )
    elif needs_durable_bug_media_bundle(
        last_user_text,
        text,
        has_media=(
            last_actual_user_has_media(transcript_path)
            if transcript_path else False
        ),
    ):
        evidence_gap = (
            "BUG-MEDIENBELEG FEHLT: Binde den Anhang an Bug-ID, kanonische "
            "Projektablage, SHA-256, MIME-Typ und bytegleiche Sync-Ansicht."
        )
    elif needs_audit_sync_evidence(text):
        evidence_gap = (
            "AUDIT-SYNCBELEG FEHLT: Nenne kanonische Quelle, bytegleiche "
            "Syncthing-Ansichtskopie und identische SHA-256."
        )
    elif needs_complete_visual_acceptance(last_user_text, text):
        evidence_gap = (
            "VISUELLE ABNAHME UNVOLLSTÄNDIG: Ein PASS benötigt getrennte "
            "Belege für Safe Area, Marke/Lesbarkeit, Überlagerung, Navigation, "
            "Controls, Stil/Iconkonsistenz und die Bild-SHA-256."
        )
    if evidence_gap:
        if block(
            session_id,
            evidence_gap,
            "fehlender kanonischer Abschlussbeleg",
            3,
            kind="evidence-contract",
            fingerprint=turn_fingerprint,
        ):
            return

    # Eine Fragerunde ist nur Kontext für zulässiges Warten, niemals selbst ein
    # Stop-Grund. Insbesondere erzwingt der Guard keine künstliche nächste
    # Frage. Harte Tasks, Pläne und offene Werkzeug-Cells wurden oberhalb
    # bereits vollständig geprüft; die normalen Restarbeitsregeln bleiben
    # unterhalb aktiv.
    if dialogue_round:
        if user_promises_future_input(last_user_text) or dialogue_round_resolved(dialogue_text):
            clear_audit_pending(session_id)
            clear_followthrough_pending(session_id)
            log(
                session_id,
                "durchgelassen",
                "Fragerunde wartet auf Nutzerantwort oder angekündigten Input",
            )
            return

    # 2. Harte Restarbeitsregel: Wenn eine Arbeitssession selbst einraeumt,
    #    dass ihr aktueller Umfang noch nicht fertig ist, darf sie nicht mit
    #    einem blossen Statusbericht enden. Besonders wichtig ist die explizite
    #    Kontrollfrage "alle Aufgaben erledigt?": Ein ehrliches "Nein" muss
    #    Fortsetzung oder einen belegten Nutzerblocker ausloesen.
    completion_check = bool(COMPLETION_CHECK_PROMPT.search(last_user_text))
    read_only_scope = bool(
        READ_ONLY_SCOPE_PROMPT.search(last_user_text)
        or user_requests_status_observation(last_user_text)
    )
    turn_text = current_turn_text or text
    # Textsignale bewerten grundsätzlich nur den aktuellen Abschluss. Der
    # gesamte Turn enthält regelmäßig frühere, anschließend erfüllte
    # Ankündigungen wie "Ich prüfe das jetzt". Würden sie erneut ausgewertet,
    # vergifteten sie jeden späteren faktischen Fertigbericht und erzeugten
    # Stop-Schleifen. Der Turntext ist nur ein Fallback für Provider, die keine
    # letzte Assistentennachricht liefern. Strukturierte Tasks, Pläne und
    # Werkzeugzustände werden unabhängig davon weiterhin separat geprüft.
    evaluated_text = text or turn_text

    active_execution_scope = bool(
        not read_only_scope
        and (
            (
                transcript_path
                and (
                    turn_has_tool_activity(transcript_path)
                    or session_has_tool_activity(transcript_path)
                )
            )
            or payload.get("stop_hook_active") is True
            or USER_EXECUTION_REQUEST.search(last_user_text or "")
        )
    )

    admitted_current_rest = bool(
        evaluated_text
        and (
            has_current_scope_incomplete_marker(evaluated_text)
            or has_execution_deferral_marker(evaluated_text)
            or (completion_check and has_open_status_marker(evaluated_text))
        )
    )

    if (
        admitted_current_rest
        and not blocked_attested
        and ((completion_check and not read_only_scope) or active_execution_scope)
    ):
        set_audit_pending(session_id)
        set_followthrough_pending(
            session_id,
            kind="hard",
            turn_fingerprint=turn_fingerprint,
        )
        reason = (
            "STOPP VERWEIGERT: Deine eigene Abschlussantwort sagt, dass der "
            "aktuell besprochene Arbeitsumfang noch nicht fertig ist. Ein "
            "Statusbericht beendet den Auftrag nicht. Arbeite alle bereits "
            "beauftragten und selbststaendig ausfuehrbaren Restpunkte JETZT "
            "weiter ab. Erweitere den Nutzerauftrag dabei nicht: wirklich "
            "optionale, spaetere oder nicht beauftragte Ideen musst du klar "
            "als ausserhalb des aktuellen Auftrags abgrenzen. Wenn nach "
            "Abarbeitung aller unabhaengigen Punkte nur eine Handlung der "
            "nutzenden Person fehlt, beende mit einer eigenen Zeile "
            "'BLOCKED_ON_USER: <konkreter Input und Beleg>'."
        )
        if block(
            session_id,
            reason,
            "selbst eingeraeumte Restarbeit im aktuellen Umfang",
            25,
            kind="unfinished-status",
            fingerprint=turn_fingerprint,
        ):
            return

    # 3. Weiche Textregel mit Scope-Schutz. Reine Befunde, Empfehlungen,
    # Optionen und spätere Ausbauschritte sind kein Umsetzungsauftrag. Geblockt
    # werden nur eigene unmittelbare Arbeitsversprechen, unbegründete Rückfragen,
    # ein konkreter lokaler Rest neben BLOCKED_ON_USER oder ein Widerspruch zur
    # Vollständigkeitszeile.
    unwarranted_question = bool(
        has_unwarranted_question(text)
        and not blocked_attested
        and not read_only_scope
        and active_execution_scope
    )
    text_requires_continuation = bool(
        evaluated_text
        and not read_only_scope
        and (
            has_work_promise(evaluated_text)
            or (blocked_attested and has_local_unfinished_marker(evaluated_text))
            or (
                has_full_completion_attestation(text)
                and has_open_status_marker(text)
            )
            or unwarranted_question
        )
    )
    if text_requires_continuation:
        # Eine blockierte Arbeitsankündigung muss zugleich die harte
        # Audit-Sperre setzen. Andernfalls kann der automatisch fortgesetzte
        # Stopversuch (`stop_hook_active=true`) mit einer bloßen Aufzählung
        # offener Punkte durchrutschen, weil die erste Rückgabe den regulären
        # Abschlussaudit darunter noch nicht erreicht hat.
        set_audit_pending(session_id)
        set_followthrough_pending(
            session_id,
            kind="soft",
            turn_fingerprint=turn_fingerprint,
        )
        if unwarranted_question:
            reason = (
                "RÜCKFRAGE OHNE BLOCKER: Du fragst nach Erlaubnis oder Vorgehen, "
                "statt beauftragte Aufgaben selbstständig abzuarbeiten. Führe den "
                "Originalauftrag JETZT weiter. Stoppe nur mit einer formalen "
                "Zeile 'BLOCKED_ON_USER: <konkreter Input und Beleg>', wenn wirklich "
                "eine unumgängliche Entscheidung oder ein externer Blocker vorliegt."
            )
            block_detail = "Unbegründete Rückfrage statt Fortsetzung"
        else:
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
            block_detail = "Ankündigungsmuster im Text"
        if block(
            session_id,
            reason,
            block_detail,
            MAX_BLOCKS,
            fingerprint=turn_fingerprint,
        ):
            return

    # Ein bereits blockierter Abschluss darf nicht mit einer sprachlich
    # saubereren Ausrede im automatischen Fortsetzungsturn durchrutschen. Nach
    # einer Restarbeits- oder Versprechensblockade muss die Session tatsächlich
    # weiterarbeiten oder einen konkreten Nutzerblocker belegen.
    pending_followthrough_state = followthrough_pending_state(session_id)
    pending_followthrough_kind = (
        pending_followthrough_state.get("kind")
        if pending_followthrough_state else None
    )
    pending_turn_fingerprint = (
        pending_followthrough_state.get("turnFingerprint")
        if pending_followthrough_state else None
    )
    # Eine Fortsetzungssperre gehört exakt zu dem Nutzerturn, der sie erzeugt
    # hat. Ein neuer echter Nutzerprompt darf niemals zur künstlichen
    # Fortsetzung eines alten Auftrags gezwungen werden. Alte Zustandsdateien
    # ohne Fingerprint werden nach diesem Upgrade ebenfalls fail-open verworfen.
    if (
        pending_followthrough_kind
        and pending_turn_fingerprint != turn_fingerprint
    ):
        clear_audit_pending(session_id)
        clear_followthrough_pending(session_id)
        pending_followthrough_kind = None
    if (
        pending_followthrough_kind == "soft"
        and payload.get("stop_hook_active") is True
        and not text_requires_continuation
    ):
        clear_audit_pending(session_id)
        clear_followthrough_pending(session_id)
        log(
            session_id,
            "durchgelassen",
            "unveränderter weicher Fehlalarm nach einer Korrektur fail-open",
        )
        return

    if (
        pending_followthrough_kind == "hard"
        and payload.get("stop_hook_active") is True
        and not blocked_attested
        and not (
            transcript_path
            and continuation_has_tool_activity(transcript_path)
        )
    ):
        reason = (
            "FORTSETZUNG OHNE ARBEIT: Der vorherige Stopversuch wurde wegen "
            "selbst eingeräumter Restarbeit oder eines eigenen "
            "Arbeitsversprechens blockiert. Seit der Stop-Fortsetzung ist "
            "keine tatsächliche Werkzeugaktivität belegt. Führe den "
            "unveränderten Originalauftrag jetzt selbst weiter oder delegiere "
            "ihn erneut sinnvoll. Ein gescheiterter Workerstart beendet den "
            "Originalauftrag nicht. Stoppe nur mit einem konkret belegten "
            "BLOCKED_ON_USER oder nach tatsächlicher Fertigstellung."
        )
        if block(
            session_id,
            reason,
            "Stop-Fortsetzung ohne neue Werkzeugaktivität",
            8,
            kind="follow-through",
            fingerprint=turn_fingerprint,
        ):
            return

    # 4. Spezifische UI-Sicherheitsregel. Allgemeine Werkzeugnutzung erzwingt
    # bewusst keinen zweiten Audit-Turn und keine formale Abschlussphrase.
    mobile_proof_claimed = claims_mobile_ui_proof(text)
    mobile_ui_work, has_full_device_frame = (
        mobile_ui_frame_state(transcript_path, text)
        if transcript_path and mobile_proof_claimed
        else (False, False)
    )
    physical_device_blocker = requires_physical_device_validation(text)
    external_eas_build_pending = waits_for_external_eas_build(text)
    native_ios_device_proof = has_native_ios_device_proof(text)
    if (
        mobile_ui_work
        and mobile_proof_claimed
        and not has_full_device_frame
        and not native_ios_device_proof
        and not physical_device_blocker
        and not external_eas_build_pending
    ):
        reason = (
            "MOBILE-UI-PRÜFUNG UNGÜLTIG: Ein nackter 393×852-Viewport oder "
            "ein entsprechend großes Chromium-Screenshot ist kein iPhone-Nachweis. "
            "Ein erfolgreich über Metro neu gebündelter und geladener, verbundener "
            "iOS-Development-Build auf einem echten Gerät ist der bevorzugte "
            "Nachweis und benötigt keinen zusätzlichen Browserrahmen. Wenn kein "
            "solcher native Gerätebeleg vorliegt, stelle die Expo-/iOS-App in einem "
            "vollständigen realistischen "
            "iPhone-15-Pro-Geräterahmen dar: Die App selbst bleibt exakt 393×852 "
            "CSS-Pixel groß; außen herum müssen sichtbares Gehäuse, abgerundete "
            "Displaykanten, Dynamic Island und eine iOS-Statusleiste mit Uhrzeit, "
            "Mobilfunk/WLAN und Batterie sichtbar sein. Bediene diesen gerahmten "
            "Stand im Browser, prüfe Konsole und Netzwerk und benenne den Beleg "
            "im Abschluss ausdrücklich."
        )
        if block(
            session_id,
            reason,
            "Mobile-UI ohne Vollrahmen-Nachweis",
            3,
            kind="iphone-frame",
            fingerprint=turn_fingerprint,
        ):
            return

    # Altlasten des früheren universellen Abschlussaudits nicht in neue
    # Stopversuche hineintragen.
    if audit_pending(session_id):
        clear_audit_pending(session_id)
        log(session_id, "audit-altlast-entfernt", "universeller Abschlussaudit deaktiviert")
    if followthrough_pending(session_id):
        clear_followthrough_pending(session_id)
        log(session_id, "fortsetzung-erledigt", "neue Werkzeugaktivität nach Stop-Blockade")

    log(session_id, "durchgelassen", "keine offenen Tasks, kein Muster")


if __name__ == "__main__":
    main()
