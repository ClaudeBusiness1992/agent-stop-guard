// Providerneutraler Stop-Guard für OpenCode. Er nutzt dieselbe fachliche
// Schwelle wie der Claude-/Codex-Hook: strukturierte Restarbeit und konkrete
// eigene Arbeitsversprechen blockieren; reine Werkzeugnutzung und optionale
// Empfehlungen nicht.
import type { Plugin } from "@opencode-ai/plugin"

const MAX_BLOCKS = 8

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
const BLOCKED_LINE = /(?:^|\n)\s*BLOCKED_ON_USER:\s*([^\n]*)/i
const NON_SPECIFIC_BLOCKER = /^(?:sp[aä]ter|unbekannt|unklar|offen|todo|tbd|n\/?a|keine ahnung|wartet)[.!\s]*$/i

export function withoutQuotedExamples(text: string): string {
  return text.replace(QUOTED_EXAMPLES, "")
}

export function blockedOnUserDetail(text: string): string | undefined {
  const detail = BLOCKED_LINE.exec(text)?.[1]?.trim()
  if (!detail || detail.length < 12 || NON_SPECIFIC_BLOCKER.test(detail)) return
  return detail
}

export function shouldContinueFromText(text: string): boolean {
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

const blockCounts = new Map<string, number>()

export const StopOpenItemsGuard: Plugin = async ({ client }) => ({
  event: async ({ event }) => {
    if (event.type !== "session.idle") return
    const sessionID = event.properties.sessionID
    if (!sessionID) return

    const count = blockCounts.get(sessionID) ?? 0
    if (count >= MAX_BLOCKS) return

    let text = ""
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      for (const msg of res.data ?? []) {
        if (msg.info?.role !== "assistant") continue
        const parts = (msg.parts ?? [])
          .filter((part: any) => part.type === "text" && typeof part.text === "string")
          .map((part: any) => part.text)
        if (parts.length > 0) text = parts.join("\n")
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

    if (actionable.length > 0) {
      const listing = actionable
        .slice(0, 12)
        .map((todo) => `- [${todo.status}] ${todo.content ?? todo.id ?? "Aufgabe"}`)
        .join("\n")
      prompt =
        `[stop-open-items-guard] ${actionable.length} selbstständig bearbeitbare ` +
        `OpenCode-Aufgabe(n) sind offen:\n${listing}\n\nArbeite sie jetzt ab und ` +
        "aktualisiere die Todo-Liste. Nur ein wirklich externer Blocker darf als " +
        "pending mit 'BLOCKED_ON_USER: <konkreter Input und Beleg>' verbleiben."
    } else if (parked.length > 0 && blockedOnUserDetail(text) === undefined) {
      prompt =
        "[stop-open-items-guard] Die OpenCode-Todo-Liste enthält geparkte " +
        "BLOCKED_ON_USER-Punkte. Benenne im Abschluss den konkret benötigten " +
        "Nutzerinput samt Beleg; ein Platzhalter wie 'später' genügt nicht."
    } else if (text && shouldContinueFromText(text)) {
      prompt = CONTINUE_PROMPT
    }

    if (!prompt) {
      blockCounts.delete(sessionID)
      return
    }

    blockCounts.set(sessionID, count + 1)
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
