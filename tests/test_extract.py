"""Tests for the hand-written parsers in src/extract.py.

Run with:  python -m unittest discover tests
No third-party dependencies.
"""

import unittest

from src.extract import (find_references, parse_header, sentences, slugify,
                         split_sections, section_tree, prose_only)


HEADER = """PEP: 604
Title: Allow writing union types as ``X | Y``
Author: Philippe PRADOS <python@quatuor.org>,
        Maggie Moss <maggiebmoss@gmail.com>
Status: Final
Type: Standards Track
Python-Version: 3.10
Post-History: 28-Aug-2019,
              05-Aug-2020

Abstract
========

This PEP proposes overloading the ``|`` operator.
"""


class HeaderParserTests(unittest.TestCase):
    def test_simple_fields(self):
        h = parse_header(HEADER)
        self.assertEqual(h["PEP"], "604")
        self.assertEqual(h["Status"], "Final")
        self.assertEqual(h["Python-Version"], "3.10")

    def test_continuation_lines_are_joined(self):
        h = parse_header(HEADER)
        self.assertIn("Maggie Moss", h["Author"])
        self.assertEqual(h["Post-History"], "28-Aug-2019, 05-Aug-2020")

    def test_stops_at_first_blank_line(self):
        h = parse_header(HEADER)
        self.assertNotIn("Abstract", h)
        self.assertEqual(len(h), 7)

    def test_empty_value(self):
        self.assertEqual(parse_header("PEP: 1\nRequires:\nTitle: x\n")["Requires"], "")


DOC = """PEP: 1
Title: Demo

Abstract
========

Intro text.

Specification
=============

Spec text.

Sub Topic
---------

Sub text with :pep:`484` reference.

Deeper
~~~~~~

Deep text.

Rejected Ideas
==============

Angle Brackets
--------------

We rejected angle brackets because the scanner does not pair them.
"""


class SectionSplitterTests(unittest.TestCase):
    def test_titles_and_levels(self):
        secs = split_sections(DOC)
        got = [(s.title, s.level) for s in secs]
        self.assertEqual(got, [("Header", 0), ("Abstract", 1), ("Specification", 1),
                               ("Sub Topic", 2), ("Deeper", 3), ("Rejected Ideas", 1),
                               ("Angle Brackets", 2)])

    def test_level_is_by_first_appearance_not_by_character(self):
        # Here '-' appears first, so it is level 1 and '=' becomes level 2.
        doc = "A\n---\n\ntext\n\nB\n===\n\ntext\n"
        secs = split_sections(doc)
        self.assertEqual([(s.title, s.level) for s in secs[1:]], [("A", 1), ("B", 2)])

    def test_path_tracks_nesting(self):
        secs = split_sections(DOC)
        deeper = next(s for s in secs if s.title == "Deeper")
        self.assertEqual(deeper.path, ["Specification", "Sub Topic", "Deeper"])
        self.assertEqual(deeper.path_str, "Specification > Sub Topic > Deeper")

    def test_body_excludes_subsections(self):
        secs = split_sections(DOC)
        spec = next(s for s in secs if s.title == "Specification")
        self.assertIn("Spec text", spec.body)
        self.assertNotIn("Sub text", spec.body)

    def test_underline_must_be_at_least_title_length(self):
        doc = "Long Title Here\n===\n\ntext\n"
        self.assertEqual(len(split_sections(doc)), 1)  # only the Header pseudo-section

    def test_section_tree(self):
        secs = split_sections(DOC)
        tree = section_tree(secs)
        titles = {i: s.title for i, s in enumerate(secs)}
        rejected = next(i for i, t in titles.items() if t == "Rejected Ideas")
        self.assertEqual([titles[c] for c in tree[rejected]], ["Angle Brackets"])


class ReferenceFinderTests(unittest.TestCase):
    def test_all_four_forms(self):
        text = ("See :pep:`484` and PEP 526, also PEP-544 and `PEP 561 <https://x>`_ "
                "and the :pep:`spec of PEP 484 <484#anchor>` role.")
        nums = sorted(r.number for r in find_references(text))
        self.assertEqual(nums, [484, 484, 526, 544, 561])

    def test_label_form_counts_once(self):
        refs = find_references(":pep:`spec of PEP 484 <484#anchor>`")
        self.assertEqual([r.number for r in refs], [484])
        self.assertEqual(refs[0].form, "rst-role")

    def test_plural_list(self):
        refs = find_references("Static typing as defined by PEPs 484, 526, 544, 560, and 563 was built.")
        self.assertEqual([r.number for r in refs], [484, 526, 544, 560, 563])

    def test_role_wrapping_across_lines(self):
        refs = find_references(":pep:`585` proposes to :pep:`expose\nparameters <585#x>` at runtime")
        self.assertEqual([r.number for r in refs], [585, 585])

    def test_self_reference_and_label_lines_skipped(self):
        text = ".. _PEP 544 rationale:\n\nThis PEP (PEP 544) builds on :pep:`484`."
        refs = find_references(text, "Abstract", self_number=544)
        self.assertEqual([r.number for r in refs], [484])
        self.assertEqual(refs[0].section, "Abstract")

    def test_snippet_has_context(self):
        text = "x" * 200 + " introduced in :pep:`647` " + "y" * 200
        r = find_references(text)[0]
        self.assertIn("introduced in :pep:`647`", r.snippet)
        self.assertLess(len(r.snippet), 200)


class TextHelperTests(unittest.TestCase):
    def test_sentences_skip_code_blocks_and_abbreviations(self):
        text = "First one, i.e. the start. Example::\n\n    code = 1  # PEP 999\n\nSecond one."
        self.assertEqual(sentences(text), ["First one, i.e. the start.", "Example: Second one."])

    def test_prose_only_removes_indented_block(self):
        self.assertNotIn("PEP 999", prose_only("Example::\n\n    x  # PEP 999\n\nAfter."))

    def test_slugify(self):
        self.assertEqual(slugify("3. Extend ``isinstance()`` to accept ``Union`` ?"),
                         "extend-isinstance-to-accept-union")


if __name__ == "__main__":
    unittest.main()
