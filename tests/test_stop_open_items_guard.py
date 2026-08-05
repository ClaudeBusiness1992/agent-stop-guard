#!/usr/bin/env python3
"""Regressionstests fuer die Textsignale des Stop-Guards."""

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


HOOK_PATH = pathlib.Path(__file__).parent.parent / "hooks" / "stop-open-items-guard.py"
SPEC = importlib.util.spec_from_file_location("stop_open_items_guard", HOOK_PATH)
GUARD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GUARD)


class MarkerTests(unittest.TestCase):
    def assert_blocked_text(self, text):
        self.assertTrue(GUARD.has_open_work_marker(text), text)

    def assert_allowed_text(self, text):
        self.assertFalse(GUARD.has_open_work_marker(text), text)

    def test_realer_teilabschluss_wird_erkannt(self):
        self.assert_blocked_text(
            "16 Befunde behoben, 2 teilweise, 1 nur in der Testumgebung, "
            "7 offen. Ein Punkt bleibt ehrlich offen."
        )

    def test_kompakte_statuszahlen_werden_einzeln_erkannt(self):
        self.assert_blocked_text("16 behoben, 7 offen.")
        self.assert_blocked_text("16 behoben, 2 teilweise.")

    def test_teilweise_und_nicht_behoben_werden_erkannt(self):
        self.assert_blocked_text("APP-0020 ist teilweise behoben.")
        self.assert_blocked_text("APP-0005 ist nicht behoben.")

    def test_entscheidungsliste_ist_keine_umsetzungsfreigabe(self):
        self.assert_allowed_text("Was jetzt nur noch du entscheiden kannst")

    def test_abschluss_mit_restauftrag_wird_erkannt(self):
        self.assert_blocked_text(
            "Offen für den nächsten Ausbau: echte persistente Accounts, "
            "Zutaten- und Allergieabgleich sowie Spracheingabe. Die API liefert "
            "HTTP 525, daher konnte ich die 50 Rezepte noch nicht real importieren."
        )

    def test_neubau_teilabschluss_wird_erkannt(self):
        self.assert_blocked_text(
            "Der visuelle Neubau bleibt aktiv. Fertig sind nur Recherche, "
            "Kategorienlogik und die gesicherte Referenz; die neue realistische "
            "Draufsicht mit Regalen und Artikeln ist noch nicht als Ergebnis "
            "ausgegeben."
        )

    def test_passive_restarbeit_wird_erkannt(self):
        self.assert_blocked_text(
            "Die Sammlung wird aber noch als interne Kochbuchansicht konsolidiert."
        )

    def test_teilblocker_mit_lokaler_restarbeit_wird_erkannt(self):
        self.assert_blocked_text(
            "Noch lokal umzusetzen ist der sichtbare Instagram-/YouTube-Linkimport. "
            "Der echte Partner-Liveimport bleibt der einzige externe Blocker.\n\n"
            "BLOCKED_ON_USER: Ein API-Client-Key mit crawler-recipes:read fehlt."
        )

    def test_lokale_arbeitsankuendigung_wird_erkannt(self):
        self.assert_blocked_text(
            "Ich arbeite den verbleibenden lokalen Punkt jetzt ab."
        )

    def test_varianten_von_selbst_eingeraeumter_restarbeit(self):
        self.assert_blocked_text("Es fehlen noch die Integrationstests.")
        self.assert_blocked_text("Die native Prüfung bleibt noch aus.")
        self.assert_blocked_text("Ich konnte den Import noch nicht abschließen.")

    def test_reine_reparaturankuendigung_wird_erkannt(self):
        self.assert_blocked_text(
            "Ich repariere zuerst die laufende Vorschau-Umgebung und "
            "wiederhole danach die visuelle Prüfung."
        )

    def test_trennbare_schau_mir_an_ankuendigung_wird_erkannt(self):
        self.assert_blocked_text(
            "Ich schaue mir jetzt gezielt Herdr selbst an: ob die Chat- und "
            "Werkzeugkarten kompakter dargestellt werden können."
        )

    def test_ich_mache_weiter_wird_erkannt(self):
        self.assert_blocked_text(
            "Ich mache weiter mit dem vorgesehenen Onboarding für den Swipe-Tab."
        )

    def test_zitiertes_regelbeispiel_ist_keine_arbeitsankuendigung(self):
        self.assert_allowed_text(
            "Die Formulierung „Ich mache weiter …“ wird als Ankündigung erkannt."
        )
        self.assert_allowed_text(
            "Der Test für `Ich mache weiter` ist erfolgreich."
        )

    def test_echter_fertigbericht_bleibt_erlaubt(self):
        self.assert_allowed_text(
            "Alle beauftragten Punkte sind umgesetzt und verifiziert. "
            "Tests und Build sind erfolgreich."
        )

    def test_empfehlungen_und_nicht_beauftragte_optionen_bleiben_erlaubt(self):
        self.assert_allowed_text(
            "Als nächsten Schritt wäre eine Zusammenlegung denkbar. "
            "Diese optionale Erweiterung ist noch nicht beauftragt."
        )


