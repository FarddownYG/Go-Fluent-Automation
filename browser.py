"""Playwright worker for GoFluent dashboard navigation and supported quizzes."""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QThread, pyqtSignal
from playwright.sync_api import sync_playwright, Error as PlaywrightError

from activities import inspect_dashboard, filter_by_level
from answer_bank import AnswerBank, iter_questions
from monthly_tracker import load_snapshot, save_snapshot, visible_history_groups, MONTHLY_GOAL

BASE_URL = "https://esaip.gofluent.com/app/dashboard"
EXPLORE_URL = BASE_URL + "/exploreChat"
TRAINING_URL = "https://esaip.gofluent.com/app/training"
ALLOWED_HOST = "esaip.gofluent.com"
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "gofluent_profile"
DATA = ROOT / "data"
REPORT_PATH = DATA / "dashboard_inspection.json"
RESULTS_PATH = DATA / "execution_results.json"
LOG_DIR = DATA / "logs"
LEVEL_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}
LOG_RETENTION_COUNT = 5


def prune_old_logs(keep: int = LOG_RETENTION_COUNT) -> int:
    """Keep only the newest run logs; return the number removed."""
    logs = sorted(LOG_DIR.glob("run_*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for path in logs[max(0, keep):]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class BrowserPreviewWorker(QThread):
    """Open the saved GoFluent Chromium profile for manual inspection/login."""
    status_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)
    page_changed = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        context = None
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE), headless=False,
                    viewport={"width": 1400, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                self.page_changed.emit(page.url)
                self.status_changed.emit("NAVIGATEUR OUVERT")
                self.log_message.emit("Chromium ouvert sur GoFluent avec le profil existant. Connecte-toi si nécessaire.")
                while not self._stop_event.wait(0.25):
                    if page.is_closed():
                        break
                self.log_message.emit("Fermeture du navigateur GoFluent.")
        except Exception as exc:
            self.status_changed.emit("ERREUR")
            self.log_message.emit(f"Impossible d’ouvrir Chromium : {exc}")
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            self.finished_signal.emit()


class MonthlyRefreshWorker(QThread):
    """Refresh the saved monthly count without blocking the PyQt interface."""
    log_message = pyqtSignal(str)
    snapshot_changed = pyqtSignal(str)
    result_changed = pyqtSignal(bool, str)

    def run(self):
        context = None
        success = False
        message = "Synchronisation mensuelle indisponible."
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE), headless=False,
                    viewport={"width": 1400, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()
                sync_worker = BrowserWorker()
                sync_worker.log_message.connect(self.log_message.emit)
                sync_worker.monthly_snapshot_changed.connect(self.snapshot_changed.emit)
                page.goto(TRAINING_URL, wait_until="domcontentloaded", timeout=60000)
                success = sync_worker.refresh_monthly_history(page, "bouton manuel")
                message = "Historique GoFluent actualisé." if success else "Synchronisation échouée ; dernière valeur conservée."
        except Exception as exc:
            message = f"Échec du rafraîchissement GoFluent : {exc}"
            self.log_message.emit(message)
            cached = load_snapshot()
            self.snapshot_changed.emit(json.dumps(cached, ensure_ascii=False))
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            self.result_changed.emit(success, message)


class BrowserWorker(QThread):
    status_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)
    page_changed = pyqtSignal(str)
    activity_found = pyqtSignal(str, str)
    progress_changed = pyqtSignal(int, int)
    monthly_snapshot_changed = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, requested_count=10, min_level="Tous", max_level="B2", response_delay_seconds=20, learning_need=""):
        super().__init__()
        self._stop_event = threading.Event()
        self.requested_count = requested_count
        self.min_level = min_level
        self.max_level = max_level
        self.response_delay_seconds = max(0, min(300, int(response_delay_seconds)))
        self.learning_need = str(learning_need or "").strip()
        self.completed = 0
        self.results: list[dict] = []
        self._saved_result_count = 0
        self._log_file = None
        self._log_lock = threading.Lock()
        self._captured_content: dict[str, dict] = {}
        self._current_activity_content_id: str | None = None
        self._current_activity_url: str | None = None

    def stop(self):
        self._stop_event.set()
        self.log("Arrêt demandé par l’utilisateur.")

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def reached_limit(self) -> bool:
        return self.requested_count > 0 and self.completed >= self.requested_count

    def log(self, message: str):
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        with self._log_lock:
            if self._log_file is not None:
                try:
                    self._log_file.write(line + "\n")
                    self._log_file.flush()
                except OSError:
                    pass
        self.log_message.emit(message)

    def run(self):
        self.status_changed.emit("DÉMARRAGE")
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            # Reserve one slot for the log file opened immediately below.
            removed_logs = prune_old_logs(LOG_RETENTION_COUNT - 1)
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            self.log_path = LOG_DIR / f"run_{stamp}.log"
            self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
            self.log(f"Journal détaillé : {self.log_path}")
            self.log(f"Nettoyage des journaux : {removed_logs} ancien(s) supprimé(s), conservation des {LOG_RETENTION_COUNT} plus récents.")
            self.log(f"Délai avant réponse : {self.response_delay_seconds} s par question.")
            bank = AnswerBank(DATA)
            self.log(f"Banque chargée : {len(bank.documents)} fichier(s) JSON ; historique : {len(self.completed_urls())} activité(s) terminée(s).")
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE), headless=False,
                    viewport={"width": 1400, "height": 900},
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.on("response", self.capture_content_response)
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                    self.wait_stable(page)
                    self.page_changed.emit(page.url)
                    if urlparse(page.url).hostname != ALLOWED_HOST or "login" in page.url.lower():
                        self.status_changed.emit("CONNEXION REQUISE")
                        self.log("Connecte-toi dans Chromium ; reprise automatique après redirection vers GoFluent (5 min max).")
                        deadline = time.monotonic() + 300
                        while not self.stopped() and time.monotonic() < deadline:
                            page.wait_for_timeout(500)
                            self.page_changed.emit(page.url)
                            if urlparse(page.url).hostname == ALLOWED_HOST and "login" not in page.url.lower():
                                break
                        if self.stopped():
                            return
                        if urlparse(page.url).hostname != ALLOWED_HOST or "login" in page.url.lower():
                            self.status_changed.emit("ERREUR")
                            self.log("Connexion GoFluent non détectée après 5 minutes.")
                            return
                        self.wait_stable(page)

                    self.refresh_monthly_history(page, "début")
                    if self.stopped():
                        return
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                    self.wait_stable(page)

                    if self.learning_need:
                        items = self.generate_learning_activity(page)
                        if not items:
                            self.status_changed.emit("ARRÊT" if self.stopped() else "ERREUR")
                            return
                        selected = items
                        unknown = excluded = 0
                    else:
                        if not self.wait_for_dashboard_activities(page):
                            self.log("Aucune carte d’activité visible après chargement du tableau de bord et du menu Browse.")
                            self.status_changed.emit("ERREUR")
                            return

                        report = inspect_dashboard(page)
                        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        items = [item for item in report["links"]
                                 if re.search(r"/app/dashboard/(?:mg/|resources/)", item["url"])
                                 and not re.search(r"/resources/(?:practice-speaking|practice-writing|video|article)(?:/)?$", item["url"])]
                        selected, unknown, excluded = filter_by_level(items, self.min_level, self.max_level)
                    answerable = [(item, bank.answers_for(item.get("text") or "Activité"))
                                  for item in selected]
                    completed_urls = self.completed_urls()
                    pending_count = sum(1 for item, _ in answerable if item["url"] not in completed_urls)
                    target = "sans limite" if self.requested_count == 0 else str(self.requested_count)
                    self.log(f"Objectif : {target}. {len(selected)} activité(s) dans la plage CECRL ; "
                             f"{sum(bool(answers) for _, answers in answerable)} déjà associée(s) à la banque ; "
                             f"{unknown} sans niveau et {excluded} hors plage.")
                    self.log(f"Après historique : {pending_count} activité(s) candidate(s) ; "
                             "les autres réponses seront recherchées dans les données chargées par l’interface GoFluent.")
                    self.progress_changed.emit(0, self.requested_count)

                    seen_urls: set[str] = set()
                    already_done = 0
                    halted_on_incomplete = False
                    for item, answers in answerable:
                        if self.stopped() or self.reached_limit():
                            break
                        label, url = item.get("text") or "Activité", item["url"]
                        if urlparse(url).hostname != ALLOWED_HOST or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        if url in completed_urls:
                            already_done += 1
                            continue
                        self.activity_found.emit(label, url)
                        self.log(f"Ouverture : {label}")
                        try:
                            self._current_activity_content_id = self.content_id_from_url(url)
                            self._current_activity_url = url
                            page.goto(url, wait_until="domcontentloaded", timeout=45000)
                            self.wait_stable(page)
                            self.page_changed.emit(page.url)
                            site_done, site_evidence = self.site_completion_state(page)
                            if site_done:
                                self.record(label, url, "already_completed_remote", site_evidence)
                                already_done += 1
                                self.log(f"DÉJÀ TERMINÉE SUR GOFLUENT — {label} ({site_evidence}). Activité ignorée.")
                                continue
                            if not self.open_practice(page):
                                self.record(label, url, "ignored", "Onglet Practice non détecté.")
                                self.log(f"ARRÊT — {label} : onglet Practice non détecté. Aucune activité suivante ne sera ouverte.")
                                halted_on_incomplete = True
                                break
                            self.log(f"Écran Practice/démarrage détecté : {label}.")
                            self.wait_stable(page)
                            # Completed quizzes may show their result only after
                            # opening Practice; detect it before looking for Start.
                            site_done, site_evidence = self.site_completion_state(page)
                            if site_done:
                                self.record(label, url, "already_completed_remote", site_evidence)
                                already_done += 1
                                self.log(f"DÉJÀ TERMINÉE SUR GOFLUENT — {label} ({site_evidence}). Activité ignorée.")
                                continue
                            if not self.start_activity(page):
                                self.record(label, url, "ignored", "Bouton Start non détecté sur l’écran Practice.")
                                self.log(f"ARRÊT — {label} : bouton Start non détecté. Aucune activité suivante ne sera ouverte.")
                                halted_on_incomplete = True
                                break
                            self.log(f"Bouton Start cliqué : {label}. Attente des questions.")
                            self.wait_stable(page)
                            if not answers:
                                content_id = self.content_id_from_url(url) or self._current_activity_content_id
                                document = self._captured_content.get(content_id or "")
                                unsupported = []
                                if document:
                                    bank.add_document(document, label)
                                    covered, total, unsupported = bank.answer_coverage(document)
                                    self.log(f"Données d’activité reçues : {covered}/{total} question(s) résolues par les données.")
                                    if unsupported:
                                        self.log(f"Formats non pris en charge : {', '.join(unsupported)}.")
                                    self.persist_content(content_id, document, label)
                                    answers = bank.answers_for(label)
                                if not answers:
                                    detail = (f"Formats non pris en charge : {', '.join(unsupported)}."
                                              if document and unsupported else
                                              "Aucune réponse vérifiable dans les fichiers ou les données chargées par GoFluent.")
                                    self.record(label, url, "ignored", detail)
                                    self.log(f"ARRÊT — {label} : {detail} Aucune activité suivante ne sera ouverte.")
                                    halted_on_incomplete = True
                                    break
                            result, detail = self.answer_activity(page, answers)
                            self.record(label, url, result, detail)
                            if result == "completed":
                                self.completed += 1
                                self.progress_changed.emit(self.completed, self.requested_count)
                                suffix = "" if self.requested_count == 0 else f"/{self.requested_count}"
                                self.log(f"RÉUSSIE — {label} ({self.completed}{suffix}).")
                            else:
                                outcome = {"ignored": "IGNORÉE", "error": "ERREUR", "stopped": "ARRÊT"}.get(result, result.upper())
                                self.log(f"{outcome} — {label} : {detail}")
                                if result != "stopped":
                                    self.log("Arrêt du traitement : cette activité n’est pas confirmée terminée ; aucune suivante ne sera ouverte.")
                                    halted_on_incomplete = True
                                break
                        except Exception as exc:
                            self.record(label, url, "error", str(exc)[:500])
                            self.log(f"ERREUR — {label} : {exc}")
                            halted_on_incomplete = True
                            self.log("Arrêt du traitement : activité non terminée après une erreur.")
                            break
                    if self.stopped():
                        self.status_changed.emit("ARRÊT")
                    elif halted_on_incomplete:
                        self.status_changed.emit("ERREUR")
                    else:
                        self.status_changed.emit("TERMINÉ")
                    if already_done:
                        self.log(f"Historique : {already_done} activité(s) déjà terminée(s), laissée(s) intacte(s).")
                    summary = (f"{self.completed} activité(s) confirmée(s), sans limite demandée."
                               if self.requested_count == 0 else
                               f"{self.completed}/{self.requested_count} activité(s) confirmée(s).")
                    self.log(f"Traitement terminé : {summary}")
                    if not self.stopped():
                        self.refresh_monthly_history(page, "fin")
                finally:
                    context.close()
        except PlaywrightError as exc:
            self.status_changed.emit("ERREUR")
            self.log(f"Erreur Playwright : {exc}")
        except Exception as exc:
            self.status_changed.emit("ERREUR")
            self.log(f"Erreur inattendue : {exc}")
        finally:
            self.save_results()
            self.log("Exécution terminée. Historique et journal conservés.")
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except OSError:
                    pass
                self._log_file = None
            self.finished_signal.emit()

    def generate_learning_activity(self, page) -> list[dict]:
        """Submit a custom learning need in GoFluent Explore and collect its activity link."""
        self.log("Ouverture de GoFluent Explore content pour générer un entraînement.")
        page.goto(EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
        self.wait_stable(page)
        prompt_box = page.get_by_placeholder(re.compile(r"learning needs", re.I))
        try:
            prompt_box.wait_for(state="visible", timeout=15000)
            prompt_box.fill(self.learning_need)
        except PlaywrightError:
            self.log("Champ de demande Explore content introuvable. Vérifie la page GoFluent ou la connexion.")
            return []

        before = set(self.explore_activity_links(page))
        sent = False
        try:
            form = prompt_box.locator("xpath=ancestor::form[1]")
            if form.count():
                submit = form.locator('button[type="submit"], input[type="submit"]')
                if submit.count() and submit.first.is_visible() and submit.first.is_enabled():
                    submit.first.click(timeout=3000)
                    sent = True
        except PlaywrightError:
            pass
        if not sent:
            try:
                wrapper = prompt_box.locator("xpath=ancestor::div[.//button][1]")
                buttons = wrapper.locator("button")
                if buttons.count() == 1 and buttons.first.is_visible() and buttons.first.is_enabled():
                    buttons.first.click(timeout=3000)
                    sent = True
            except PlaywrightError:
                pass
        if not sent:
            self.log("Impossible d’envoyer la demande Explore content.")
            return []

        self.log("Demande envoyée. Attente de la génération GoFluent AI (jusqu’à 2 min).")
        deadline = time.monotonic() + 120
        generated_urls: set[str] = set()
        last_new_link_at = None
        while not self.stopped() and time.monotonic() < deadline:
            try:
                page.wait_for_timeout(1000)
                current = self.explore_activity_links(page)
                new_urls = set(current) - before - generated_urls
                if new_urls:
                    generated_urls.update(new_urls)
                    last_new_link_at = time.monotonic()
                if generated_urls and last_new_link_at is not None and time.monotonic() - last_new_link_at >= 5:
                    candidates = [item for item in self.explore_activity_items(page)
                                  if item["url"] in generated_urls]
                    best = self.most_relevant_activity(candidates, self.learning_need)
                    if best:
                        self.log(f"Activité sélectionnée : {best['text']}.")
                        return [best]
                    self.log("Résultats GoFluent AI détectés, mais aucun titre ne correspond assez au thème. Aucun lien ouvert.")
                    return []
            except PlaywrightError:
                break
        if self.stopped():
            self.log("Attente de la génération interrompue.")
        else:
            self.log("Aucun lien d’activité généré détecté après 2 minutes. Aucune activité traitée.")
        return []

    @staticmethod
    def explore_activity_links(page) -> list[str]:
        return [item["url"] for item in BrowserWorker.explore_activity_items(page)]

    @staticmethod
    def explore_activity_items(page) -> list[dict[str, str]]:
        try:
            links = page.locator(
                "a[href], [role='link'][href], [role='link'][data-href], [data-href]"
            ).evaluate_all("""elements => elements
                .map(el => ({
                    href: el.href || el.getAttribute('href') || el.getAttribute('data-href'),
                    text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim()
                }))
                .filter(item => item.href)
                .map(item => ({ ...item, href: new URL(item.href, window.location.href).href }))""")
        except PlaywrightError:
            return []
        activity_links = []
        seen = set()
        ignored_paths = {"/", "/app/dashboard", "/app/dashboard/home", "/app/dashboard/training",
                         "/app/dashboard/exploreChat", "/app/training", "/login"}
        for item in links:
            parsed = urlparse(item["href"])
            path = parsed.path.rstrip("/")
            query_ids = parse_qs(parsed.query)
            has_query_id = any(
                re.fullmatch(r"[0-9a-f-]{36}", value, re.I)
                for key in ("id", "contentId", "content_id")
                for value in query_ids.get(key, [])
            )
            if (parsed.hostname != ALLOWED_HOST or
                    (path in ignored_paths and not (path == "/app/dashboard/exploreChat" and has_query_id))):
                continue
            normalized = parsed._replace(fragment="").geturl()
            if normalized not in seen:
                seen.add(normalized)
                activity_links.append({"url": normalized, "text": " ".join(item["text"].split())[:180]})
        return activity_links

    @staticmethod
    def topic_terms(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        match = re.search(r"about this topic or request:\s*(.*?)\.\s*Interpret", normalized)
        topic = match.group(1) if match else normalized
        translations = {
            "verbe": "verb", "verbes": "verb", "irregulier": "irregular", "irreguliere": "irregular",
            "grammaire": "grammar", "conjugaison": "conjugation", "temps": "tense",
            "verbaux": "verb", "verbale": "verb", "modaux": "modal", "prepositions": "preposition",
            "preposition": "preposition", "conditionnels": "conditional", "vocabulaire": "vocabulary",
            "professionnel": "professional", "professionnelle": "professional", "courriel": "email",
            "emails": "email", "mail": "email", "voyage": "travel", "voyages": "travel",
            "orientation": "direction", "ecoute": "listening", "orale": "listening", "oral": "listening",
            "lecture": "reading", "ecrite": "reading", "prononciation": "pronunciation",
            "entretien": "interview", "embauche": "interview", "questions": "question",
            "negation": "negative", "articles": "article", "determinants": "determiner",
        }
        stop_words = {"the", "and", "for", "with", "about", "english", "practice", "activity",
                      "activities", "common", "in", "to", "of", "a", "an", "use", "include",
                      "topic", "request", "target", "up", "cefr", "interpret", "french", "wording",
                      "tolerate", "spelling", "mistakes", "suitable", "exercise", "format", "when",
                      "relevant", "this", "one", "interactive", "audio"}
        terms = set()
        for token in re.findall(r"[a-z0-9]+", topic):
            translated = translations.get(token, token)
            if translated not in stop_words and len(translated) > 2:
                terms.add(translated.rstrip("s"))
        return terms

    @staticmethod
    def most_relevant_activity(items: list[dict[str, str]], learning_need: str) -> dict[str, str] | None:
        terms = BrowserWorker.topic_terms(learning_need)
        scored = []
        for index, item in enumerate(items):
            title = unicodedata.normalize("NFKD", item.get("text", "").casefold())
            title = "".join(char for char in title if not unicodedata.combining(char))
            title_terms = {token.rstrip("s") for token in re.findall(r"[a-z0-9]+", title)}
            overlap = len(terms & title_terms)
            if overlap:
                scored.append((overlap, -index, item))
        return max(scored, default=(0, 0, None))[2]

    def record(self, title: str, url: str, status: str, detail: str):
        self.results.append({"title": title, "url": url, "status": status,
                             "detail": detail, "timestamp": datetime.now(timezone.utc).isoformat()})
        self.save_results()

    def refresh_monthly_history(self, page, phase: str):
        """Count passed activities for the current month through GoFluent's History UI."""
        current_month = date.today().strftime("%Y-%m")
        self.log(f"Synchronisation de l’objectif mensuel GoFluent ({phase}).")
        try:
            page.goto(TRAINING_URL, wait_until="domcontentloaded", timeout=45000)
            self.wait_stable(page)
            history_tab = page.get_by_role("tab", name=re.compile(r"^History$", re.I))
            if history_tab.count() and history_tab.first.is_visible():
                history_tab.first.click(timeout=4000)
            page.locator("section.training-card-block").first.wait_for(state="visible", timeout=15000)

            records: list[dict] = []
            visited_pages = 0
            for _ in range(100):
                groups = visible_history_groups(page)
                if not groups:
                    raise RuntimeError("aucune date de l’historique n’est visible")
                visited_pages += 1
                month_seen = False
                dates = []
                for group in groups:
                    raw_day = group.get("date")
                    if not raw_day:
                        continue
                    dates.append(raw_day)
                    if raw_day[:7] != current_month:
                        continue
                    month_seen = True
                    for entry in group.get("records", []):
                        if (entry.get("type", "").casefold() == "activity"
                                and re.search(r"\bpassed\b", entry.get("status", ""), re.I)):
                            records.append({
                                "date": raw_day,
                                "title": entry.get("title", ""),
                                "status": entry.get("status", ""),
                            })
                # History is newest-first. Once the current month no longer
                # appears, later pages cannot contain more current-month rows.
                if dates and max(dates)[:7] < current_month:
                    break
                if not month_seen and dates and min(dates)[:7] < current_month:
                    break

                next_button = page.locator(".pagination > button").last
                if not next_button.count() or not next_button.is_enabled():
                    break
                old_page = page.locator("button.pagination-item_selected_yes").inner_text().strip()
                next_button.click(timeout=4000)
                try:
                    page.wait_for_function(
                        "old => document.querySelector('button.pagination-item_selected_yes')?.innerText.trim() !== old",
                        arg=old_page, timeout=8000,
                    )
                except Exception:
                    break

            snapshot = save_snapshot(records)
            self.monthly_snapshot_changed.emit(json.dumps(snapshot, ensure_ascii=False))
            self.log(f"Objectif mensuel synchronisé : {snapshot['count']}/{MONTHLY_GOAL} activité(s) réussie(s) distincte(s) en {current_month} ({visited_pages} page(s) d’historique parcourue(s)).")
            return True
        except Exception as exc:
            cached = load_snapshot()
            self.monthly_snapshot_changed.emit(json.dumps(cached, ensure_ascii=False))
            self.log(f"Synchronisation mensuelle indisponible : {exc}. Valeur locale conservée ({cached['count']}/{MONTHLY_GOAL}).")
            return False

    @staticmethod
    def wait_stable(page):
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightError:
            page.wait_for_timeout(800)

    @staticmethod
    def wait_for_dashboard_activities(page) -> bool:
        """Wait for the dashboard's lazy-loaded activity cards before inspecting links."""
        selector = '[role="link"][href*="/app/dashboard/mg/"], a[href*="/app/dashboard/mg/"]'

        def wait_for_cards(timeout_ms: int) -> bool:
            try:
                page.wait_for_function(
                    "selector => document.querySelector(selector) !== null",
                    arg=selector, timeout=timeout_ms)
                return True
            except PlaywrightError:
                return False

        if wait_for_cards(12000):
            return True

        # The home dashboard can initially render only its shell/course card.
        # Opening Browse through the visible UI prompts GoFluent to populate the
        # activity rail; this does not navigate away from the dashboard.
        try:
            browse = page.get_by_role("button", name=re.compile(r"^browse$", re.I))
            if browse.count() and browse.first.is_visible():
                browse.first.click(timeout=3000)
        except Exception:
            pass
        return wait_for_cards(12000)

    @staticmethod
    def content_id_from_url(url: str) -> str | None:
        """Extract the activity UUID from its dashboard URL, without guessing API routes."""
        match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27,})/?$", url, re.I)
        if match:
            return match.group(1).lower()
        query = parse_qs(urlparse(url).query)
        for key in ("id", "contentId", "content_id"):
            for value in query.get(key, []):
                if re.fullmatch(r"[0-9a-f-]{36}", value, re.I):
                    return value.lower()
        return None

    def capture_content_response(self, response):
        """Cache quiz/content JSON returned by the authenticated GoFluent UI."""
        try:
            parsed = urlparse(response.url)
            content_match = re.fullmatch(
                r"/api/v1/content-service/content/([0-9a-f-]{36})", parsed.path, re.I)
            quiz_match = re.fullmatch(
                r"/api/v1/content-service/quiz/legacy/([0-9a-f-]{36})", parsed.path, re.I)
            if parsed.hostname != ALLOWED_HOST or not (content_match or quiz_match):
                return
            if "json" not in response.headers.get("content-type", "").lower():
                return
            document = response.json()
            if not isinstance(document, dict):
                return
            # For legacy quizzes, the quiz UUID differs from the activity UUID.
            # Associate it with the activity currently opened in the visible UI.
            content_id = (self._current_activity_content_id if quiz_match
                          else content_match.group(1).lower())
            if content_match and self._current_activity_url and not self._current_activity_content_id:
                self._current_activity_content_id = content_id
            if not content_id:
                return
            if response.ok and any(True for _ in iter_questions(document)):
                self._captured_content[content_id] = document
        except Exception:
            # Network responses can disappear while navigating; ignore unrelated or non-JSON traffic.
            return

    def persist_content(self, content_id: str, document: dict, activity_title: str):
        path = DATA / f"content_{content_id}.json"
        if path.exists():
            self.log(f"Données déjà archivées : {path.name} (fichier conservé).")
            return
        stored = dict(document)
        stored["activityTitle"] = activity_title
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.log(f"Données de questions archivées : {path.name}.")

    def save_results(self):
        try:
            pending = self.results[self._saved_result_count:]
            if not pending:
                return
            old = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else []
            if not isinstance(old, list):
                old = []
            temporary = RESULTS_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(old + pending, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(RESULTS_PATH)
            self._saved_result_count += len(pending)
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"Impossible d’enregistrer le rapport : {exc}")

    @staticmethod
    def completed_urls() -> set[str]:
        try:
            entries = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            if isinstance(entries, list):
                return {item["url"] for item in entries
                        if isinstance(item, dict) and item.get("status") == "completed" and item.get("url")}
        except (OSError, json.JSONDecodeError):
            pass
        return set()

    @staticmethod
    def site_completion_state(page) -> tuple[bool, str]:
        """Recognize explicit completion labels on the activity page; never infer from a missing Start button."""
        try:
            body_text = " ".join((page.locator("body").inner_text(timeout=2500) or "").split())
        except Exception:
            body_text = ""
        complete_label = re.search(
            r"\bactivity\s+(?:complete|completed)\b|\bpractice\s+completed\b|"
            r"\byou\s+have\s+completed\b|\balready\s+completed\b|"
            r"\b(?:activité|pratique)\s+terminée\b|\bdéjà\s+terminée?\b",
            body_text, re.I)
        score = re.search(r"\b(?:quiz\s+)?score\s+\d+\s*/\s*\d+\b", body_text, re.I)
        review = re.search(r"\b(review|retake)\s+activity\b", body_text, re.I)
        if complete_label and (score or review):
            return True, complete_label.group(0)
        # Alternate result layout: score + accuracy + a review/retake action.
        if score and re.search(r"\bquiz\s+accuracy\s+\d+%", body_text, re.I) and review:
            return True, "Quiz score and review controls"

        # Some GoFluent cards expose completion only through accessible names/labels.
        try:
            labelled = page.locator('[aria-label], [title], [data-state]')
            for index in range(min(labelled.count(), 500)):
                node = labelled.nth(index)
                if not node.is_visible():
                    continue
                attributes = " ".join(filter(None, (
                    node.get_attribute("aria-label"), node.get_attribute("title"),
                    node.get_attribute("data-state"),
                )))
                if re.search(
                        r"\b(?:activity|practice)\s+(?:complete|completed)\b|"
                        r"\bcompleted\s+(?:activity|practice)\b|\balready\s+done\b|"
                        r"\b(?:activité|pratique)\s+terminée\b",
                        attributes, re.I):
                    return True, f"Libellé d’accessibilité : {attributes[:100]}"
        except Exception:
            pass
        return False, ""

    def open_practice(self, page) -> bool:
        """Open Practice, or accept activity pages that land directly on Start."""
        # Some GoFluent content (for example practical-guide activities) has no
        # Learn/Practice tab: its route already renders the Practice start card.
        # Treat that state as ready; start_activity() still performs the click.
        start_button = page.get_by_role("button", name=re.compile(r"^(?:start|begin|continue|resume|reprendre)$", re.I))
        try:
            if start_button.count() and start_button.first.is_visible():
                return True
        except Exception:
            pass

        practice_tab = page.get_by_role("tab", name=re.compile(r"^practice$", re.I))
        try:
            practice_tab.first.wait_for(state="visible", timeout=15000)
            if practice_tab.count() and practice_tab.first.is_visible():
                practice_tab.first.click(timeout=3000)
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass

        # UI builds may expose the tab as a button or a plain accessible label.
        for locator in (
            page.get_by_role("button", name=re.compile(r"^practice$", re.I)),
            page.get_by_text("Practice", exact=True),
        ):
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=3000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def start_activity(page) -> bool:
        """Start an activity after its Practice tab and answer data are ready."""
        for pattern in (re.compile(r"^(?:start|begin|continue|resume|reprendre)$", re.I),):
            button = page.get_by_role("button", name=pattern)
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                page.wait_for_timeout(700)
                return True
        # GoFluent currently marks its Start card as role="presentation".
        try:
            start = page.locator("button").filter(has_text=re.compile(r"^\s*Start(?:\s|$)", re.I))
            if start.count() and start.first.is_visible():
                start.first.click(timeout=3000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            pass
        try:
            start_text = page.get_by_text("Start", exact=True)
            if start_text.count() and start_text.first.is_visible():
                start_text.first.click(timeout=3000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            pass
        return False

    def answer_activity(self, page, answers: list[dict]) -> tuple[str, str]:
        self.log(f"Quiz démarré : {len(answers)} question(s) connues.")
        # Practical guides interleave lesson pages with questions. Their 1/18
        # progress counter is a page count, not a question number. Resume only
        # from the explicit “Question N” heading, and walk past lesson pages.
        first_index = 0
        try:
            heading = page.locator("main#quiz").inner_text(timeout=1000)
            question_match = re.search(r"\bQuestion\s+(\d+)\b", heading, re.I)
            if question_match:
                first_index = min(len(answers) - 1, max(0, int(question_match.group(1)) - 1))
                if first_index:
                    self.log(f"Reprise détectée à la question {first_index + 1}.")
        except Exception:
            pass
        for index in range(first_index, min(len(answers), 100)):
            answer = answers[index]
            if self.stopped():
                return "stopped", "Arrêt demandé."
            if self.is_complete(page):
                return "completed", "État final de réussite détecté."
            if not self.advance_to_answer_page(page, answer):
                if self.is_complete(page):
                    return "completed", "État final de réussite détecté."
                return "ignored", f"Question {index + 1} : écran de question non détecté après navigation des pages de cours."
            if self.response_delay_seconds:
                self.log(f"Question {index + 1} : attente de {self.response_delay_seconds} s avant réponse.")
                if self._stop_event.wait(self.response_delay_seconds):
                    return "stopped", "Arrêt demandé pendant le délai avant réponse."
            visible_index = self.visible_question_index(page)
            if visible_index is not None and visible_index > index:
                index = min(visible_index, len(answers) - 1)
                answer = answers[index]
                self.log(f"Alignement sur la question visible {index + 1}/{len(answers)}.")
            self.log(f"Question {index + 1}/{len(answers)} : type {answer.get('kind', 'inconnu')}.")
            ok = self.apply_answer(page, answer)
            if not ok:
                self.log(f"Question {index + 1} : impossible de relier une réponse au contrôle visible.")
                return "ignored", f"Question {index + 1} : interface inconnue ou choix non identifiable."
            before = self.question_progress(page)
            self.log(f"Question {index + 1} : réponse sélectionnée ; validation disponible.")
            if not self.click_action(page, ("Submit", "Check", "Validate", "Vérifier", "Valider")):
                self.log(f"Question {index + 1} : aucun bouton de validation actif trouvé.")
                return "ignored", f"Question {index + 1} : bouton de validation/suivant absent."
            self.log(f"Question {index + 1} : validation envoyée.")
            page.wait_for_timeout(700)
            if self.is_complete(page):
                self.log("Écran final de réussite détecté après validation.")
                return "completed", "État final de réussite détecté."
            if index == min(len(answers), 100) - 1:
                self.log("Dernière réponse validée : progression dans les pages finales et attente du résultat GoFluent.")
                self.advance_to_completion(page)
                break
            next_actions = ("Next", "Continue", "Suivant", "Continuer", "Next question")
            deadline = time.monotonic() + 8000
            after = self.question_progress(page)
            next_ready = False
            while not self.stopped() and time.monotonic() < deadline:
                if self.is_complete(page):
                    self.log("Écran final de réussite détecté après validation.")
                    return "completed", "État final de réussite détecté."
                after = self.question_progress(page)
                if before and after and before != after:
                    break
                next_ready = any(
                    self.action_available(page, name) for name in next_actions
                )
                if next_ready:
                    break
                page.wait_for_timeout(250)
            if before and after and before != after:
                self.log(f"Question suivante détectée ({after}).")
                continue  # GoFluent already advanced to the next question.
            if next_ready and self.click_action(page, next_actions):
                self.log(f"Passage manuel à la question suivante après {index + 1}.")
                page.wait_for_timeout(500)
            elif index < len(answers) - 1:
                self.log(f"Arrêt à la question {index + 1} : aucun contrôle de progression détecté.")
                return "ignored", f"Question {index + 1} validée, mais passage à la suivante non détecté."
        if self.wait_for_completion(page, timeout_ms=12000):
            self.log("Écran final de réussite confirmé après attente de mise à jour GoFluent.")
            return "completed", "État final de réussite détecté."
        # GoFluent can persist the last answer but leave the SPA on the final
        # question. Reload once so the server-rendered result state can appear.
        if not self.stopped():
            try:
                self.log("Écran final absent après 12 s ; rechargement unique pour vérifier le résultat enregistré.")
                page.reload(wait_until="domcontentloaded", timeout=20000)
                self.wait_stable(page)
                if self.is_complete(page):
                    self.log("Écran final confirmé après rechargement de l’activité.")
                    return "completed", "État final de réussite détecté après rechargement."
            except PlaywrightError as exc:
                self.log(f"Rechargement de vérification impossible : {exc}")
        self.log("Toutes les réponses connues ont été traitées, mais le marqueur final est absent.")
        return "ignored", "Fin ou état final non confirmé après les réponses connues."

    def advance_to_answer_page(self, page, answer: dict, max_pages: int = 24) -> bool:
        """Skip informational practical-guide pages until a verifiable answer UI appears."""
        next_actions = ("Next", "Continue", "Suivant", "Continuer", "Next question")
        for skipped in range(max_pages + 1):
            if self.stopped() or self.is_complete(page):
                return False
            if self.has_answer_controls(page, answer) and self.has_visible_action(
                    page, ("Submit", "Check", "Validate", "Vérifier", "Valider")):
                if skipped:
                    self.log(f"Pages de cours ignorées : {skipped} ; écran de question détecté.")
                return True
            if skipped >= max_pages or not self.click_action(page, next_actions):
                return False
            page.wait_for_timeout(250)
        return False

    def advance_to_completion(self, page, max_pages: int = 24):
        """After the last known question, follow bounded lesson pages to the result screen."""
        next_actions = ("Next", "Continue", "Suivant", "Continuer", "Next question")
        for step in range(max_pages):
            if self.stopped() or self.is_complete(page):
                return
            if not self.click_action(page, next_actions):
                return
            page.wait_for_timeout(350)
            if self.is_complete(page):
                self.log("Écran final de réussite détecté après les dernières pages de l’activité.")
                return

    @staticmethod
    def has_visible_action(page, names: tuple[str, ...]) -> bool:
        for name in names:
            try:
                button = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
                if button.count() and button.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def visible_question_index(page) -> int | None:
        try:
            text = page.locator("main#quiz").inner_text(timeout=1000)
            match = re.search(r"\bQuestion\s+(\d+)\b", text, re.I)
            return max(0, int(match.group(1)) - 1) if match else None
        except Exception:
            return None

    @staticmethod
    def has_answer_controls(page, answer: dict) -> bool:
        try:
            if answer.get("kind") == "choice":
                controls = page.locator('main#quiz input[type="radio"], main#quiz input[type="checkbox"], main#quiz [role="radio"], main#quiz [role="checkbox"]')
            elif answer.get("kind") == "blocks":
                controls = page.locator('main#quiz #source-container [class*="letterBlock"]')
            else:
                controls = page.locator('main#quiz input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]):not([type="range"]), main#quiz textarea')
            return any(controls.nth(i).is_visible() for i in range(min(controls.count(), 100)))
        except Exception:
            return False

    def wait_for_completion(self, page, timeout_ms: int = 12000) -> bool:
        """Allow the final submission/score screen to render before deciding the run failed."""
        deadline = time.monotonic() + timeout_ms / 1000
        while not self.stopped() and time.monotonic() < deadline:
            if self.is_complete(page):
                return True
            try:
                page.wait_for_timeout(250)
            except PlaywrightError:
                return False
        return self.is_complete(page)

    @staticmethod
    def apply_answer(page, answer: dict) -> bool:
        if answer["kind"] == "text":
            fields = page.locator("input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea")
            visible = [fields.nth(i) for i in range(fields.count()) if fields.nth(i).is_visible() and fields.nth(i).is_enabled()]
            if len(visible) < len(answer["answers"]):
                return False
            for field, value in zip(visible, answer["answers"]):
                field.fill(value)
            return True
        if answer["kind"] == "blocks":
            # GoFluent's current quiz UI renders these as letterBlock sources
            # and receiverDroppable targets inside the scrambled-letters widget.
            # Match exact visible text and receiver order from the quiz payload.
            sources = page.locator('main#quiz #source-container [class*="letterBlock"]')
            targets = page.locator('main#quiz [class*="receiverDroppable"]')
            if not sources.count() or not targets.count():
                return False
            for value in answer["answers"]:
                text = str(value.get("text", "")).strip()
                receiver_index = value.get("receiver_index")
                if not text or not isinstance(receiver_index, int) or receiver_index >= targets.count():
                    return False
                source = None
                for index in range(sources.count()):
                    candidate = sources.nth(index)
                    try:
                        if candidate.is_visible() and " ".join(candidate.inner_text().split()) == text:
                            source = candidate
                            break
                    except Exception:
                        continue
                target_by_id = page.locator(f'main#quiz #receiver-{receiver_index}')
                target = target_by_id if target_by_id.count() else targets.nth(receiver_index)
                if source is None or not target.is_visible():
                    return False
                try:
                    source_box = source.bounding_box()
                    target_box = target.bounding_box()
                    if not source_box or not target_box:
                        return False
                    # GoFluent uses dnd-kit pointer sensors (not HTML draggable),
                    # so dispatch a real pointer-style mouse drag between centers.
                    page.mouse.move(source_box["x"] + source_box["width"] / 2,
                                    source_box["y"] + source_box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(target_box["x"] + target_box["width"] / 2,
                                    target_box["y"] + target_box["height"] / 2, steps=12)
                    page.mouse.up()
                    page.wait_for_timeout(150)
                    if text not in " ".join(target.inner_text().split()):
                        return False
                except Exception:
                    return False
            return True
        for value in answer["answers"]:
            if isinstance(value, dict) and value.get("id"):
                try:
                    option = page.locator(
                        f'label:has(input[type="radio"][value="{value["id"]}"]), '
                        f'label:has(input[type="checkbox"][value="{value["id"]}"])'
                    )
                    if option.count() and option.first.is_visible():
                        radio = option.first.locator('input[type="radio"], input[type="checkbox"]')
                        if radio.count():
                            radio.first.check(force=True, timeout=2000)
                        else:
                            option.first.click(timeout=2000)
                        continue
                    option = page.locator(f'[role="radio"][data-option-id="{value["id"]}"], [role="checkbox"][data-option-id="{value["id"]}"]')
                    if option.count() and option.first.is_visible():
                        option.first.click(timeout=2000)
                        continue
                except Exception:
                    pass
            text_value = value.get("text", "") if isinstance(value, dict) else value
            candidates = [page.get_by_label(text_value, exact=True), page.get_by_role("radio", name=text_value, exact=True),
                          page.get_by_role("checkbox", name=text_value, exact=True), page.get_by_text(text_value, exact=True)]
            selected = False
            for locator in candidates:
                try:
                    if locator.count() and locator.first.is_visible():
                        locator.first.click(timeout=2000)
                        selected = True
                        break
                except Exception:
                    continue
            if not selected:
                return False
        return True

    @staticmethod
    def click_action(page, names: tuple[str, ...]) -> bool:
        for name in names:
            locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
            try:
                if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
                    locator.first.click(timeout=3000)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def action_available(page, name: str) -> bool:
        locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
        try:
            locator.first.wait_for(state="visible", timeout=150)
            return locator.first.is_enabled()
        except PlaywrightError:
            return False
        except Exception:
            return False

    @staticmethod
    def question_progress(page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=1000)
            match = re.search(r"\b(\d+)\s*/\s*(\d+)\b", text)
            return f"{match.group(1)}/{match.group(2)}" if match else ""
        except Exception:
            return ""

    @staticmethod
    def is_complete(page) -> bool:
        try:
            text = " ".join(page.locator("body").inner_text(timeout=1000).lower().split())
        except Exception:
            return False
        # Require a result screen signature. Generic words like "well done" can
        # appear in lesson text and must not count as proof of completion.
        score_result = re.search(r"\b(?:quiz\s+)?score\s+\d+\s*/\s*\d+\b", text)
        review = "review activity" in text or "retake activity" in text
        complete_label = bool(re.search(
            r"\bactivity\s+(?:complete|completed)\b|\bquiz\s+completed\b|"
            r"\byou\s+have\s+completed\b|\bactivité\s+terminée\b|\bquiz\s+terminé\b",
            text, re.I))
        return bool(score_result and review and complete_label)
