// Providerneutraler Stop-Guard für OpenCode. Er nutzt dieselbe fachliche
// Schwelle wie der Claude-/Codex-Hook: strukturierte Restarbeit und konkrete
// eigene Arbeitsversprechen blockieren; reine Werkzeugnutzung und optionale
// Empfehlungen nicht.
import type { Plugin } from "@opencode-ai/plugin"

const MAX_BLOCKS = 1

const DIALOGUE_ROUND_ENABLE = new RegExp(
  [
    "(?:geh(?:e)?|mach(?:e)?|klär(?:e)?|erklär(?:e)?)\\b.{0,80}\\b(?:jeden\\s+punkt\\s+einzeln|punkt\\s+f[üu]r\\s+punkt|frage\\s+f[üu]r\\s+frage)",
    "(?:immer\\s+)?(?:nur\\s+)?eine\\s+frage\\s+(?:zur\\s+zeit|pro\\s+(?:nachricht|runde)|nach\\s+der\\s+anderen)",
    "nicht\\s+(?:so\\s+)?viele\\s+(?:punkte|fragen)\\s+auf\\s+einmal",
    "(?:starte|beginne|mach(?:e)?)\\b.{0,60}\\bfragerunde",
    "fragerunde\\b.{0,40}\\b(?:starten|beginnen|machen)",
  ].join("|"),
  "i",
)

const DIALOGUE_ROUND_DISABLE = new RegExp(
  [
    "(?:beende|stoppe)\\s+(?:jetzt\\s+)?(?:die\\s+)?fragerunde",
    "keine\\s+weiteren\\s+fragen",
    "ohne\\s+(?:weitere\\s+)?r[üu]ckfragen",
    "(?:mach|erledige|bearbeite|arbeite)\\b.{0,60}\\b(?:alles|den\\s+rest)\\s+(?:auf\\s+einmal|selbstst[aä]ndig|durch|ab)",
    "entscheide\\s+(?:den\\s+rest\\s+)?selbst",
  ].join("|"),
  "i",
)

const DIALOGUE_TASK_RESET = new RegExp(
  [
    "(?:neuer|anderer|nächster|naechster)\\s+(?:auftrag|aufgabe|thema|scope)",
    "(?:alle|sämtliche|saemtliche|diese|die)\\b.{0,100}\\b(?:müssen|muessen|sollen)\\b.{0,100}\\b(?:integriert|umgesetzt|behoben|repariert|gefixt|geprüft|geprueft|abgeschlossen|fertig)",
    "^\\s*(?:implementiere|integriere|repariere|behebe|fixe|baue|erstelle|ändere|aendere|prüfe|pruefe|analysiere|übernimm|uebernimm|arbeite|sorge)\\b",
  ].join("|"),
  "is",
)

// Auswahloptionen können nach der Frage stehen; die Frage muss nicht enden.
// Ein beliebiges Fragezeichen in einem Zitat oder Statusbericht genügt nicht.
const DIRECT_QUESTION = new RegExp(
  "(?:^|[\\n.!]\\s*)(?:" +
    "(?:was|wer|wen|wem|welch\\w*|wie|warum|weshalb|wo|wohin|woher|wann|wieviel|wie\\s+viel|wie\\s+lange)\\b" +
    "|(?:soll\\w*|darf\\w*|kann\\w*|könn\\w*|koenn\\w*|möcht\\w*|moecht\\w*|will\\w*|ist|sind|hat|haben|braucht|passt|gilt|geht|funktioniert|should|shall|can|could|would|do|does|is|are|has|have|what|which|how|why|where|when|who)\\b" +
    ")[^?\\n]{0,500}\\?",
  "im",
)
const QUOTED_DIRECT_QUESTION_CONTEXT = new RegExp(
  "\\b(?:beantworte|entscheide|wähle|waehle|antworte)\\b[^?\\n]{0,120}" +
    "(?:was|wer|wen|wem|welch\\w*|wie|warum|weshalb|wo|wann|soll\\w*|darf\\w*|kann\\w*|möcht\\w*|moecht\\w*|will\\w*|ist|sind)\\b" +
    "[^?\\n]{0,300}\\?",
  "im",
)

