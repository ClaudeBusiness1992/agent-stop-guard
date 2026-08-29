#!/usr/bin/env python3
"""Regressionstests fuer die Textsignale des Stop-Guards."""

import importlib.util
import hashlib
import io
import json
import os
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

    def test_realer_immoworld_abschluss_wird_erkannt(self):
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

    def test_arbeitsversprechen_ohne_adverb_wird_erkannt(self):
        # KritzelKorb-Buildstand vom 08.08.2026: drei Turns nacheinander endeten
        # auf einem eigenen Fortsetzungsversprechen ohne "jetzt"/"gleich".
        self.assert_blocked_text(
            "Was jetzt noch vor dem Build zu tun ist: dein Logo freistellen und "
            "die offene Oberflächenarbeit. Ich arbeite die der Reihe nach ab und "
            "baue erst, wenn alles drin ist."
        )
        self.assert_blocked_text(
            "Solange mache ich die buildrelevanten Reparaturen fertig."
        )
        self.assert_blocked_text(
            "Da sind die alten Quelltexturen drin. Das prüfe ich, bevor gebaut wird."
        )

    def test_getrennte_vorsilbe_am_satzende_wird_erkannt(self):
        # 08.08.2026, zweiter Durchgang: Die deutsche Trennung schiebt die
        # Vorsilbe beliebig weit hinter das Verb. Ein Muster, das nur direkt
        # benachbarte Formen kennt ("mache weiter"), greift dann nicht.
        self.assert_blocked_text(
            "Ich mache mit der Zeilenausrichtung weiter und baue erst, "
            "wenn alles durch ist."
        )
        self.assert_blocked_text(
            "Ich gehe die restlichen drei Punkte der Reihe nach durch."
        )
        self.assert_blocked_text("Ich fahre mit dem Einstellungsumbau fort.")
        self.assert_blocked_text(
            "Ich f\u00fchre den Pr\u00fcflauf f\u00fcr die Grundlinien zu Ende."
        )

    def test_verneinte_trennung_und_redewendung_bleiben_erlaubt(self):
        self.assert_allowed_text(
            "Ich mache damit nicht weiter, das w\u00e4re ungefragte Zusatzarbeit."
        )
        self.assert_allowed_text("Ich gehe davon aus, dass der Build durchl\u00e4uft.")
        self.assert_allowed_text(
            "Ich schaue ab und zu in das Protokoll, dort stand nichts Neues."
        )

    def test_gemischte_anfuehrungszeichen_werden_als_zitat_erkannt(self):
        # 08.08.2026: Ein Bericht belegte fremde Saetze mit gemischtem Paar
        # (deutsches Auf, gerades Zu). Der Guard wertete sie als eigene Zusage
        # und blockierte den Abschluss eines fertigen Auftrags.
        self.assert_allowed_text(
            'Im Protokoll standen drei Saetze: \u201eIch arbeite die der Reihe '
            'nach ab." und \u201eDas pruefe ich, bevor gebaut wird." '
            'Beide sind jetzt abgedeckt.'
        )

    def test_eigene_zusage_ausserhalb_von_zitaten_bleibt_erkannt(self):
        self.assert_blocked_text(
            'Der Guard erkennt \u201eIch arbeite die Liste ab." als Muster. '
            'Ich arbeite die der Reihe nach ab.'
        )

    def test_verneinte_absage_ist_kein_arbeitsversprechen(self):
        self.assert_allowed_text(
            "Das mache ich nicht, weil es den Store-Build bricht."
        )
        self.assert_allowed_text(
            "Ich habe die Liste abgearbeitet und jeden Punkt verifiziert."
        )

    def test_kochwert_abschluss_mit_restauftrag_wird_erkannt(self):
        self.assert_blocked_text(
            "Offen für den nächsten Ausbau: echte persistente Accounts, "
            "Zutaten- und Allergieabgleich sowie Spracheingabe. Die API liefert "
            "HTTP 525, daher konnte ich die 50 Rezepte noch nicht real importieren."
        )

    def test_supermarkt_neubau_teilabschluss_wird_erkannt(self):
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

    def test_kochwert_teilblocker_mit_lokaler_restarbeit_wird_erkannt(self):
        self.assert_blocked_text(
            "Noch lokal umzusetzen ist der sichtbare Instagram-/YouTube-Linkimport. "
            "Der echte Alevora-Liveimport bleibt der einzige externe Blocker.\n\n"
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

    def test_aktuell_unfertiger_gesamtumfang_wird_eng_erkannt(self):
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Nein, noch nicht alle Gesamtaufgaben. Vor einem iOS-Build "
                "fehlen noch die Geraetetests."
            )
        )
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker(
                "Die App ist insgesamt noch nicht veröffentlichungsfertig."
            )
        )
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker("Nein, noch nicht alles.")
        )
        self.assertTrue(
            GUARD.has_current_scope_incomplete_marker("Nein, noch nicht. Zwei Punkte fehlen.")
        )
        self.assertFalse(
            GUARD.has_current_scope_incomplete_marker(
                "Die KI-Erweiterung ist eine optionale Idee fuer spaeter."
            )
        )

    def test_gescheiterte_delegation_mit_unerledigtem_originalauftrag_wird_erkannt(self):
        text = (
            "Nein. Ich habe die Ursache eindeutig gefunden, aber noch nichts "
            "repariert. Der Agy-Fix wurde nicht umgesetzt, weil der gewünschte "
            "Codex-Worker wegen eines Fehlers im zentralen Herdr-Router nicht "
            "starten konnte. Es wurden keine Projektdateien verändert und kein "
            "OTA veröffentlicht."
        )
        self.assert_blocked_text(text)
        self.assertTrue(GUARD.has_current_scope_incomplete_marker(text))

    def test_reine_reparaturankuendigung_wird_erkannt(self):
        self.assert_blocked_text(
            "Ich repariere zuerst die laufende Vorschau-Umgebung und "
            "wiederhole danach die visuelle Prüfung."
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

    def test_pc_tailscale_status_ist_kein_nutzerblocker(self):
        self.assertIsNone(GUARD.blocked_on_user_detail(
            "BLOCKED_ON_USER: Ist dein PC mit deinem Tailscale-Netz verbunden? "
            "Das ist erforderlich, damit die private Dashboard-Adresse von "
            "deinem PC erreichbar ist."
        ))

    def test_vnc_login_mit_chat_bullet_ist_konkreter_nutzerblocker(self):
        detail = GUARD.blocked_on_user_detail(
            "• BLOCKED_ON_USER: Es fehlt die persönliche Anmeldung bei "
            "ChatGPT im VNC-Fenster — E-Mail-Adresse, Passwort und "
            "gegebenenfalls 2FA-Bestätigung kann nur Nick eingeben."
        )
        self.assertIsNotNone(detail)
        self.assertIn("Anmeldung", detail)


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

    def test_hook_fortsetzung_setzt_toolaktivitaet_nicht_zurueck(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:1">Arbeite weiter.</hook_prompt>'
                )}],
            }},
        ])
        self.assertTrue(GUARD.turn_has_tool_activity(path))

    def test_offene_exec_cell_blockiert_abschluss(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "exec-1",
                "output": "Script running with cell ID 85\nWall time 11.0 seconds",
            }},
        ])
        self.assertEqual(GUARD.codex_open_exec_cells(path), ["85"])
        output = self.run_guard({
            "session_id": "unit-open-exec-cell",
            "transcript_path": path,
            "last_assistant_message": "Der Routerlauf hängt im Zustand erstellt.",
            "stop_hook_active": False,
        })
        reason = json.loads(output)["reason"]
        self.assertIn("HINTERGRUNDLAUF NOCH AKTIV", reason)
        self.assertIn("nicht als Minutenprotokoll kommentiert", reason)

    def test_abgeschlossene_wait_cell_erlaubt_abschluss(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "exec-1",
                "output": [{"type": "input_text", "text": "Script running with cell ID 85"}],
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "wait", "call_id": "wait-1",
                "input": '{"cell_id":"85","yield_time_ms":30000}',
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "wait-1",
                "output": "Script completed\nWall time 42.0 seconds",
            }},
        ])
        self.assertEqual(GUARD.codex_open_exec_cells(path), [])

    def test_neuer_nutzerturn_uebernimmt_keine_alte_exec_cell(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "exec-1",
                "output": "Script running with cell ID 72",
            }},
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
        ])
        self.assertEqual(GUARD.codex_open_exec_cells(path), [])

    def test_hook_fortsetzung_uebernimmt_offene_exec_cell(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "exec-1",
                "output": "Script running with cell ID 72",
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:2">Warte weiter.</hook_prompt>'
                )}],
            }},
        ])
        self.assertEqual(GUARD.codex_open_exec_cells(path), ["72"])

    def test_prune_bewahrt_kill_switch_log_und_fremde_datei(self):
        kill_switch = pathlib.Path(GUARD.KILL_SWITCH)
        log_path = pathlib.Path(GUARD.LOG_PATH)
        foreign = pathlib.Path(GUARD.STATE_ROOT, "important-blocks-not-guard-state")
        counter = pathlib.Path(
            GUARD.STATE_ROOT,
            f"{GUARD.safe_session_id('old-session')}-blocks-commentary",
        )
        for path in (kill_switch, log_path, foreign, counter):
            path.write_text("alt", encoding="utf-8")
            os.utime(path, (0, 0))
        GUARD.prune_state()
        self.assertTrue(kill_switch.exists())
        self.assertTrue(log_path.exists())
        self.assertTrue(foreign.exists())
        self.assertFalse(counter.exists())

    def test_log_wird_begrenzt_ohne_neue_eintraege_zu_verlieren(self):
        log_path = pathlib.Path(GUARD.LOG_PATH)
        log_path.write_text("alte-zeile\n" * 40, encoding="utf-8")
        with mock.patch.object(GUARD, "MAX_LOG_BYTES", 100):
            with mock.patch.object(GUARD, "LOG_RETAIN_BYTES", 60):
                GUARD.log("rotation-session", "durchgelassen", "neuer Eintrag")
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("rotation-session\tdurchgelassen\tneuer Eintrag", content)
        self.assertLess(len(content.encode("utf-8")), 300)

    def test_zitierte_cell_historie_ist_keine_offene_exec_cell(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "diagnose-1",
                "output": (
                    "Diagnoseausgabe:\n"
                    "2026-08-06 custom_tool_call_output "
                    "Script running with cell ID 85\n"
                ),
            }},
        ])
        self.assertEqual(GUARD.codex_open_exec_cells(path), [])

    def test_audit_sperre_bleibt_bis_zum_werkzeuglauf(self):
        session_id = "unit-audit-latch"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        GUARD.clear_audit_pending(session_id)
        self.assertFalse(GUARD.audit_pending(session_id))
        GUARD.set_audit_pending(session_id)
        self.assertTrue(GUARD.audit_pending(session_id))
        GUARD.clear_audit_pending(session_id)
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_nackter_expo_viewport_ist_kein_iphone_rahmen(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        mobile_ui, full_frame = GUARD.mobile_ui_frame_state(path)
        self.assertTrue(mobile_ui)
        self.assertFalse(full_frame)

    def test_vollstaendiger_iphone_rahmen_wird_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": (
                    "Expo App.tsx Screenshot mit DeviceFrame iPhone-15-Pro-Geräterahmen, "
                    "Dynamic Island und iOS-Statusleiste"
                ),
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "name": "view_image",
                "input": '{"path":"/tmp/iphone-frame.png"}',
            }},
            {"type": "custom_tool_call_output", "payload": {
                "type": "image", "path": "/tmp/iphone-frame.png",
            }},
        ])
        mobile_ui, full_frame = GUARD.mobile_ui_frame_state(path)
        self.assertTrue(mobile_ui)
        self.assertTrue(full_frame)

    def test_blasser_rahmentext_ist_kein_bildbeleg(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": (
                    "Expo App.tsx Screenshot mit DeviceFrame iPhone 15 Pro, "
                    "Dynamic Island und Statusleiste"
                ),
            }},
        ])
        self.assertEqual(GUARD.mobile_ui_frame_state(path), (True, False))

    def test_detaillierter_abschluss_mit_bildbeleg_gilt_ohne_toolformatbindung(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        assistant_text = (
            "Die mobile UI-Prüfung ist gültig abgeschlossen. Vollständiger "
            "realistischer iPhone-15-Pro-Geräterahmen geprüft; App-Fläche exakt "
            "393 × 852 CSS-Pixel. Gehäuse, Displayrundungen, Dynamic Island und "
            "iOS-Statusleiste sichtbar. Gelesen und Chat wurden bedient; Netzwerk "
            "ohne fehlgeschlagene Anfragen. Bildbeleg: preview/iphone-proof.png"
        )
        self.assertEqual(
            GUARD.mobile_ui_frame_state(path, assistant_text),
            (True, True),
        )
        output = self.run_guard({
            "session_id": "unit-explicit-frame-proof",
            "transcript_path": path,
            "last_assistant_message": assistant_text,
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_verbundener_ios_development_build_ist_staerkerer_geraetebeleg(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        assistant_text = (
            "Die mobile UI-Prüfung ist abgeschlossen. Der verbundene "
            "iOS-Development-Build wurde über Metro neu gebündelt und geladen, "
            "ohne neue Laufzeitfehler. Kein neuer Build und kein OTA erforderlich."
        )
        self.assertTrue(GUARD.has_native_ios_device_proof(assistant_text))
        output = self.run_guard({
            "session_id": "unit-connected-ios-development-build",
            "transcript_path": path,
            "last_assistant_message": assistant_text,
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_blosser_metro_start_ist_kein_nativer_geraetebeleg(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        assistant_text = (
            "Die mobile UI-Prüfung ist abgeschlossen. Metro wurde gestartet und "
            "wartet auf eine Verbindung zu einem iOS-Development-Build."
        )
        self.assertFalse(GUARD.has_native_ios_device_proof(assistant_text))
        output = self.run_guard({
            "session_id": "unit-unconnected-metro-start",
            "transcript_path": path,
            "last_assistant_message": assistant_text,
            "stop_hook_active": False,
        })
        self.assertIn("MOBILE-UI-PRÜFUNG UNGÜLTIG", json.loads(output)["reason"])

    def test_umgebrochener_iphone_rahmen_und_png_pfad_werden_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        assistant_text = (
            "Das Kontaktformular wurde bedient im vollständigen "
            "iPhone-15-Pro-Rahmen mit exakt 393 × 852 CSS-Pixeln, Gehäuse, "
            "Dynamic Island und iOS-Statusleiste geprüft:\n"
            "ClaudeBusiness/einkaufsliste-app/universal/\n"
            "test-results/rechtliches-kontakt-iphone-15-\n"
            "pro.png. Browserkonsole und Netzwerk waren sauber.\n\n"
            "BLOCKED_ON_USER: Für die Weiterleitung fehlt ein aktiver Maildienst; "
            "die Domain besitzt keinen MX-Eintrag."
        )
        self.assertEqual(
            GUARD.mobile_ui_frame_state(path, assistant_text),
            (True, True),
        )
        output = self.run_guard({
            "session_id": "unit-wrapped-frame-proof",
            "transcript_path": path,
            "last_assistant_message": assistant_text,
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_geschuetzte_unicode_bindestriche_im_iphone_nachweis(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        assistant_text = (
            "Die mobile UI-Prüfung ist gültig abgeschlossen: vollständiger "
            "iPhone‑15‑Pro-Rahmen, App-Fläche exakt 393×852 CSS-Pixel, "
            "Gehäuse, Displayrundung, Dynamic Island und iOS-Statusleiste sichtbar. "
            "Diagnosemodus bedient; keine Konsolen- oder Netzwerkfehler. "
            "Bildbeleg: /tmp/kritzelkorb-autosync-iphone15pro.png"
        )
        self.assertEqual(
            GUARD.mobile_ui_frame_state(path, assistant_text),
            (True, True),
        )
        output = self.run_guard({
            "session_id": "unit-unicode-hyphen-frame-proof",
            "transcript_path": path,
            "last_assistant_message": assistant_text,
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_guard_wartung_mit_expo_testtext_ist_keine_mobile_ui_arbeit(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": (
                    "apply_patch /tmp/test_stop_open_items_guard.py "
                    "fixture: npx expo start App.tsx chromium --screenshot"
                ),
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "herdr agent read w129:p2H",
            }},
            {"type": "custom_tool_call_output", "payload": {
                "text": "fremde Session: Expo App.tsx screenshot iPhone"
            }},
        ])
        mobile_ui, full_frame = GUARD.mobile_ui_frame_state(path)
        self.assertFalse(mobile_ui)
        self.assertFalse(full_frame)

    def test_physischer_iphone_blocker_hat_vorrang_vor_browserrahmen(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-physical-iphone-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "BLOCKED_ON_USER: Der neue OTA-freie iOS-Build muss einmal auf "
                "dem registrierten physischen iPhone geöffnet werden, weil sich "
                "der Geräteabsturz serverseitig nicht abschließend ausschließen lässt."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_vnc_login_blocker_beendet_fortsetzung_ohne_werkzeugschleife(self):
        session_id = "unit-vnc-login-blocker"
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        GUARD.set_followthrough_pending(session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Prüfe den Stiltest."}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "• BLOCKED_ON_USER: Es fehlt die persönliche Anmeldung bei "
                "ChatGPT im VNC-Fenster — E-Mail-Adresse, Passwort und "
                "gegebenenfalls 2FA-Bestätigung kann nur Nick eingeben."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(output, "")

    def test_umgebrochener_physischer_iphone_blocker_hat_vorrang(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-wrapped-physical-iphone-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "BLOCKED_ON_USER: Der native Absturzfix muss\n"
                "abschließend einmal durch Öffnen dieses\n"
                "Builds auf dem registrierten iPhone bestätigt\n"
                "werden."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_nativer_diagnosebuild_blocker_hat_vorrang(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-native-diagnostic-build-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "BLOCKED_ON_USER: Der native Diagnosebuild\n"
                "muss auf dem iPhone geöffnet und der\n"
                "sichtbare Startfehlertext beziehungsweise ein\n"
                "erneuter Absturz gemeldet werden."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_laufender_eas_build_loest_keine_mobile_ui_nachforderung_aus(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-running-eas-build",
            "transcript_path": path,
            "last_assistant_message": (
                "Der neue EAS-iOS-Build läuft bereits:\n\n"
                "https://expo.dev/accounts/example/projects/app/builds/build-id\n\n"
                "Status: noch in Bearbeitung. Sobald Expo fertig ist, erscheint "
                "auf genau dieser Seite der Installationsknopf."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_laufender_eas_build_verdeckt_keine_lokale_arbeitsankuendigung(self):
        text = (
            "Der EAS-iOS-Build ist noch in Bearbeitung. "
            "Ich prüfe jetzt noch die lokale Eingabekachel."
        )
        self.assertFalse(GUARD.waits_for_external_eas_build(text))

    def test_allgemeiner_unbelegter_blocker_wird_abgewiesen(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-generic-mobile-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "BLOCKED_ON_USER: Für die Abnahme fehlt noch Nicks allgemeine Freigabe."
            ),
            "stop_hook_active": False,
        })
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("UNGÜLTIGER NUTZERBLOCKER", parsed["reason"])

    def test_ota_freigabe_darf_nicht_an_andere_hauptsitzung_delegiert_werden(self):
        path = self.write_transcript([
            {"type": "user", "message": {
                "role": "user", "content": "ota kannst machen",
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-ota-fake-release-session",
            "transcript_path": path,
            "last_assistant_message": (
                "Die Freigabe ist eindeutig, aber ich darf aus dieser Expo-"
                "Engineering-Sitzung selbst kein EAS-OTA veröffentlichen.\n\n"
                "BLOCKED_ON_USER: Bitte das Preview-OTA in einer "
                "releaseberechtigten Hauptsitzung ausführen lassen; deine "
                "ausdrückliche Freigabe liegt bereits vor."
            ),
            "stop_hook_active": False,
        })
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("SCHEINBLOCKER WIDERSPRICHT DER NUTZERFREIGABE", parsed["reason"])

    def test_hauptsitzungs_hinweis_widerlegt_falsche_workflow_grenze(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "warum darfst du kein OTA machen? du bist in einer hauptsitzung"
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-ota-fake-workflow-limit",
            "transcript_path": path,
            "last_assistant_message": (
                "Der verbindliche Expo-Arbeitsablauf dieser Sitzung erlaubt "
                "Implementierung und Prüfung, untersagt aber das tatsächliche "
                "Veröffentlichen eines OTA-Updates. Die Korrektur ist "
                "releasebereit, aber noch nicht auf preview veröffentlicht."
            ),
            "stop_hook_active": False,
        })
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("SCHEINBLOCKER WIDERSPRICHT DER NUTZERFREIGABE", parsed["reason"])

    def test_vorangestellte_falsche_rollengrenze_wird_ebenfalls_erkannt(self):
        self.assertTrue(
            GUARD.contradicts_explicit_release_authorization(
                "OTA kannst du machen; das ist die Hauptsitzung.",
                "Das Veröffentlichen verbietet angeblich die Expo-Engineering-Rolle "
                "dieser Sitzung, daher ist das OTA noch nicht veröffentlicht.",
            )
        )

    def test_echter_eas_providerfehler_bleibt_zulaessiger_blocker(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "OTA kannst du machen."}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-ota-real-provider-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "BLOCKED_ON_USER: EAS hat das Update mit HTTP 403 abgelehnt; "
                "Nick muss dem angemeldeten Konto die Projektberechtigung erteilen."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_ausdruecklich_behaupteter_nackter_mobile_nachweis_blockiert(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-claimed-naked-mobile-proof",
            "transcript_path": path,
            "last_assistant_message": (
                "Die mobile UI-Prüfung wurde bei 393×852 getestet; "
                "Screenshot und Browserlauf waren erfolgreich."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")
        self.assertIn("MOBILE-UI-PRÜFUNG UNGÜLTIG", json.loads(output)["reason"])

    def test_expo_browserarbeit_ohne_mobile_beweisbehauptung_blockiert_nicht(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; chromium --screenshot --window-size=393,852 App.tsx",
            }},
        ])
        with mock.patch.object(
            GUARD,
            "mobile_ui_frame_state",
            side_effect=AssertionError("unnötiger Vollscan"),
        ):
            output = self.run_guard({
                "session_id": "unit-no-unrequested-mobile-gate",
                "transcript_path": path,
                "last_assistant_message": (
                    "Die Proton-Adresse ist in Impressum, Datenschutz und Kontakt "
                    "aktualisiert. TypeScript, ESLint und Webexport sind erfolgreich."
                ),
                "stop_hook_active": False,
            })
        self.assertEqual(output, "")

    def test_landingpage_mit_393_viewport_ist_keine_mobile_beweisbehauptung(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call",
                "input": "npx expo start; playwright screenshot landing-redaktion",
            }},
        ])
        text = (
            "Die Landingpage-Designvorschau ist zur Abnahme bereit. "
            "Drei Desktop-/iPhone-Interaktionstests sind erfolgreich; "
            "393 × 852 Pixel wurden mitgeprüft."
        )
        self.assertFalse(GUARD.claims_mobile_ui_proof(text))
        output = self.run_guard({
            "session_id": "unit-landingpage-responsive-no-iphone-proof",
            "transcript_path": path,
            "last_assistant_message": text,
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

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

    def test_codex_plan_aus_frueherem_nutzerturn_ist_fuer_stop_veraltet(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Bearbeite den Bugbestand."}],
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Gesamtbestand',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Committe nur den letzten Fix."}],
            }},
        ])
        self.assertFalse(GUARD.codex_plan_updated_in_current_turn(path))
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-stale-plan-new-user-turn",
            "transcript_path": path,
            "last_assistant_message": "Der letzte Fix ist committed und gepusht.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_automatischer_hook_prompt_veraltet_aktuellen_plan_nicht(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Arbeite den Routerfix ab."}],
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Routertest',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:1">Arbeite weiter.</hook_prompt>'
                )}],
            }},
        ])
        self.assertTrue(GUARD.codex_plan_updated_in_current_turn(path))

    def test_juengster_codex_plan_ersetzt_vorherigen_snapshot(self):
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

    def test_erfolgreicher_planabschluss_schliesst_dieselbe_requirement_id(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "c1",
                "input": "tools.update_plan({plan:[{step:'Alt',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "c1", "output": "ok",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "c2",
                "input": "tools.update_plan({plan:[{step:'Alt',status:'completed'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "c2", "output": "ok",
            }},
        ])
        self.assertEqual(GUARD.codex_open_plan_items(path), [])

    def test_fehlgeschlagener_update_plan_wird_nicht_autoritativ(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "c1",
                "input": "tools.update_plan({plan:[{step:'Alt',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "c1", "output": "ok",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "c2",
                "input": "tools.update_plan({plan:[{step:'Alt',status:'completed'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "c2",
                "output": "Error: rejected", "is_error": True,
            }},
        ])
        self.assertEqual(
            GUARD.codex_open_plan_items(path),
            [{"step": "Alt", "status": "pending"}],
        )

    def test_juengster_snapshot_enthaelt_nur_seine_eigenen_punkte(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Weiter pruefen',status:'pending'}]})",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Anderes fertig',status:'completed'}]})",
            }},
        ])
        ledger = GUARD.codex_requirement_ledger(path)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["generation"], 1)
        self.assertEqual(ledger[0]["step"], "Anderes fertig")
        self.assertEqual(ledger[0]["status"], "completed")
        self.assertTrue(ledger[0]["id"].startswith("req-"))

    def test_wiedereroeffnung_erhoeht_generation_bei_stabiler_id(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Review',status:'pending'}]})"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Review',status:'completed'}]})"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Review',status:'pending'}]})"}},
        ])
        ledger = GUARD.codex_requirement_ledger(path)
        self.assertEqual(ledger[0]["generation"], 2)
        self.assertEqual(ledger[0]["status"], "pending")
        self.assertEqual(ledger[0]["evidenceRefs"], ["legacy-transcript-plan-call"])

    def test_erneuter_punkt_nach_snapshot_abloesung_hat_neue_generation(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Review',status:'pending'}]})"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Anderes',status:'completed'}]})"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'Review',status:'pending'}]})"}},
        ])
        ledger = GUARD.codex_requirement_ledger(path)
        self.assertEqual(ledger[0]["generation"], 2)
        self.assertEqual(ledger[0]["step"], "Review")

    def test_abgeloester_planpunkt_blockiert_fertigen_snapshot_nicht(self):
        session_id = "unit-ledger-completion-claim"
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Erledige A und B."}]}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'A',status:'pending'},{step:'B',status:'pending'}]})"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                "input": "tools.update_plan({plan:[{step:'B',status:'completed'}]})"}},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Alles umgesetzt und verifiziert.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

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

    def test_erfuelltes_frueheres_versprechen_vergiftet_abschluss_nicht(self):
        session_id = "unit-completed-promise-does-not-poison-final"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Richte die Vorschau ein."}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Ich prüfe jetzt den Tailscale-Link."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Codeburn ist privat über Tailscale erreichbar und geprüft."
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Codeburn ist privat über Tailscale erreichbar und geprüft."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_faktischer_opencode_abschluss_bleibt_erlaubt(self):
        session_id = "unit-opencode-factual-final"
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Prüfe die OpenCode-Freigaben."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Ich prüfe jetzt die Projektgrenzen."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "OpenCode-Freigabeprüfung abgeschlossen.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_fragerunde_bleibt_ueber_kurze_antworten_aktiv(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Gehe jeden Punkt einzeln durch und erkläre ihn, damit ich "
                    "qualifiziert antworten kann."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Wie viel Datenverlust ist vertretbar?"
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Vier Stunden."}],
            }},
        ])
        self.assertTrue(GUARD.dialogue_round_active(path))
        self.assertTrue(GUARD.asks_direct_question(
            "Bitte beantworte erneut: „Wie viel Datenverlust ist vertretbar?“"
        ))

    def test_agentenregeln_aktivieren_keine_fragerunde(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "# AGENTS.md instructions for /home/nick\n\n"
                    "<INSTRUCTIONS>\nIn einer gewünschten Fragerunde wird "
                    "Nicks letzte Antwort verarbeitet und genau eine Frage "
                    "gestellt.\n</INSTRUCTIONS>"
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Weiter."}],
            }},
        ])
        self.assertFalse(GUARD.dialogue_round_active(path))

    def test_ausdruecklicher_fragerundenauftrag_bleibt_erkannt(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Starte bitte eine Fragerunde zu den offenen Optionen."
                )}],
            }},
        ])
        self.assertTrue(GUARD.dialogue_round_active(path))

    def test_fragerunde_darf_nach_einer_einzelfrage_stoppen(self):
        session_id = "unit-dialogue-round-waits-for-user"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Gehe jeden Punkt einzeln durch und erkläre ihn, damit ich "
                    "qualifiziert antworten kann."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Vier Stunden."}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Vier Stunden sind übernommen. Der Baukasten ist noch nicht "
                "vollständig. Wer darf die nächste Testphase freigeben?"
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_fragerunde_darf_nach_frage_vor_optionen_stoppen(self):
        session_id = "unit-dialogue-round-question-before-options"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "A"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Soll der Hook eine Frage vor Optionen erkennen?\n\n"
                "A — ja.\nB — nein."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_fragerunde_darf_nach_frage_und_separatem_wartesatz_stoppen(self):
        session_id = "unit-dialogue-round-question-before-wait-message"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Soll der formal nicht verifizierte Plan trotzdem als "
                    "Grundlage gelten?\n\nA — ja.\nB — nein."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Ich warte auf deine Auswahl: A oder B."
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Ich warte auf deine Auswahl: A oder B.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_interner_goal_turn_wiederholt_eine_gestellte_frage_nicht(self):
        session_id = "unit-dialogue-round-goal-continuation"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Möchtest du die zweite Probe übernehmen?"
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "<codex_internal_context source=\"goal\">\n"
                    "Continue working toward the active thread goal.\n"
                    "</codex_internal_context>"
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Ich warte auf deine Antwort."
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Ich warte auf deine Antwort.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_fragerunde_ohne_aktuelle_frage_erzeugt_keine_kunstfrage(self):
        session_id = "unit-dialogue-round-old-question-does-not-count"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Ist A richtig?"}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Ja."}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "A ist übernommen.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_fragerunde_ohne_frage_blockiert_nicht(self):
        session_id = "unit-dialogue-round-missing-question"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Gehe jeden Punkt einzeln durch und erkläre ihn."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Vier Stunden."}],
            }},
        ])
        payload = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Vier Stunden sind als Wiederherstellungsziel übernommen."
            ),
            "stop_hook_active": True,
        }
        first = self.run_guard(payload)
        self.assertEqual(first, "")
        self.assertEqual(self.run_guard(payload), "")

    def test_echte_entscheidungfrage_darf_ausserhalb_fragerunde_warten(self):
        session_id = "unit-direct-question-waits-for-user"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Prüfe den Routerstand und bereite die sichere Aktivierung vor."
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Die Prüfung ist abgeschlossen. Darf ich Herdr jetzt kontrolliert "
                "neu starten? A — ja (empfohlen). B — nein."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_echte_frage_darf_trotz_offener_plananzeige_warten(self):
        session_id = "unit-direct-question-bypasses-open-plan"
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": (
                    'tools.update_plan({"plan":['
                    '{"step":"Unabhängigen Routertest ausführen",'
                    '"status":"in_progress"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Soll ich den Routertest später ausführen?",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_beliebiges_fragezeichen_ist_keine_echte_frage(self):
        self.assertFalse(GUARD.asks_direct_question(
            "Der Bericht nennt die Datei status?.txt und ist abgeschlossen."
        ))
        self.assertFalse(GUARD.asks_direct_question(
            "Das Zitat „Soll ich später weitermachen?“ stammt aus dem Fehlerbericht."
        ))

    def test_fragerunde_darf_auf_konkret_angekuendigten_workflow_warten(self):
        session_id = "unit-dialogue-round-promised-workflow"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bin gleich soweit. Du bekommst gleich den Workflow."
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Alles klar, ich warte auf den Workflow.",
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")
        self.assertTrue(GUARD.user_promises_future_input(
            "Bin gleich soweit. Du bekommst gleich den Workflow."
        ))

    def test_fragerunde_kann_ausdruecklich_beendet_werden(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Beende die Fragerunde und arbeite den Rest selbstständig ab."
                )}],
            }},
        ])
        self.assertFalse(GUARD.dialogue_round_active(path))

    def test_neuer_arbeitsauftrag_beendet_alte_fragerunde_ohne_marker(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Alle Punkte müssen zu 100% integriert und geprüft sein."
                )}],
            }},
        ])
        self.assertFalse(GUARD.dialogue_round_active(path))

    def test_zitierter_fragerundenfehler_plus_fixauftrag_beendet_alte_runde(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Bitte Punkt für Punkt und nur eine Frage zur Zeit."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Stop hook: FRAGERUNDE OHNE EINZELFRAGE. "
                    "Bitte fixen und auch nach weiteren Bugs gucken."
                )}],
            }},
        ])
        self.assertFalse(GUARD.dialogue_round_active(path))

    def test_weiche_textregel_blockiert_hoechstens_einmal(self):
        session_id = "unit-soft-text-circuit-breaker"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Prüfe den Link."}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        payload = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Ich prüfe jetzt den Link.",
            # Manche Provider markieren auch automatische Retrys erneut als
            # ersten Stopversuch. Das darf den Zirkelbrecher nicht zurücksetzen.
            "stop_hook_active": False,
        }
        first = self.run_guard(payload)
        self.assertEqual(json.loads(first)["decision"], "block")
        self.assertEqual(self.run_guard(payload), "")

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

    def test_read_only_vollstaendigkeitspruefung_startet_keine_reparatur(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Prüfe nur read-only, ob alles umgesetzt ist; nichts ändern."
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-read-only-completion-check",
            "transcript_path": path,
            "last_assistant_message": (
                "Nein, noch nicht alles. Drei Befunde sind noch offen; dies ist "
                "nur der angeforderte Read-only-Bericht."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_completion_check_mit_eingeraeumter_restarbeit_blockiert(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "alle aufgaben erledigt?"}],
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-completion-check-open-work",
            "transcript_path": path,
            "last_assistant_message": (
                "Nein, noch nicht alle Gesamtaufgaben. Die zuletzt beauftragten "
                "UI-/Katalogänderungen sind erledigt und geprüft. Vor einem "
                "iOS-Build fehlen noch die echte iPhone-Prüfung von Dynamic Type, "
                "Offline-Neustart und Spracherkennung, der Realtime-Test mit zwei "
                "Konten auf zwei Geräten sowie der kombinierte Gesamttest. Die "
                "App ist insgesamt noch nicht build- beziehungsweise "
                "veröffentlichungsfertig."
            ),
            "stop_hook_active": False,
        })
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("Ein Statusbericht beendet den Auftrag nicht", parsed["reason"])

    def test_kurzfrage_alles_umgesetzt_mit_realer_restliste_blockiert(self):
        path = self.write_transcript([
            {"type": "user", "message": {
                "role": "user", "content": "okay alles umgesetzt?",
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-alles-umgesetzt-open-work",
            "transcript_path": path,
            "last_assistant_message": (
                "Nein, noch nicht alles. Umgesetzt und für Web/iOS im Browser "
                "geprüft sind Header, Artikelnamen und Dynamic Type. Noch offen: "
                "echte iPhone-Prüfung, Realtime-Test mit zwei Konten/Geräten, "
                "technische Tarifumstellung, Family-Zusatznutzer und Apple-"
                "In-App-Abos. Ein neuer Build oder OTA wurde noch nicht ausgegeben."
            ),
            "stop_hook_active": False,
        })
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("Ein Statusbericht beendet den Auftrag nicht", parsed["reason"])

    def test_kurzfrage_fertig_mit_knapper_absage_blockiert(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "fertig?"}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-fertig-open-work",
            "transcript_path": path,
            "last_assistant_message": (
                "Nein, noch nicht. Der Realtime-Test und die Tarifumstellung fehlen."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_reine_betriebsstatusfrage_erzeugt_keine_fortsetzung(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Geht es jetzt?"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-runtime-status-only",
            "transcript_path": path,
            "last_assistant_message": (
                "Noch nicht vollständig: Der Router ist aktiv, aber "
                "herdr-runtime ist weiterhin gestoppt."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_alte_harte_fortsetzung_gilt_nicht_fuer_neuen_nutzerprompt(self):
        session_id = "unit-followthrough-new-user-turn"
        old_fingerprint = hashlib.sha256(
            "Repariere den Router vollständig.".encode("utf-8")
        ).hexdigest()
        GUARD.set_followthrough_pending(
            session_id,
            kind="hard",
            turn_fingerprint=old_fingerprint,
        )
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Wir arbeiten künftig ohne herdr-runtime. Nimm den "
                    "Stop-Hook-Punkt bitte nur auf."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:9">Arbeite weiter.</hook_prompt>'
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Der Stop-Hook-Befund ist aufgenommen; die spätere "
                "Nachbesserung ist nicht Teil dieses Statusauftrags."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.followthrough_pending(session_id))

    def test_womit_frage_wartet_auf_nutzerentscheidung(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "weiter"}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-womit-question",
            "transcript_path": path,
            "last_assistant_message": (
                "Womit soll ich fortfahren? A: Stop-Hook committen. "
                "B: Router ohne Runtime planen."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_hook_fortsetzung_mit_unveraenderter_restarbeit_blockiert_erneut(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Baue den Auftrag fertig."}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:1">Arbeite weiter.</hook_prompt>'
                )}],
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-hook-repeats-open-work",
            "transcript_path": path,
            "last_assistant_message": "Der Auftrag ist insgesamt noch nicht fertig.",
            "stop_hook_active": True,
        })
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_belegter_nutzerblocker_darf_restarbeit_sauber_parken(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "alle aufgaben erledigt?"}],
            }},
        ])
        output = self.run_guard({
            "session_id": "unit-completion-check-user-blocker",
            "transcript_path": path,
            "last_assistant_message": (
                "Die native Pruefung bleibt als ein offener Punkt.\n\n"
                "BLOCKED_ON_USER: Nick muss den Preview-Build auf dem registrierten "
                "iPhone oeffnen und das Ergebnis bestaetigen."
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

    def test_codex_output_text_aus_transkript_wird_erkannt(self):
        # Codex 0.147 serialisiert Assistenten-Text als output_text, nicht als
        # text, und liefert beim Stop-Hook kein last_assistant_message. Der
        # Guard muss die letzte Antwort dann aus dem Transkript lesen.
        path = self.write_transcript([
            {"type": "response_item", "payload": {"type": "message", "role": "user"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": (
                    "Ich setze direkt am sicheren Handoff-Punkt fort und baue "
                    "die restlichen Schritte ab."
                )}],
            }},
        ])
        self.assertEqual(
            GUARD.last_assistant_text(path),
            "Ich setze direkt am sicheren Handoff-Punkt fort und baue die "
            "restlichen Schritte ab.",
        )
        output = self.run_guard({
            "session_id": "unit-codex-output-text",
            "transcript_path": path,
            "last_assistant_message": "",
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(output)["decision"], "block")

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
                    '{"step":"50 Alevora-Rezepte live importieren",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "TypeScript erfolgreich.\n\n"
                "BLOCKED_ON_USER: Alevora benötigt einen API-Client-Key; HTTP 401."
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
                    '{"step":"BLOCKED_ON_USER: Alevora-Liveimport braucht Client-Key",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Lokale Arbeit ist umgesetzt und geprüft.\n\n"
                "BLOCKED_ON_USER: Alevora benötigt einen API-Client-Key; HTTP 401."
            ),
            "stop_hook_active": True,
        })
        self.assertEqual(output, "")
        self.assertFalse(GUARD.audit_pending(session_id))

    def test_entscheidung_im_codex_plan_ist_ein_nutzerblocker(self):
        item = {
            "step": (
                "BLOCKED_ON_USER: Nick entscheidet Frage 8b zur Lebensdauer "
                "temporärer Tailscale-Vorschauen; Empfehlung A gilt bis zur Abnahme"
            ),
            "status": "pending",
        }
        self.assertTrue(GUARD.plan_item_blocked_on_user(item))

    def test_lieferung_eines_delegationsentwurfs_ist_ein_nutzerblocker(self):
        item = {
            "step": (
                "BLOCKED_ON_USER: Nick liefert den Delegations-/Routerentwurf; "
                "ohne ihn bleibt der finale Abschnitt bewusst offen"
            ),
            "status": "pending",
        }
        self.assertTrue(GUARD.plan_item_blocked_on_user(item))

    def test_geparkter_plan_darf_in_fragerunde_als_einzelfrage_endet(self):
        session_id = "unit-dialogue-parked-plan"
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Gehe die Entscheidungen bitte Frage für Frage durch."
                )}],
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": (
                    'tools.update_plan({"plan":['
                    '{"step":"BLOCKED_ON_USER: Nick entscheidet Frage 8b zur '
                    'Lebensdauer temporärer Tailscale-Vorschauen; Empfehlung A",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Meine Empfehlung ist A. Soll A oder B gelten?"
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_geparkter_plan_braucht_keinen_sichtbaren_abschlussmarker(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": (
                    'tools.update_plan({"plan":['
                    '{"step":"BLOCKED_ON_USER: Nick liefert den Delegations-'
                    'entwurf; ohne ihn bleibt der finale Abschnitt offen",'
                    '"status":"pending"}]})'
                ),
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-parked-plan-without-final-marker",
            "transcript_path": path,
            "last_assistant_message": (
                "Der Kandidat ist für alle entschiedenen Bereiche vorbereitet."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_bildauftrag_wird_bei_modellgrenze_an_router_zurueckgegeben(self):
        session_id = "unit-multimodal-handoff"
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Siehst du das Bild?"
                )}],
            }},
        ])
        payload = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Ich sehe die Datei, kann das Bild inhaltlich aber nicht "
                "ansehen. Das aktuell gepinnte Modell unterstützt keine "
                "Bild-Eingaben. Dafür bräuchte ich ein multimodales Modell "
                "über den Router. Was soll ich mit dem Bild machen?"
            ),
            "stop_hook_active": False,
        }
        first = self.run_guard(payload)
        self.assertEqual(json.loads(first)["decision"], "block")
        self.assertIn("MULTIMODALE ÜBERGABE", json.loads(first)["reason"])
        self.assertEqual(self.run_guard(payload), "")

    def test_reine_frage_zur_modellfaehigkeit_erzeugt_keine_delegation(self):
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "Kann das Modell Bilder sehen?"
                )}],
            }},
        ])
        output = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": "unit-image-capability-meta-question",
            "transcript_path": path,
            "last_assistant_message": (
                "Das Modell unterstützt keine Bild-Eingaben; dafür wäre ein "
                "multimodales Modell nötig."
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_modellhinweis_mit_bild_eingaben_wird_erkannt(self):
        self.assertTrue(GUARD.needs_multimodal_handoff(
            "Prüfe das Bild.",
            "Das gepinnte Modell unterstützt keine Bild-Eingaben; dafür ist "
            "ein multimodales Modell über den Router nötig.",
        ))

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
            GUARD.has_completion_attestation(
                "BLOCKED_ON_USER: Ich möchte das vielleicht später machen."
            )
        )
        self.assertTrue(
            GUARD.has_completion_attestation(
                "BLOCKED_ON_USER: Nick muss die gewünschte Zielfarbe auswählen."
            )
        )

    def test_workerstart_ausrede_erfordert_echte_fortsetzungsarbeit(self):
        session_id = "unit-worker-start-fallback-follow-through"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        self.addCleanup(GUARD.clear_followthrough_pending, session_id)
        initial_path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Repariere den Agy-Fix vollständig."}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        incomplete = (
            "Ich habe die Ursache gefunden, aber noch nichts repariert. "
            "Der Agy-Fix wurde nicht umgesetzt, weil der Codex-Worker nicht "
            "starten konnte."
        )
        first = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": initial_path,
            "last_assistant_message": incomplete,
            "stop_hook_active": False,
        })
        self.assertEqual(json.loads(first)["decision"], "block")
        self.assertTrue(GUARD.audit_pending(session_id))
        self.assertTrue(GUARD.followthrough_pending(session_id))

        no_work_path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Repariere den Agy-Fix vollständig."}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:1">Arbeite weiter.</hook_prompt>'
                )}],
            }},
        ])
        second = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": no_work_path,
            "last_assistant_message": "Der Routerfehler bleibt dokumentiert.",
            "stop_hook_active": True,
        })
        self.assertIn("FORTSETZUNG OHNE ARBEIT", json.loads(second)["reason"])

        worked_path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Repariere den Agy-Fix vollständig."}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    '<hook_prompt hook_run_id="stop:1">Arbeite weiter.</hook_prompt>'
                )}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
        ])
        completed = self.run_guard({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": worked_path,
            "last_assistant_message": "Der Agy-Fix ist umgesetzt und verifiziert.",
            "stop_hook_active": True,
        })
        self.assertEqual(completed, "")
        self.assertFalse(GUARD.audit_pending(session_id))
        self.assertFalse(GUARD.followthrough_pending(session_id))

    def test_commentary_hat_einen_zirkelbrecher(self):
        session_id = "unit-commentary-budget"
        path = self.write_transcript([
            {"type": "event_msg", "payload": {
                "type": "agent_message", "phase": "commentary",
            }},
        ])
        payload = {
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Zwischenstand",
            "stop_hook_active": True,
        }
        for _ in range(3):
            self.assertEqual(json.loads(self.run_guard(payload))["decision"], "block")
        self.assertEqual(self.run_guard(payload), "")

    def test_false_stop_flag_setzt_zirkelbudget_nicht_zurueck(self):
        session_id = "unit-commentary-false-stop-flag"
        path = self.write_transcript([
            {"type": "event_msg", "payload": {
                "type": "agent_message", "phase": "commentary",
            }},
        ])
        initial = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": "Zwischenstand",
            "stop_hook_active": False,
        }
        continuation = dict(initial, stop_hook_active=True)
        self.assertEqual(json.loads(self.run_guard(initial))["decision"], "block")
        self.assertEqual(json.loads(self.run_guard(continuation))["decision"], "block")
        self.assertEqual(json.loads(self.run_guard(initial))["decision"], "block")
        self.assertEqual(self.run_guard(initial), "")
        counter = pathlib.Path(
            GUARD.STATE_ROOT,
            f"{GUARD.safe_session_id(session_id)}-blocks-commentary",
        )
        self.assertEqual(json.loads(counter.read_text(encoding="utf-8"))["count"], 3)

    def test_zwischenfrage_mit_unfertiger_vorschau_blockiert(self):
        session_id = "unit-zwischenfrage-unfertige-vorschau"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "hast das problem jettz gefixt\n?"}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Noch nicht vollständig. Die Produktivumgebung ist wieder unangetastet, "
                "aber die lokale Web-Vorschau übernimmt den Test-Clerk-Schlüssel noch "
                "nicht korrekt. Ich muss das jetzt rein im lokalen Universal-Worktree abschließen."
            ),
            "stop_hook_active": False,
        })
        self.assertTrue(output)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")

    def test_zwischenfrage_kann_ich_weiterarbeiten_mit_restarbeit_blockiert(self):
        session_id = "unit-zwischenfrage-kann-ich-weiterarbeiten"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": (
                    "okay kann ich universal und auth jetzt weiterarbeiten? hast du deinen teil erledigt?"
                )}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Noch nicht ganz: Der lokale Auth-Vorschaupfad ist noch nicht sauber verifiziert. "
                "Du kannst im Universal/Auth-Worktree normal weiterarbeiten; ich ändere dort "
                "nichts an deinem Code, sondern prüfe und korrigiere den lokalen Exportpfad "
                "separat bis die Anmeldung mit der Testumgebung lädt."
            ),
            "stop_hook_active": False,
        })
        self.assertTrue(output)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")

    def test_mach_ich_mit_build_und_vorschau_ankuendigung_blockiert(self):
        session_id = "unit-mach-ich-build-vorschau"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "okay du machst bitte deine aufgaben auch noch fertig"}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Mach ich. Ich warte den bereits laufenden Universal/Auth-Export ab, "
                "prüfe danach dessen Ergebnis und übernehme nur noch die nötigen lokalen "
                "Vorschau-Anpassungen."
            ),
            "stop_hook_active": False,
        })
        self.assertTrue(output)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")

    def test_separater_port_start_ankuendigung_blockiert(self):
        session_id = "unit-separater-port-start"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Mach den Serverstart fertig"}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Port 4502 gehört inzwischen der Web-Session und bleibt unangetastet. "
                "Ich starte die Universal/Auth-Vorschau deshalb getrennt auf einem eigenen "
                "lokalen Port, statt eine andere Worktree-Session zu stören."
            ),
            "stop_hook_active": False,
        })
        self.assertTrue(output)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")

    def test_entscheidungfrage_nach_toolarbeit_darf_warten(self):
        session_id = "unit-unbegruendete-rueckfrage"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Richte die Vorschau ein"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "kannst weiter machen"}],
            }},
        ])
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Ja. Soll die Tailnet-Webvorschau auf Port 4502 künftig bewusst die "
                "Development-Instanz nutzen, während der EAS-TestFlight-Build Production nutzt?"
            ),
            "stop_hook_active": False,
        })
        self.assertEqual(output, "")

    def test_session_tool_activity_ueber_turns_hinweg_schuetzt_arbeitslauf(self):
        session_id = "unit-session-activity-across-turns"
        self.addCleanup(GUARD.clear_audit_pending, session_id)
        path = self.write_transcript([
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Optimiere den Export"}],
            }},
            {"type": "response_item", "payload": {"type": "custom_tool_call"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Export ist gestartet."}],
            }},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "wie ist der stand?"}],
            }},
        ])
        self.assertTrue(GUARD.session_has_tool_activity(path))
        self.assertFalse(GUARD.turn_has_tool_activity(path))
        output = self.run_guard({
            "session_id": session_id,
            "transcript_path": path,
            "last_assistant_message": (
                "Der Webexport läuft noch. Ich baue die Konfiguration jetzt deterministisch neu."
            ),
            "stop_hook_active": False,
        })
        self.assertTrue(output)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")


if __name__ == "__main__":
    unittest.main()
