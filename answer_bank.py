"""Load exported GoFluent quiz data and resolve solutions to visible answers."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def iter_questions(document: Any) -> Iterator[dict[str, Any]]:
    """Walk nested quiz documents; only QUESTION records count as questions."""
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type") == "QUESTION" or node.get("contentItemType") == "QUESTION":
                yield node
            else:
                stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def _answer_for(solution_ids: list[str], options: list[dict[str, Any]]) -> list[dict[str, str]] | None:
    by_id = {str(option.get("id")): option for option in options if option.get("id")}
    if not solution_ids or any(str(value) not in by_id for value in solution_ids):
        return None
    return [{"id": str(value), "text": str(by_id[str(value)].get("text", ""))}
            for value in solution_ids]


def resolve_question(question: dict[str, Any]) -> dict[str, Any] | None:
    """Return answer text and type only when every solution maps to an option."""
    kind = str(question.get("questionType", "")).upper()
    if kind in {"MULTIPLE_CHOICE", "TRUE_OR_FALSE"}:
        answers = _answer_for(question.get("solutions", []), question.get("options", []))
        if answers and all(answer["text"] for answer in answers):
            return {"kind": "choice", "answers": answers}
    if kind == "SCRAMBLED_LETTERS":
        # GoFluent stores the correct letter sequence as solution option IDs.
        # Preserve that order and route every tile into its owning receiver.
        blocks: list[dict[str, Any]] = []
        receivers = question.get("receivers", [])
        if not receivers:
            return None
        for receiver_index, receiver in enumerate(receivers):
            mapped = _answer_for(receiver.get("solutions", []), receiver.get("options", []))
            if not mapped or not all(answer["text"] for answer in mapped):
                return None
            blocks.extend({**answer, "receiver_index": receiver_index} for answer in mapped)
        return {"kind": "blocks", "answers": blocks} if blocks else None
    if kind == "FILL_IN_THE_GAP":
        receivers = question.get("receivers", [])
        receiver_types = {str(receiver.get("type", "")).upper() for receiver in receivers}
        if not receivers or not receiver_types:
            return None
        if receiver_types <= {"TEXT_BLOCK", "TEXT_BLOCKS"}:
            blocks: list[dict[str, Any]] = []
            for receiver_index, receiver in enumerate(receivers):
                mapped = _answer_for(receiver.get("solutions", []), receiver.get("options", []))
                if not mapped or not all(answer["text"] for answer in mapped):
                    return None
                blocks.extend({**answer, "receiver_index": receiver_index} for answer in mapped)
            return {"kind": "blocks", "answers": blocks} if blocks else None
        if not receiver_types <= {"TEXT_INPUT", "TEXT_INPUTS"}:
            return None
        answers: list[str] = []
        for receiver in receivers:
            mapped = _answer_for(receiver.get("solutions", []), receiver.get("options", []))
            if not mapped or not all(answer["text"] for answer in mapped):
                return None
            answers.extend(answer["text"] for answer in mapped)
        if answers:
            return {"kind": "text", "answers": answers}
    return None


class AnswerBank:
    def __init__(self, directory: Path):
        self.documents: list[dict[str, Any]] = []
        self.by_title: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(document, dict):
                continue
            self.documents.append(document)
            title = normalize(str(document.get("activityTitle") or document.get("title", "")))
            if title:
                self.by_title[title] = document

    def match_activity(self, title: str) -> dict[str, Any] | None:
        candidate = normalize(title)
        if candidate in self.by_title:
            return self.by_title[candidate]
        noise = {"the", "a", "an", "vocabulary", "grammar", "quiz",
                 "a1", "a2", "b1", "b2", "c1", "c2"}
        candidate_words = set(candidate.split()) - noise
        matches = []
        for key, doc in self.by_title.items():
            key_words = set(key.split()) - noise
            if key and (key in candidate or candidate in key
                        or (key_words and key_words <= set(candidate.split()))
                        or (candidate_words and candidate_words <= set(key.split()))):
                matches.append(doc)
        return matches[0] if len(matches) == 1 else None

    def answers_for(self, activity_title: str) -> list[dict[str, Any]]:
        document = self.match_activity(activity_title)
        if not document:
            return []
        questions = list(iter_questions(document))
        if not questions:
            return []
        # Answers are consumed in question order by the browser worker. A partial
        # list would shift later answers onto the wrong questions, so require a
        # verified mapping for every question before returning any of them.
        answers = [resolve_question(question) for question in questions]
        if any(answer is None for answer in answers):
            return []
        return answers

    def answer_coverage(self, document: dict[str, Any]) -> tuple[int, int, list[str]]:
        """Summarize mapped answers and unsupported question formats for logs."""
        questions = list(iter_questions(document))
        unsupported_types: dict[str, set[str]] = {}
        for question in questions:
            if resolve_question(question) is None:
                subtype = str(question.get("questionSubType") or question.get("questionType") or "unknown")
                receiver_types = {str(receiver.get("type", "unknown"))
                                  for receiver in question.get("receivers", [])}
                unsupported_types.setdefault(subtype, set()).update(receiver_types)
        unsupported = [
            subtype if not types else f"{subtype} ({', '.join(sorted(types))})"
            for subtype, types in sorted(unsupported_types.items())
        ]
        return sum(resolve_question(question) is not None for question in questions), len(questions), unsupported

    def add_document(self, document: dict[str, Any], activity_title: str) -> int:
        """Index a content document received by the authenticated site UI."""
        document = dict(document)
        document["activityTitle"] = activity_title
        title = normalize(activity_title)
        if title:
            self.by_title[title] = document
        self.documents.append(document)
        return sum(resolve_question(question) is not None for question in iter_questions(document))