const USER_PROMISES_FUTURE_INPUT = new RegExp(
  "(?:" +
    "(?=.*\\b(?:gleich|sobald|nachher|bald)\\b)" +
    "(?=.*\\b(?:workflow|entwurf|bereich|text|datei|pfad|info(?:rmation)?|" +
    "input|antwort)\\b)" +
    "(?=.*\\b(?:schick\\w*|send\\w*|liefer\\w*|bekomm\\w*|reich\\w*|geb\\w*)\\b)" +
    "|(?=.*\\b(?:lasse|lass)\\b.{0,80}\\b(?:testen|pr[üu]fen|gegenchecken)\\b)" +
    "(?=.*\\b(?:melde\\s+mich|gebe\\s+bescheid|schicke\\s+das\\s+ergebnis)\\b)" +
    ")",
  "is",
)

const DIALOGUE_RESOLUTION_MARKERS = new RegExp(
  "\\b(?:fragerunde|entscheidung(?:en)?|punkte?)\\b.{0,100}\\b" +
    "(?:abgeschlossen|gekl[äa]rt|beantwortet|zur\\s+(?:getrennten\\s+)?" +
    "umsetzung\\s+bereit)\\b|\\bkeine\\s+weitere\\s+frage\\b",
  "is",
)

function userPromisesFutureInput(text: string): boolean {
  return USER_PROMISES_FUTURE_INPUT.test(text)
}

const IMAGE_TASK_PROMPT = new RegExp(
  "(?=.*\\b(?:bild\\w*|foto\\w*|screenshot\\w*|grafik\\w*|image\\w*)\\b)" +
    "(?=.*\\b(?:siehst?|sehen|ansehen|schau\\w*|guck\\w*|pr[üu]f\\w*|" +
    "analys\\w*|beschreib\\w*|erkenn\\w*|bewert\\w*)\\b)",
  "is",
)

const IMAGE_CAPABILITY_META_PROMPT = new RegExp(
  "(?=.*\\bmodell\\w*\\b)(?=.*\\b(?:unterst[üu]tz\\w*|f[äa]hig\\w*|" +
    "capabilit\\w*|kann\\s+(?:das\\s+)?modell)\\b)",
  "is",
)

const IMAGE_CAPABILITY_DEFLECTION = new RegExp(
  "(?:\\b(?:kann|konnte)\\b.{0,100}\\bbild\\w*\\b.{0,100}\\bnicht\\b" +
    ".{0,60}\\b(?:ansehen|sehen|pr[üu]fen|analysieren|auswerten|verarbeiten)\\b" +
    "|\\bmodell\\w*\\b.{0,120}\\bunterst[üu]tzt\\b.{0,60}\\bkeine?\\b" +
    ".{0,40}\\bbild(?:er|[- ]?eingaben?)?\\b)" +
    "(?=.*\\b(?:multimodal\\w*|router\\w*|bild[- ]?eingab\\w*|vision\\w*)\\b)",
  "is",
)

function needsMultimodalHandoff(userText: string, assistantText: string): boolean {
  return (
    IMAGE_TASK_PROMPT.test(userText) &&
    !IMAGE_CAPABILITY_META_PROMPT.test(userText) &&
    IMAGE_CAPABILITY_DEFLECTION.test(assistantText)
  )
}

const OPEN_STATUS_MARKERS = new RegExp(
  [
    "noch offen",
    "offene punkte",
    "offener punkt",
    "steht noch aus",
    "stehen noch aus",
    "\\b[1-9]\\d*\\s+(?:(?:befunde?|punkte?|aufgaben?)\\s+)?(?:weiterhin\\s+)?offen\\b",
    "\\b[1-9]\\d*\\s+teilweise\\b",
    "unver[aä]ndert offen",
    "nicht behoben",
    "teilweise behoben",
    "(?:fehlt|fehlen|bleibt|bleiben) noch\\b",
    "noch (?:lokal(?:e[rnms]?)?\\s+)?umzusetzen\\b",
    "ich konnte(?:n)? .{0,120}\\bnoch nicht\\b",
    "fertig (?:sind|ist) nur\\b",
    "remaining (?:tasks|items|work)",
    "still open",
    "not yet (?:done|complete|implemented)",
  ].join("|"),
  "i",
)