class ToolActivityTests(unittest.TestCase):
    def write_transcript(self, entries):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        with handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        self.addCleanup(pathlib.Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def test_codex_arbeits_turn_wird_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        self.assertTrue(GUARD.turn_has_tool_activity(path))

    def test_claude_arbeits_turn_wird_erkannt(self):
        path = self.write_transcript([
            {"type": "user", "message": {"role": "user", "content": "Baue das fertig"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use"}]}},
        ])
        self.assertTrue(GUARD.turn_has_tool_activity(path))

    def test_reiner_chat_turn_braucht_keinen_audit(self):
        path = self.write_transcript([
            {"type": "user", "message": {"role": "user", "content": "Wie geht es?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Gut."}]}},
        ])
        self.assertFalse(GUARD.turn_has_tool_activity(path))

    def test_neuer_nutzerprompt_setzt_alte_toolaktivitaet_zurueck(self):
        path = self.write_transcript([
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use"}]}},
            {"type": "user", "message": {"role": "user", "content": "Danke"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Gern."}]}},
        ])
        self.assertFalse(GUARD.turn_has_tool_activity(path))

    def test_audit_sperre_bleibt_bis_zum_werkzeuglauf(self):
        session_id = "unit-audit-latch"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        self.assertFalse(GUARD.audit_pending(session_id))
        GUARD.set_audit_pending(session_id)
        self.assertTrue(GUARD.audit_pending(session_id))
        GUARD.clear_audit_pending(session_id)
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_codex_commentary_phase_wird_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "event_msg", "payload": {
                "type": "agent_message",
                "phase": "commentary",
                "message": "Ich mache weiter.",
            }},
        ])
        self.assertEqual(GUARD.last_assistant_phase(path), "commentary")

    def test_codex_finale_phase_ersetzt_commentary(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "phase": "commentary"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "phase": "final_answer"}},
        ])
        self.assertEqual(GUARD.last_assistant_phase(path), "final_answer")

    def test_codex_plan_liefert_offene_punkte(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "input": (
                    'const r = await tools.update_plan({"plan":['
                    '{"step":"Lokalen Linkimport ergänzen","status":"in_progress"},'
                    '{"step":"Build prüfen","status":"completed"},'
                    '{"step":"API live anbinden","status":"pending"}]});'
                ),
            }},
        ])
        self.assertEqual(
            GUARD.codex_open_plan_items(path),
            [
                {"step": "Lokalen Linkimport ergänzen", "status": "in_progress"},
                {"step": "API live anbinden", "status": "pending"},
            ],
        )

    def test_juengster_codex_plan_ersetzt_den_vorherigen(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Alt',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Fertig',status:'completed'}]})",
            }},
        ])
        self.assertEqual(GUARD.codex_open_plan_items(path), [])

    def run_guard(self, payload):
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            with mock.patch.object(sys, "stdout", stdout):
                GUARD.main()
        return stdout.getvalue().strip()

    def test_erster_arbeitsabschluss_setzt_audit_sperre(self):
        session_id = "unit-first-stop-latch"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Alles umgesetzt.",
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_arbeitsversprechen_kann_nicht_mit_offenliste_durchrutschen(self):
        session_id = "unit-promise-latches-audit"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])

        first = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Ich arbeite den Remote-MCP-OAuth-Flow jetzt vollständig ab."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(first)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

        second = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Offene, bereits beauftragte Punkte: vollständiger OAuth-Flow "
                "und abschließende Verifikation."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(second)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_erster_belegter_arbeitsabschluss_braucht_keinen_zweitaudit(self):
        session_id = "unit-attested-first-stop"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Originalauftrag geprüft; Tests erfolgreich.\n\n"
                "AUFTRAG VOLLSTÄNDIG ERLEDIGT"
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_erster_belegter_blocker_braucht_keinen_zweitaudit(self):
        session_id = "unit-attested-blocker-first-stop"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Drei belegte Punkte sind abgehakt; DNS/TLS, echter Restore-Test, "
                "Store, Zahlungen, Recht und alle nicht belegten Punkte bleiben "
                "absichtlich offen.\n\n"
                "BLOCKED_ON_USER: Root-A-Record fehlt; autoritativer DNS-Beleg ENODATA."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_reiner_auditbefund_startet_keine_unbeauftragte_umsetzung(self):
        session_id = "unit-read-only-findings"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Im geprüften Fremdprojekt sind 7 Punkte offen. "
                "Das ist der angeforderte Read-only-Auditbericht."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_vollstaendigkeitszeile_mit_offenem_status_bleibt_widerspruch(self):
        session_id = "unit-contradictory-attestation"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "16 Befunde behoben, 7 offen.\n\n"
                "AUFTRAG VOLLSTÄNDIG ERLEDIGT"
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_trennbare_ankuendigung_blockiert_auch_ohne_werkzeuglauf(self):
        session_id = "unit-separable-promise-no-tool"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Ich schaue mir jetzt gezielt Herdr selbst an: ob die Chat- und "
                "Werkzeugkarten kompakter dargestellt werden können."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_ankuendigung_ohne_werkzeug_laesst_audit_offen(self):
        session_id = "unit-no-tool-latch"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.set_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Alles umgesetzt.",
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_werkzeuglauf_erfuellt_offenen_audit(self):
        session_id = "unit-tool-satisfies-latch"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.set_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Alles umgesetzt und verifiziert.\n\n"
                "AUFTRAG VOLLSTÄNDIG ERLEDIGT"
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_werkzeuglauf_ohne_attestierung_bleibt_blockiert(self):
        session_id = "unit-missing-attestation"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.set_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Alles umgesetzt und verifiziert.",
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_blocked_on_user_verdeckt_keinen_lokalen_codex_planpunkt(self):
        session_id = "unit-partial-blocker-plan"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.set_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": (
                    'tools.update_plan({"plan":['
                    '{"step":"Sichtbaren Instagram-/YouTube-Linkimport ergänzen",'
                    '"status":"in_progress"},'
                    '{"step":"50 Partnerdatensätze live importieren",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "TypeScript erfolgreich.\n\n"
                "BLOCKED_ON_USER: Partner-API benötigt einen Client-Key; HTTP 401."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_explizit_geparkter_codex_blocker_darf_abschliessen(self):
        session_id = "unit-explicit-blocker-plan"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.set_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": (
                    'tools.update_plan({"plan":['
                    '{"step":"Lokalen Linkimport ergänzen","status":"completed"},'
                    '{"step":"BLOCKED_ON_USER: Partner-Liveimport braucht Client-Key",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Lokale Arbeit ist umgesetzt und geprüft.\n\n"
                "BLOCKED_ON_USER: Partner-API benötigt einen Client-Key; HTTP 401."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_strukturierte_abschlussbestaetigungen(self):
        self.assertTrue(
            GUARD.has_completion_attestation(
                "BLOCKED_ON_USER: Client-Key fehlt; HTTP-Beleg liegt vor."
            )
        )
        self.assertTrue(
            GUARD.has_completion_attestation("Ergebnis.\nAUFTRAG VOLLSTÄNDIG ERLEDIGT")
        )
        self.assertFalse(GUARD.has_completion_attestation("BLOCKED_ON_USER:"))


if __name__ == "__main__":
    unittest.main()
