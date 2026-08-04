// Stop-Guard: Wenn eine Session idle wird und die letzte Assistant-Nachricht
// offene/unerledigte Punkte ankündigt, wird automatisch eine Fortsetzungs-
// Nachricht injiziert, statt die Arbeit liegen zu lassen.
// Gegenstück zum Claude-Code-/Codex-Stop-Hook stop-open-items-guard.py.
// Max. MAX_BLOCKS Fortsetzungen pro Session als Endlosschleifen-Schutz.
const MAX_BLOCKS = 8

const MARKERS = new RegExp(
  [
    "noch offen",
    "offene punkte",
    "offener punkt",
    "steht noch aus",
    "stehen noch aus",
    "noch nicht (erledigt|abgeschlossen|umgesetzt|fertig|getestet|implementiert)",
    "verbleibend",
    "ausstehend",
    "nächste schritte",
    "als n[äa]chstes",
    "n[äa]chster schritt",
    "im n[äa]chsten schritt",
    "was ich (jetzt|noch|gleich|danach) (angehe|mache|umsetze|vorhabe)",
    "soll ich (weitermachen|fortfahren|damit weitermachen|die restlichen)",
    "next steps",
    "next up",
    "as a next step",
    "remaining (tasks|items|work)",
    "still open",
    "not yet (done|complete|implemented)",
    // Ich-Ankündigungen: angekündigte eigene Arbeit muss im selben Zug
    // passieren: Eine Ankündigung wie "ich schaue kurz nach" ist noch
    // keine ausgeführte Arbeit.
    "ich (schaue|gucke|prüfe|pruefe|checke|teste) (kurz|mal|gleich|jetzt|mir das|nach)",
    "ich (arbeite|mache) (jetzt|gleich|direkt|nun|sofort) .{0,60}weiter",
    "ich (beginne|starte|fange|lege) (jetzt|gleich|nun|direkt|sofort)",
    "ich setze .{0,40}fort",
    "ich kümmere mich (jetzt|gleich|sofort|darum|dann)",
    "(i'?ll|i will|let me) (check|take a (quick )?look|look into|get started|continue|proceed|now)",
  ].join("|"),
  "i",
)

const CONTINUE_PROMPT =
  "[stop-open-items-guard] Deine letzte Antwort kündigt offene/unerledigte " +
  "Punkte an. Arbeite sie JETZT vollständig ab, statt zu stoppen. Stoppe " +
  "erst, wenn alles erledigt ist — oder wenn ein echter Blocker existiert, " +
  "den nur die nutzende Person lösen kann: dann benenne ihn explizit mit Beleg (roher " +
  "Fehler/Output)."

const blockCounts = new Map<string, number>()

export const StopOpenItemsGuard = async ({ client }: any) => ({
  event: async ({ event }) => {
    if (event.type !== "session.idle") return
    const sessionID = event.properties.sessionID
    if (!sessionID) return

    const count = blockCounts.get(sessionID) ?? 0
    if (count >= MAX_BLOCKS) return

    let text = ""
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      const messages = res.data ?? []
      for (const msg of messages) {
        if (msg.info?.role !== "assistant") continue
        const parts = (msg.parts ?? [])
          .filter((p: any) => p.type === "text" && typeof p.text === "string")
          .map((p: any) => p.text)
        if (parts.length > 0) text = parts.join("\n")
      }
    } catch {
      return
    }

    // Eigene injizierte Prompts und Berichte ÜBER den Guard (zitieren die
    // Marker-Phrasen) nicht erneut anstoßen
    if (!text || text.toLowerCase().includes("stop-guard")) return
    if (!MARKERS.test(text)) return

    blockCounts.set(sessionID, count + 1)
    try {
      await client.session.promptAsync({
        path: { id: sessionID },
        body: { parts: [{ type: "text", text: CONTINUE_PROMPT }] },
      })
    } catch {
      // Session evtl. schon beendet — dann nichts tun
    }
  },
})