const WORK_PROMISE_MARKERS = new RegExp(
  [
    "was ich (?:jetzt|noch|gleich|danach) (?:angehe|mache|umsetze|vorhabe)",
    "ich (?:schaue|gucke|prüfe|pruefe|checke|teste) (?:kurz|mal|gleich|jetzt|mir das|nach)",
    "ich (?:schaue|gucke|prüfe|pruefe|checke|teste) mir (?:(?:jetzt|gleich|nun|direkt|kurz|mal)\\s+)?[^\\n.!?]{0,120}\\b(?:an|nach)\\b",
    "ich (?:arbeite|mache) (?:jetzt|gleich|direkt|nun|sofort) .{0,60}weiter",
    "ich (?:mache|arbeite) weiter\\b",
    "ich arbeite .{0,80}\\bjetzt\\b.{0,40}\\bab\\b",
    "ich (?:repariere|behebe|korrigiere|ersetze|wiederhole|prüfe|pruefe|teste) (?:zuerst|jetzt|gleich|direkt|nun|als n[äa]chstes)",
    "ich (?:beginne|starte|fange|lege) (?:jetzt|gleich|nun|direkt|sofort)",
    "ich setze .{0,40}fort",
    "ich kümmere mich (?:jetzt|gleich|sofort|darum|dann)",
    "(?:i'?ll|i will|let me) (?:check|take a (?:quick )?look|look into|get started|continue|proceed|now)",
  ].join("|"),
  "i",
)

const LOCAL_UNFINISHED_MARKERS = new RegExp(
  [
    "noch lokal(?:e[rnms]?)?\\s+umzusetzen\\b",
    "ich konnte(?:n)? .{0,120}\\bnoch nicht\\b",
    "fertig (?:sind|ist) nur\\b",
  ].join("|"),
  "i",
)

