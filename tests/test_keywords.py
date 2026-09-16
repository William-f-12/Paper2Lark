import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from paper2lark.errors import Paper2LarkError
from paper2lark.keywords import reconcile_keywords


def field(options=None):
    return {
        "id": "fld_keywords",
        "name": "Keywords",
        "type": "select",
        "multiple": True,
        "description": "User-maintained vocabulary",
        "style": {"color": "blue"},
        "default": ["Machine Learning"],
        "options": options or [
            {"id": "opt_ml", "name": "Machine Learning", "color": "blue"},
            {"id": "opt_kd", "name": "Knowledge Distillation", "color": "purple"},
            {"id": "opt_llm", "name": "Large Language Models", "color": "green"},
        ],
    }


class KeywordTests(unittest.TestCase):
    def test_case_and_whitespace_reuse_live_spelling_without_mutating_input(self):
        live = field()
        original = copy.deepcopy(live)
        result = reconcile_keywords({"selected_existing": ["  machine   learning  ", "MACHINE LEARNING"], "proposed_new": []}, live, 3, 8)
        self.assertEqual(result["selected"], ["Machine Learning"])
        self.assertEqual(result["new_labels"], [])
        self.assertNotIn("field_definition", result)
        self.assertEqual(live, original)

    def test_host_selected_acronym_labels_reuse_existing_canonical_options(self):
        live = field([
            {"name": "KD", "color": "blue"},
            {"name": "LLM", "color": "green"},
            {"name": "RAG", "color": "purple"},
        ])
        result = reconcile_keywords({"selected_existing": [" kd ", "llm", "RAG"], "proposed_new": []}, live, 3, 8)
        self.assertEqual(result["selected"], ["KD", "LLM", "RAG"])

    def test_late_live_option_reconciles_a_proposed_label_to_existing(self):
        result = reconcile_keywords(
            {"selected_existing": [], "proposed_new": [{"label": "Agent Memory", "concept": "persistent memory", "reason": "needed", "considered_existing": ["AI Agents"]}]},
            field([{"name": "agent   memory", "color": "yellow"}]), 3, 8,
        )
        self.assertEqual(result["selected"], ["agent   memory"])
        self.assertEqual(result["new_labels"], [])

    def test_new_labels_preserve_live_configuration_and_remove_provider_ids(self):
        live = field()
        result = reconcile_keywords(
            {"selected_existing": ["Machine Learning"], "proposed_new": [{"label": "Agent Memory", "concept": "memory", "reason": "needed", "considered_existing": []}]},
            live, 3, 8,
        )
        definition = result["field_definition"]
        self.assertEqual(result["selected"], ["Machine Learning", "Agent Memory"])
        self.assertEqual(result["new_labels"], ["Agent Memory"])
        self.assertNotIn("id", definition)
        self.assertEqual(definition["description"], live["description"])
        self.assertEqual(definition["style"], live["style"])
        self.assertEqual(definition["default"], live["default"])
        self.assertEqual(definition["options"][0], {"name": "Machine Learning", "color": "blue"})

    def test_hyphens_count_as_word_separators_and_invalid_english_is_rejected(self):
        proposal = lambda label: {"selected_existing": [], "proposed_new": [{"label": label, "concept": "c", "reason": "r", "considered_existing": []}]}
        self.assertEqual(reconcile_keywords(proposal("Multi-Modal Learning"), field(), 3, 8)["new_labels"], ["Multi-Modal Learning"])
        for label in ("Multi-Modal Large Language", "中文", "Agent, Memory", "Agent/Memory", "A sentence has punctuation.", ""):
            with self.subTest(label=label), self.assertRaises(Paper2LarkError):
                reconcile_keywords(proposal(label), field(), 3, 8)

    def test_duplicates_and_nine_distinct_labels_are_rejected_or_collapsed(self):
        duplicate = {
            "selected_existing": [],
            "proposed_new": [
                {"label": "Agent Memory", "concept": "c", "reason": "r", "considered_existing": []},
                {"label": "  agent   memory ", "concept": "c", "reason": "r", "considered_existing": []},
            ],
        }
        self.assertEqual(reconcile_keywords(duplicate, field(), 3, 8)["new_labels"], ["Agent Memory"])
        labels = [{"label": f"Topic {number}", "concept": "c", "reason": "r", "considered_existing": []} for number in range(9)]
        with self.assertRaises(Paper2LarkError):
            reconcile_keywords({"selected_existing": [], "proposed_new": labels}, field(), 3, 8)

    def test_bad_proposals_unknown_selected_and_dynamic_schemas_fail_before_output(self):
        for proposal in (
            {"selected_existing": ["Missing"], "proposed_new": []},
            {"selected_existing": [], "proposed_new": [{"label": "Agent Memory", "concept": "", "reason": "r", "considered_existing": []}]},
            {"selected_existing": [], "proposed_new": [], "extra": True},
        ):
            with self.subTest(proposal=proposal), self.assertRaises(Paper2LarkError):
                reconcile_keywords(proposal, field(), 3, 8)
        dynamic = field()
        dynamic["dynamic_options_source"] = {"table": "other"}
        with self.assertRaises(Paper2LarkError) as raised:
            reconcile_keywords({"selected_existing": [], "proposed_new": []}, dynamic, 3, 8)
        self.assertEqual(raised.exception.code, "KEYWORD_SCHEMA_UNSUPPORTED")

    def test_runtime_limits_cannot_weaken_the_fixed_contract_maxima(self):
        four_words = {"selected_existing": [], "proposed_new": [{"label": "Four English Word Label", "concept": "c", "reason": "r", "considered_existing": []}]}
        nine_labels = {"selected_existing": [], "proposed_new": [{"label": f"Topic {number}", "concept": "c", "reason": "r", "considered_existing": []} for number in range(9)]}
        with self.assertRaises(Paper2LarkError):
            reconcile_keywords(four_words, field(), 4, 8)
        with self.assertRaises(Paper2LarkError):
            reconcile_keywords(nine_labels, field(), 3, 9)

    def test_tabs_normalize_to_the_same_live_label_spelling(self):
        result = reconcile_keywords({"selected_existing": ["Machine\tLearning"], "proposed_new": []}, field(), 3, 8)
        self.assertEqual(result["selected"], ["Machine Learning"])
