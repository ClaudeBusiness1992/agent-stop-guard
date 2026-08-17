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

    def test_unfertige_architektur_aenderung_wird_erkannt(self):
        text = (
            "Die vorhandene unfertige Änderung gruppiert zwar Anbieter und Modelle, "
            "ist aber noch nicht einklappbar, und Architektur wird noch nicht separat "
            "erkannt. Ich ändere jetzt bewusst nichts."
        )
        self.assertTrue(GUARD.has_current_scope_incomplete_marker(text))

    def test_noch_nicht_implementiert_wird_als_restarbeit_erkannt(self):
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Die Gruppierung ist derzeit noch nicht implementiert."
            )
        )

    def test_icons_serienreparatur_mit_eingeschobener_kopula_bleibt_offen(self):
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Der erste neue Ersatz ist fertig. Die Serienreparatur ist "
                "damit begonnen, aber noch nicht abgeschlossen."
            )
        )

    def test_icons_passiver_naechster_reparaturschritt_bleibt_offen(self):
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Die App-Originale sind weiterhin unangetastet. Als Nächstes "
                "folgen die 129 motivischen Reparaturen; danach werden die "
                "Ergebnisse integriert und gegengeprüft."
            )
        )

    def test_icons_mengenbezogene_restarbeit_bleibt_offen(self):
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Damit sind 217 von 316 betroffenen Icons vorbereitet. "
                "99 Motivkorrekturen sind noch offen."
            )
        )
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Aktuell sind 202 Korrekturen vorbereitet. "
                "Die übrigen 114 Motive folgen noch."
            )
        )

    def test_bewusstes_nichtaendern_und_uhrzeit_vertagung(self):
        self.assertTrue(
            GUARD.has_execution_deferral_marker(
                "Ich ändere jetzt bewusst nichts."
            )
        )
        self.assertTrue(
            GUARD.has_execution_deferral_marker(
                "Nach 16:00 Uhr kann die Funktion ergänzt, getestet und aktiviert werden."
            )
        )

    def test_optionale_empfehlung_ist_keine_ausfuehrungsvertagung(self):
        self.assertFalse(
            GUARD.has_execution_deferral_marker(
                "Optional wäre später eine weitere Gruppierung denkbar."
            )
        )

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
    def setUp(self):
        self.runtime_state = tempfile.TemporaryDirectory()
        self.addCleanup(self.runtime_state.cleanup)
        self.previous_state_root = GUARD.STATE_ROOT
        self.previous_log_path = GUARD.LOG_PATH
        self.previous_kill_switch = GUARD.KILL_SWITCH
        GUARD.STATE_ROOT = self.runtime_state.name
        GUARD.LOG_PATH = str(pathlib.Path(self.runtime_state.name, "guard.log"))
        GUARD.KILL_SWITCH = str(pathlib.Path(self.runtime_state.name, "disabled"))
        self.addCleanup(setattr, GUARD, "STATE_ROOT", self.previous_state_root)
        self.addCleanup(setattr, GUARD, "LOG_PATH", self.previous_log_path)
        self.addCleanup(setattr, GUARD, "KILL_SWITCH", self.previous_kill_switch)

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

    def test_werkzeugabschluss_braucht_keinen_zweitaudit(self):
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
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_architektur_teilabschluss_blockiert_den_ersten_stop(self):
        session_id = "unit-architecture-incomplete-stop"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Die vorhandene unfertige Änderung gruppiert zwar Anbieter und Modelle, "
                "ist aber noch nicht einklappbar, und Architektur wird noch nicht separat "
                "erkannt. Ich ändere jetzt bewusst nichts."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_icons_teilabschluss_blockiert_den_ersten_stop(self):
        session_id = "unit-icons-incomplete-stop"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Bitte weiter reparieren"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Die App-Originale sind weiterhin unangetastet. Als Nächstes "
                "folgen die 129 motivischen Reparaturen; danach werden die "
                "Ergebnisse integriert und gegengeprüft."
            ),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))

    def test_icons_fortsetzung_darf_nicht_nach_einer_weiteren_charge_stoppen(self):
        session_id = "unit-icons-multi-batch-continuation"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        GUARD.set_audit_pending(session_id)
        GUARD.set_followthrough_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Bitte weiter reparieren"}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "<hook_prompt>weiter</hook_prompt>"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "30 neue Motivkorrekturen sind fertig. Damit sind 217 von 316 "
                "Icons vorbereitet. 99 Motivkorrekturen sind noch offen."
            ),
            "hook_event_name": "Stop",
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))
        self.assertTrue(GUARD.followthrough_pending(session_id))

    def test_reine_statusantwort_ohne_arbeitslauf_bleibt_erlaubt(self):
        session_id = "unit-status-answer-no-work"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Die Gruppierung ist derzeit noch nicht implementiert.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

        passive_path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Was wäre danach möglich?"}],
            }},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
        ])
        passive_output = self.run_guard({
            "session_id": f"{session_id}-passive",
            "transcript_path": passive_path,
            "last_assistant_message": "Als Nächstes folgen optional weitere Reparaturen.",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        })
        self.assertEqual(passive_output, "")

    def test_belegter_nutzerblocker_erlaubt_unfertigen_status(self):
        session_id = "unit-incomplete-with-real-user-blocker"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Die Gruppierung ist noch nicht implementiert.\n\n"
                "BLOCKED_ON_USER: Die erforderliche Designentscheidung fehlt; "
                "nur der Nutzer kann Variante A oder B bestätigen."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_arbeitsversprechen_blockiert_nur_das_echte_versprechen(self):
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
        self.assertEqual(second, "")
        self.assertFalse(GUARD.audit_pending(session_id))

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

    def test_fertiger_text_ohne_werkzeug_loescht_audit_altlast(self):
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
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

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

    def test_werkzeuglauf_braucht_keine_formale_attestierung(self):
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
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

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
        self.assertFalse(
            GUARD.has_completion_attestation("BLOCKED_ON_USER: später")
        )
        self.assertTrue(
            GUARD.has_completion_attestation(
                "BLOCKED_ON_USER: API-Schlüssel fehlt; HTTP 401 belegt das Gate."
            )
        )

    def test_unspezifischer_blocker_parkt_weder_task_noch_plan(self):
        self.assertFalse(
            GUARD.task_blocked_on_user({
                "status": "pending",
                "description": "BLOCKED_ON_USER: später",
            })
        )
        self.assertFalse(
            GUARD.plan_item_blocked_on_user({
                "status": "pending",
                "step": "BLOCKED_ON_USER: unbekannt",
            })
        )

    def test_stop_guard_bericht_ist_keine_ausnahme_fuer_echte_ankuendigung(self):
        session_id = "unit-no-guard-report-bypass"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Beim Stop-Guard: Ich mache jetzt mit der Reparatur weiter."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_nackter_expo_viewport_ist_kein_iphone_rahmen(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        self.assertEqual(GUARD.mobile_ui_frame_state(path), (True, False))

    def test_vollstaendiger_iphone_rahmen_wird_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": (
                    "Expo App.tsx Screenshot mit DeviceFrame iPhone-15-Pro-Geräterahmen, "
                    "Dynamic Island und iOS-Statusleiste; view_image"
                ),
            }},
            {"type": "custom_tool_call_output", "output": "image inspected"},
        ])
        self.assertEqual(GUARD.mobile_ui_frame_state(path), (True, True))


if __name__ == "__main__":
    unittest.main()
