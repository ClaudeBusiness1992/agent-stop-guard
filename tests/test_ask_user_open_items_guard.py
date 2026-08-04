#!/usr/bin/env python3
"""Regressionstests fuer den Rueckfrage-Guard."""

import importlib.util
import json
import pathlib
import tempfile
import unittest


HOOK_PATH = pathlib.Path(__file__).parent.parent / "hooks" / "ask-user-open-items-guard.py"
SPEC = importlib.util.spec_from_file_location("ask_user_open_items_guard", HOOK_PATH)
GUARD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GUARD)


class GuardTestBase(unittest.TestCase):
    """Nur gemeinsame Helfer - enthaelt bewusst keine eigenen Tests."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        GUARD.TASKS_ROOT = self.tempdir.name
        GUARD.STATE_ROOT = str(pathlib.Path(self.tempdir.name, "guard-state"))

    def add_task(self, session_id, task_id, status, description=""):
        directory = pathlib.Path(self.tempdir.name, session_id)
        directory.mkdir(parents=True, exist_ok=True)
        pathlib.Path(directory, f"{task_id}.json").write_text(
            json.dumps(
                {
                    "id": task_id,
                    "subject": f"Task {task_id}",
                    "status": status,
                    "description": description,
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def payload(session_id="session-1", transcript_path=None):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {"questions": []},
            "transcript_path": transcript_path,
        }

    def transcript(self, text):
        path = pathlib.Path(self.tempdir.name, "transcript.jsonl")
        path.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    def tool_transcript(self, count):
        path = pathlib.Path(self.tempdir.name, "tool-transcript.jsonl")
        entries = []
        for index in range(count):
            entries.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Read",
                                    "id": f"tool-{index}",
                                    "input": {},
                                }
                            ]
                        },
                    }
                )
            )
        path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        return str(path)


class AskUserGuardTests(GuardTestBase):
    def test_ohne_taskliste_bleibt_eine_einfache_rueckfrage_moeglich(self):
        self.assertEqual(GUARD.evaluate(self.payload()), {})

    def test_offene_arbeit_ohne_taskliste_muss_zuerst_erfasst_werden(self):
        transcript = self.transcript("Weiter: APP-0021, APP-0018 und APP-0020")
        result = GUARD.evaluate(self.payload(transcript_path=transcript))
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn("keine offene Taskliste", result["systemMessage"])

    def test_echte_einzelfrage_ohne_offen_signal_bleibt_moeglich(self):
        transcript = self.transcript("Welche Zielplattform soll verwendet werden?")
        self.assertEqual(
            GUARD.evaluate(self.payload(transcript_path=transcript)), {}
        )

    def test_lange_session_ohne_taskliste_wird_ebenfalls_abgewiesen(self):
        transcript = self.tool_transcript(6)
        result = GUARD.evaluate(self.payload(transcript_path=transcript))
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_kurze_session_ohne_taskliste_bleibt_flexibel(self):
        transcript = self.tool_transcript(2)
        self.assertEqual(
            GUARD.evaluate(self.payload(transcript_path=transcript)), {}
        )

    def test_bearbeitbarer_task_blockiert_die_rueckfrage(self):
        self.add_task("session-1", "1", "pending")
        result = GUARD.evaluate(self.payload())
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_ausschliesslich_geparkte_tasks_erlauben_die_rueckfrage(self):
        self.add_task(
            "session-1",
            "1",
            "in_progress",
            "BLOCKED_ON_USER: Zielarchitektur benötigt eine Nutzerentscheidung.",
        )
        self.assertEqual(GUARD.evaluate(self.payload()), {})

    def test_teilblockade_stoppt_unabhaengigen_task_nicht(self):
        self.add_task(
            "session-1",
            "1",
            "pending",
            "BLOCKED_ON_USER: Produktionsfreigabe fehlt.",
        )
        self.add_task("session-1", "2", "pending", "Lokale Tests ausfuehren")
        result = GUARD.evaluate(self.payload())
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn("#2", result["systemMessage"])

    def test_erledigte_tasks_werden_ignoriert(self):
        self.add_task("session-1", "1", "completed")
        self.assertEqual(GUARD.evaluate(self.payload()), {})


if __name__ == "__main__":
    unittest.main()


class DeadlockBreakerTests(GuardTestBase):
    """Ein Agent darf nicht dauerhaft zwischen Frageverbot und Arbeitsverbot haengen."""

    def test_unveraenderte_blockade_wird_nach_drei_versuchen_durchgelassen(self):
        self.add_task("session-1", "7", "pending")
        payload = self.payload()

        # Zwei echte Abweisungen: der Agent bekommt Gelegenheit nachzubessern.
        for attempt in (1, 2):
            result = GUARD.evaluate(payload)
            self.assertEqual(
                result["hookSpecificOutput"]["permissionDecision"],
                "deny",
                f"Versuch {attempt} muss abweisen",
            )

        # Dritter Versuch mit voellig unveraenderter Lage: der Agent steckt fest.
        self.assertEqual(GUARD.evaluate(payload), {})

    def test_fortschritt_setzt_den_zaehler_zurueck(self):
        self.add_task("session-1", "7", "pending")
        payload = self.payload()
        GUARD.evaluate(payload)
        GUARD.evaluate(payload)

        # Der Agent legt einen weiteren Task an - die Lage hat sich geaendert,
        # also faengt die Zaehlung von vorn an statt sofort durchzulassen.
        self.add_task("session-1", "8", "pending")
        result = GUARD.evaluate(payload)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_geparkte_tasks_loeschen_den_zaehler(self):
        self.add_task("session-1", "7", "pending")
        payload = self.payload()
        GUARD.evaluate(payload)
        GUARD.evaluate(payload)

        # Marker gesetzt -> sofort zugelassen, ohne den Zirkelbrecher zu brauchen.
        self.add_task(
            "session-1", "7", "pending", "BLOCKED_ON_USER: Apple-Anmeldung fehlt"
        )
        self.assertEqual(GUARD.evaluate(payload), {})

        # Und der Zaehler ist geloescht: eine spaetere neue Blockade weist
        # wieder regulaer ab statt sofort durchzuwinken.
        self.add_task("session-1", "9", "pending")
        result = GUARD.evaluate(payload)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")


class LongFinishedSessionTests(GuardTestBase):
    """Sessionlaenge allein ist kein Beleg fuer offene Arbeit."""

    def test_lange_aber_erkennbar_fertige_session_darf_fragen(self):
        transcript = self.long_transcript(
            "Alles erledigt und verifiziert, 546 Tests grün. "
            "Bleibt nur eine Entscheidung, die dir gehört."
        )
        self.assertEqual(
            GUARD.evaluate(self.payload(transcript_path=str(transcript))), {}
        )

    def test_lange_session_mit_offen_signal_weist_weiterhin_ab(self):
        transcript = self.long_transcript(
            "Fertig ist der erste Teil. Noch nicht erledigt: der zweite Teil."
        )
        result = GUARD.evaluate(self.payload(transcript_path=str(transcript)))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def long_transcript(self, text):
        """Transkript mit genug Tool-Calls, um den Ersatzbeleg auszuloesen."""
        path = pathlib.Path(self.tempdir.name, "long-transcript.jsonl")
        lines = []
        for index in range(8):
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "id": f"t{index}",
                                    "input": {},
                                }
                            ]
                        },
                    }
                )
            )
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            )
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
