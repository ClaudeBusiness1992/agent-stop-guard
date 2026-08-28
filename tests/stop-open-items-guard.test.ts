import assert from "node:assert/strict"
import { StopOpenItemsGuard } from "../plugins/stop-open-items-guard.ts"

type Scenario = {
  prompts: string[]
  event: (event: any) => Promise<void>
}

async function scenario(
  sessionID: string,
  text = "Ich mache weiter.",
  userTexts: string[] = [],
  todos: any[] = [],
): Promise<Scenario> {
  const prompts: string[] = []
  const messages = [
    ...userTexts.map((userText) => ({
      info: { role: "user" },
      parts: [{ type: "text", text: userText }],
    })),
    {
      info: { role: "assistant" },
      parts: [{ type: "text", text }],
    },
  ]
  const client = {
    session: {
      messages: async () => ({ data: messages }),
      todo: async () => ({ data: todos }),
      promptAsync: async ({ body }: any) => {
        prompts.push(body.parts[0].text)
      },
    },
  }
  const hooks = await StopOpenItemsGuard({ client } as any)
  assert.ok(hooks.event)
  return {
    prompts,
    event: async (event) => hooks.event!({ event }),
  }
}

async function main(): Promise<void> {
  const aborted = await scenario("aborted")
  await aborted.event({
    type: "session.error",
    properties: {
      sessionID: "aborted",
      error: { name: "MessageAbortedError", data: { message: "Aborted" } },
    },
  })
  await aborted.event({ type: "session.idle", properties: { sessionID: "aborted" } })
  assert.equal(aborted.prompts.length, 0, "Abbruch darf keine Fortsetzung auslösen")

  const normal = await scenario("normal")
  await normal.event({ type: "session.idle", properties: { sessionID: "normal" } })
  assert.equal(normal.prompts.length, 1, "Normaler Abschluss bleibt geschützt")

  const providerError = await scenario("provider-error")
  await providerError.event({
    type: "session.error",
    properties: {
      sessionID: "provider-error",
      error: { name: "APIError", data: { message: "HTTP 500" } },
    },
  })
  await providerError.event({
    type: "session.idle",
    properties: { sessionID: "provider-error" },
  })
  assert.equal(
    providerError.prompts.length,
    1,
    "Andere Fehler dürfen die Abschlussprüfung nicht pauschal abschalten",
  )

  const unfinished = await scenario(
    "unfinished",
    "Ich repariere jetzt die gefundene Ursache.",
  )
  await unfinished.event({ type: "session.idle", properties: { sessionID: "unfinished" } })
  assert.equal(unfinished.prompts.length, 1, "Eingestandene Restarbeit muss fortgesetzt werden")
  assert.match(unfinished.prompts[0], /stop-open-items-guard/)

  await unfinished.event({ type: "session.idle", properties: { sessionID: "unfinished" } })
  assert.equal(
    unfinished.prompts.length,
    1,
    "Ein identischer Nutzerturn darf keinen Stop-Endloslauf erzeugen",
  )

  await unfinished.event({ type: "session.idle", properties: { sessionID: "unfinished" } })
  assert.equal(
    unfinished.prompts.length,
    1,
    "Weitere Stopversuche setzen das Zirkelbudget desselben Nutzerturns nicht zurück",
  )

  const quoted = await scenario(
    "quoted-unfinished",
    "Der Bericht enthielt das Zitat „noch nichts repariert\"; der Auftrag selbst ist fertig.",
  )
  await quoted.event({ type: "session.idle", properties: { sessionID: "quoted-unfinished" } })
  assert.equal(quoted.prompts.length, 0, "Zitierte Fremdaussagen dürfen keinen Fehlalarm auslösen")

  const completed = await scenario(
    "completed",
    "Erledigt: Der monatliche LLM-Stats-Review läuft ab 01.09. um 09:00 Uhr.",
  )
  await completed.event({ type: "session.idle", properties: { sessionID: "completed" } })
  assert.equal(completed.prompts.length, 0, "Ein faktischer Abschluss muss sofort passieren")

  const imageHandoff = await scenario(
    "image-handoff",
    "Ich sehe die Datei, kann das Bild inhaltlich aber nicht ansehen. Das " +
      "aktuell gepinnte Modell unterstützt keine Bild-Eingaben. Dafür bräuchte " +
      "ich ein multimodales Modell über den Router. Was soll ich mit dem Bild machen?",
    ["Siehst du das Bild?"],
  )
  await imageHandoff.event({
    type: "session.idle",
    properties: { sessionID: "image-handoff" },
  })
  await imageHandoff.event({
    type: "session.idle",
    properties: { sessionID: "image-handoff" },
  })
  assert.equal(
    imageHandoff.prompts.length,
    1,
    "Ein Bildauftrag muss genau einmal zur multimodalen Übergabe zurückgegeben werden",
  )
  assert.match(imageHandoff.prompts[0], /MULTIMODALE ÜBERGABE/)

  const imageHandoffModelOnly = await scenario(
    "image-handoff-model-only",
    "Das gepinnte Modell unterstützt keine Bild-Eingaben; dafür ist ein " +
      "multimodales Modell über den Router nötig.",
    ["Prüfe das Bild."],
  )
  await imageHandoffModelOnly.event({
    type: "session.idle",
    properties: { sessionID: "image-handoff-model-only" },
  })
  assert.equal(imageHandoffModelOnly.prompts.length, 1)
  assert.match(imageHandoffModelOnly.prompts[0], /MULTIMODALE ÜBERGABE/)

  const imageMeta = await scenario(
    "image-meta",
    "Das Modell unterstützt keine Bild-Eingaben; dafür wäre ein multimodales Modell nötig.",
    ["Kann das Modell Bilder sehen?"],
  )
  await imageMeta.event({
    type: "session.idle",
    properties: { sessionID: "image-meta" },
  })
  assert.equal(
    imageMeta.prompts.length,
    0,
    "Eine reine Frage zur Modellfähigkeit darf keine Delegation erzwingen",
  )

  const dialogueQuestion = await scenario(
    "dialogue-question",
    "Meine Empfehlung ist A. Soll die Vorschau danach automatisch beendet werden?",
    ["Gehe bitte jeden Punkt einzeln durch."],
  )
  await dialogueQuestion.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-question" },
  })
  assert.equal(
    dialogueQuestion.prompts.length,
    0,
    "Eine Fragerunde mit genau einer Frage muss auf Nick warten dürfen",
  )

  const dialogueQuestionBeforeOptions = await scenario(
    "dialogue-question-before-options",
    "Soll der Hook eine Frage vor Optionen erkennen?\n\nA — ja.\nB — nein.",
    ["Gehe bitte jeden Punkt einzeln durch."],
  )
  await dialogueQuestionBeforeOptions.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-question-before-options" },
  })
  assert.equal(
    dialogueQuestionBeforeOptions.prompts.length,
    0,
    "Eine Frage vor ihren Optionen ist ein zulässiger Wartezustand",
  )

  const dialogueMissingQuestion = await scenario(
    "dialogue-missing-question",
    "Deine Antwort ist übernommen.",
    ["Bitte nicht so viele Fragen auf einmal, immer eine Frage zur Zeit."],
  )
  await dialogueMissingQuestion.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-missing-question" },
  })
  await dialogueMissingQuestion.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-missing-question" },
  })
  assert.equal(
    dialogueMissingQuestion.prompts.length,
    0,
    "Der Guard darf keine künstliche nächste Frage erzwingen",
  )

  const directQuestion = await scenario(
    "direct-question",
    "Darf ich Herdr jetzt kontrolliert neu starten? A — ja. B — nein.",
    ["Prüfe den Routerstand und bereite die sichere Aktivierung vor."],
  )
  await directQuestion.event({
    type: "session.idle",
    properties: { sessionID: "direct-question" },
  })
  assert.equal(
    directQuestion.prompts.length,
    0,
    "Eine echte aktuelle Frage ist auch außerhalb einer Fragerunde ein Wartezustand",
  )

  const directQuestionWithOpenTodo = await scenario(
    "direct-question-open-todo",
    "Die Prüfung ist abgeschlossen. Darf ich Herdr jetzt kontrolliert neu starten? A — ja. B — nein.",
    ["Bereite die Aktivierung vor."],
    [{ status: "in_progress", content: "Herdr-Aktivierung abnehmen" }],
  )
  await directQuestionWithOpenTodo.event({
    type: "session.idle",
    properties: { sessionID: "direct-question-open-todo" },
  })
  assert.equal(
    directQuestionWithOpenTodo.prompts.length,
    0,
    "Eine echte Frage darf trotz älterer Todo-Anzeige auf Nick warten",
  )

  const punctuationOnly = await scenario(
    "punctuation-only",
    "Der Bericht nennt status?.txt; der Auftrag ist abgeschlossen.",
  )
  await punctuationOnly.event({
    type: "session.idle",
    properties: { sessionID: "punctuation-only" },
  })
  assert.equal(
    punctuationOnly.prompts.length,
    0,
    "Ein beliebiges Fragezeichen darf keinen Dialogzustand vortäuschen",
  )

  const rulesMentionDialogue = await scenario(
    "rules-mention-dialogue",
    "Der Auftrag ist abgeschlossen.",
    [
      "# AGENTS.md instructions for /home/nick\n\n<INSTRUCTIONS>\n" +
        "In einer gewünschten Fragerunde wird Nicks letzte Antwort verarbeitet.\n" +
        "</INSTRUCTIONS>",
      "Weiter.",
    ],
  )
  await rulesMentionDialogue.event({
    type: "session.idle",
    properties: { sessionID: "rules-mention-dialogue" },
  })
  assert.equal(
    rulesMentionDialogue.prompts.length,
    0,
    "Ein Fragerundenhinweis in Agentenregeln darf keinen Dialogmodus aktivieren",
  )

  const vncLoginBlocker = await scenario(
    "vnc-login-blocker",
    "• BLOCKED_ON_USER: Es fehlt die persönliche Anmeldung bei ChatGPT im " +
      "VNC-Fenster — E-Mail-Adresse, Passwort und gegebenenfalls " +
      "2FA-Bestätigung kann nur Nick eingeben.",
    [],
    [{ status: "pending", content: "• BLOCKED_ON_USER: Nick muss sich im VNC-Fenster anmelden." }],
  )
  await vncLoginBlocker.event({
    type: "session.idle",
    properties: { sessionID: "vnc-login-blocker" },
  })
  assert.equal(
    vncLoginBlocker.prompts.length,
    0,
    "Ein persönlicher VNC-Login darf keinen Stop- oder Todo-Loop auslösen",
  )

  const dialoguePromisedWorkflow = await scenario(
    "dialogue-promised-workflow",
    "Alles klar, ich warte auf den Workflow.",
    [
      "Bitte nicht so viele Fragen auf einmal, immer eine Frage zur Zeit.",
      "Bin gleich soweit. Du bekommst gleich den Workflow.",
    ],
  )
  await dialoguePromisedWorkflow.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-promised-workflow" },
  })
  assert.equal(
    dialoguePromisedWorkflow.prompts.length,
    0,
    "Eine konkrete Workflow-Ankündigung ist ein zulässiger Wartezustand",
  )

  const dialogueSuperseded = await scenario(
    "dialogue-superseded",
    "Der neue Integrationsauftrag ist vollständig umgesetzt.",
    [
      "Bitte nicht so viele Fragen auf einmal, immer eine Frage zur Zeit.",
      "Alle Punkte müssen vollständig integriert und geprüft sein.",
    ],
  )
  await dialogueSuperseded.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-superseded" },
  })
  assert.equal(
    dialogueSuperseded.prompts.length,
    0,
    "Ein neuer Arbeitsauftrag muss eine alte Fragerunde ohne Marker ablösen",
  )

  const dialogueExternalCheck = await scenario(
    "dialogue-external-check",
    "Verstanden. Bis zum Testergebnis bleibt alles unverändert.",
    [
      "Gehe bitte jeden Punkt einzeln durch.",
      "Ich lasse dein Ergebnis gerade testen und melde mich.",
    ],
  )
  await dialogueExternalCheck.event({
    type: "session.idle",
    properties: { sessionID: "dialogue-external-check" },
  })
  assert.equal(
    dialogueExternalCheck.prompts.length,
    0,
    "Ein angekündigter externer Gegencheck ist ein zulässiger Wartezustand",
  )
}

void main()