const QUOTED_EXAMPLES = /`[^`\n]*`|„[^“\n]*“|“[^”\n]*”|"[^"\n]*"/g
const FULL_COMPLETION = /(?:^|\n)\s*AUFTRAG VOLLSTÄNDIG ERLEDIGT\s*(?:$|\n)/i
const BLOCKED_LINE = /(?:^|\n)\s*(?:[-*•]\s*)?BLOCKED_ON_USER:\s*([^\n]*)/i
const NON_SPECIFIC_BLOCKER = /^(?:sp[aä]ter|unbekannt|unklar|offen|todo|tbd|n\/?a|keine ahnung|wartet)[.!\s]*$/i

function withoutQuotedExamples(text: string): string {
  return text.replace(QUOTED_EXAMPLES, "")
}

function blockedOnUserDetail(text: string): string | undefined {
  const detail = BLOCKED_LINE.exec(text)?.[1]?.trim()
  if (!detail || detail.length < 12 || NON_SPECIFIC_BLOCKER.test(detail)) return
  return detail
}

function shouldContinueFromText(text: string): boolean {
  const plain = withoutQuotedExamples(text)
  const blocked = blockedOnUserDetail(plain) !== undefined
  return (
    WORK_PROMISE_MARKERS.test(plain) ||
    (blocked && LOCAL_UNFINISHED_MARKERS.test(plain)) ||
    (FULL_COMPLETION.test(plain) && OPEN_STATUS_MARKERS.test(plain))
  )
}

type Todo = { id?: string; content?: string; status?: string }

function todoBlockedOnUser(todo: Todo): boolean {
  return (
    todo.status === "pending" &&
    blockedOnUserDetail(String(todo.content ?? "")) !== undefined
  )
}

function actionableTodos(todos: Todo[]): Todo[] {
  return todos.filter(
    (todo) =>
      (todo.status === "pending" || todo.status === "in_progress") &&
      !todoBlockedOnUser(todo),
  )
}

const CONTINUE_PROMPT =
  "[stop-open-items-guard] Deine letzte Antwort enthält ein eigenes unmittelbares " +
  "Arbeitsversprechen oder widerspricht ihrem Abschluss. Arbeite ausschließlich " +
  "den bereits beauftragten Originalumfang jetzt ab. Der Guard erweitert weder " +
  "Scope noch Schreibrechte. Stoppe erst nach tatsächlicher Umsetzung und Prüfung " +
  "oder benenne einen konkreten externen Blocker mit BLOCKED_ON_USER und Beleg."

type BlockState = { fingerprint: string; count: number }
const blockCounts = new Map<string, BlockState>()
const skipNextIdle = new Set<string>()

export const StopOpenItemsGuard: Plugin = async ({ client }) => ({
  event: async ({ event }) => {
    if (event.type === "session.error") {
      const properties = event.properties as any
      if (properties?.error?.name === "MessageAbortedError" && properties.sessionID) {
        skipNextIdle.add(properties.sessionID)
      }
      return
    }
    if (event.type !== "session.idle") return
    const sessionID = event.properties.sessionID
    if (!sessionID) return
    if (skipNextIdle.delete(sessionID)) {
      blockCounts.delete(sessionID)
      return
    }

    let text = ""
    let lastUserText = ""
    let dialogueRound = false
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      for (const msg of res.data ?? []) {
        const parts = (msg.parts ?? [])
          .filter((part: any) => part.type === "text" && typeof part.text === "string")
          .map((part: any) => part.text)
        if (parts.length === 0) continue
        const messageText = parts.join("\n")
        if (msg.info?.role === "assistant") {
          text = messageText
          continue
        }
        if (
          msg.info?.role !== "user" ||
          messageText.trimStart().startsWith("[stop-open-items-guard]")
        ) continue
        lastUserText = messageText
        if (DIALOGUE_ROUND_DISABLE.test(messageText)) dialogueRound = false
        else if (DIALOGUE_ROUND_ENABLE.test(messageText)) dialogueRound = true
        else if (dialogueRound && DIALOGUE_TASK_RESET.test(messageText)) dialogueRound = false
      }
    } catch {
      return
    }

    let todos: Todo[] = []
    try {
      const res = await client.session.todo({ path: { id: sessionID } })
      todos = (res.data ?? []) as Todo[]
    } catch {
      // Textsignale funktionieren auch bei älteren OpenCode-Versionen weiter.
    }

    const actionable = actionableTodos(todos)
    const parked = todos.filter(todoBlockedOnUser)
    let prompt: string | undefined
    let promptKind = ""

    if (needsMultimodalHandoff(lastUserText, text) && blockedOnUserDetail(text) === undefined) {
      prompt =
        "[stop-open-items-guard] MULTIMODALE ÜBERGABE ERFORDERLICH: Nick hat " +
        "einen konkreten Bildauftrag gestellt und deine Antwort bestätigt nur " +
        "die Grenze des aktuell gepinnten Modells. Frage nicht erneut, was mit " +
        "dem Bild geschehen soll. Behalte den Nutzerkontakt, lies das aktuelle " +
        "Router-Schema und delegiere den unveränderten Auftrag taskgebunden mit " +
        "Bild- oder Screenshot-Modalität an ein freigegebenes multimodales Modell. " +
        "Nur ein konkret belegter Provider-, Daten-, Sandbox- oder " +
        "Berechtigungsfehler darf die Übergabe stoppen."
      promptKind = "multimodal-handoff"
    } else if (
      QUOTED_DIRECT_QUESTION_CONTEXT.test(text) ||
      DIRECT_QUESTION.test(withoutQuotedExamples(text))
    ) {
      // Eine echte aktuelle Frage wartet auf Nick und wird nicht von einer
      // älteren Todo-Anzeige in eine künstliche Fortsetzung umgedeutet.
      blockCounts.delete(sessionID)
      return
    } else if (actionable.length > 0) {
      const listing = actionable
        .slice(0, 12)
        .map((todo) => `- [${todo.status}] ${todo.content ?? todo.id ?? "Aufgabe"}`)
        .join("\n")
      prompt =
        `[stop-open-items-guard] ${actionable.length} selbstständig bearbeitbare ` +
        `OpenCode-Aufgabe(n) sind offen:\n${listing}\n\nArbeite sie jetzt ab und ` +
        "aktualisiere die Todo-Liste. Nur ein wirklich externer Blocker darf als " +
        "pending mit 'BLOCKED_ON_USER: <konkreter Input und Beleg>' verbleiben."
      promptKind = "todos"
    } else if (parked.length > 0 && blockedOnUserDetail(text) === undefined) {
      prompt =
        "[stop-open-items-guard] Die OpenCode-Todo-Liste enthält geparkte " +
        "BLOCKED_ON_USER-Punkte. Benenne im Abschluss den konkret benötigten " +
        "Nutzerinput samt Beleg; ein Platzhalter wie 'später' genügt nicht."
      promptKind = "parked"
    } else if (
      dialogueRound &&
      (userPromisesFutureInput(lastUserText) || DIALOGUE_RESOLUTION_MARKERS.test(text))
    ) {
      blockCounts.delete(sessionID)
      return
    } else if (text && shouldContinueFromText(text)) {
      prompt = CONTINUE_PROMPT
      promptKind = "text"
    }

    if (!prompt) {
      blockCounts.delete(sessionID)
      return
    }

    const fingerprint = `${promptKind}\u0000${lastUserText}`
    const previous = blockCounts.get(sessionID)
    const count = previous?.fingerprint === fingerprint ? previous.count : 0
    if (count >= MAX_BLOCKS) return
    blockCounts.set(sessionID, { fingerprint, count: count + 1 })
    try {
      await client.session.promptAsync({
        path: { id: sessionID },
        body: { parts: [{ type: "text", text: prompt }] },
      })
    } catch {
      // Session wurde zwischen Idle-Event und Fortsetzung beendet.
    }
  },
})
