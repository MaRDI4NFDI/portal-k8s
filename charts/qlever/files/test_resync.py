#!/usr/bin/env python3
"""Tests for resync.py.

Pure stdlib, so they run anywhere:  python3 charts/qlever/files/test_resync.py
(pytest discovers them too).

The munge rules are a port of the WDQS Munger; these tests pin them, because a
silent divergence would make resync fight the streaming updater forever.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resync  # noqa: E402
from resync import (Drift, Uris, build_operations, diff, munge, normalise,  # noqa: E402
                    parse_nt, parse_since, ser_triple, split_scope)

BASE = "https://example.org"
URIS = Uris(BASE)

RDF_TYPE = resync.RDF_TYPE
WB = resync.ONTOLOGY_NS


def U(iri):
    return ("U", iri)


def L(lex, datatype=None, lang=None):
    return ("L", lex, datatype, lang)


def B(label):
    return ("B", label)


ENTITY = U(BASE + "/entity/Q1")
STATEMENT = U(BASE + "/entity/statement/Q1-DEAD-BEEF")
ENTITY_DATA = U(BASE + "/wiki/Special:EntityData/Q1")
REFERENCE = U(BASE + "/reference/abc123")
VALUE = U(BASE + "/value/def456")
SITELINK = U(BASE + "/wiki/Imaginary_unit")


class NTriplesTest(unittest.TestCase):
    def test_round_trip(self):
        cases = [
            (ENTITY, U(RDF_TYPE), U(WB + "Item")),
            (ENTITY, U("http://www.w3.org/2000/01/rdf-schema#label"),
             L("imaginary unit", None, "en")),
            (ENTITY, U(BASE + "/prop/direct/P1"), L("3.14", resync.XSD + "decimal")),
            (STATEMENT, U(WB + "rank"), U(WB + "NormalRank")),
            (B("genid1"), U(RDF_TYPE), U("http://www.w3.org/2002/07/owl#Restriction")),
        ]
        text = "\n".join(ser_triple(t) for t in cases)
        self.assertEqual(list(parse_nt(text)), cases)

    def test_escapes_survive(self):
        nasty = L('quote " backslash \\ newline \n tab \t unicode ☃ \U0001F600')
        triple = (ENTITY, U(BASE + "/prop/direct/P1"), nasty)
        self.assertEqual(list(parse_nt(ser_triple(triple))), [triple])

    def test_parses_wikibase_output(self):
        text = (
            '<%s/entity/Q1> <http://www.w3.org/2000/01/rdf-schema#label> "i"@en .\n'
            "# a comment\n"
            "\n"
            '<%s/entity/Q1> <http://schema.org/version> "1"^^<%sinteger> .\n'
            % (BASE, BASE, resync.XSD))
        triples = list(parse_nt(text))
        self.assertEqual(len(triples), 2)
        self.assertEqual(triples[1][2], L("1", resync.XSD + "integer"))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            list(parse_nt("this is not n-triples"))


class MungeTest(unittest.TestCase):
    def dump(self):
        """A miniature but structurally complete Special:EntityData dump."""
        return [
            # entity-data node: dropped, three predicates move to the entity
            (ENTITY_DATA, U(RDF_TYPE), U("http://schema.org/Dataset")),
            (ENTITY_DATA, U("http://schema.org/about"), ENTITY),
            (ENTITY_DATA, U("http://creativecommons.org/ns#license"),
             U("http://creativecommons.org/publicdomain/zero/1.0/")),
            (ENTITY_DATA, U("http://schema.org/softwareVersion"), L("1.0.0")),
            (ENTITY_DATA, U("http://schema.org/version"), L("42", resync.XSD + "integer")),
            (ENTITY_DATA, U("http://schema.org/dateModified"),
             L("2026-08-18T00:00:00Z", resync.XSD + "dateTime")),
            (ENTITY_DATA, U(WB + "statements"), L("7", resync.XSD + "integer")),
            # the entity
            (ENTITY, U(RDF_TYPE), U(WB + "Item")),
            (ENTITY, U("http://www.w3.org/2000/01/rdf-schema#label"), L("i", None, "en")),
            (ENTITY, U("http://schema.org/name"), L("i", None, "en")),
            (ENTITY, U("http://www.w3.org/2004/02/skos/core#prefLabel"), L("i", None, "en")),
            (ENTITY, U("http://schema.org/description"), L("unit", None, "en")),
            (ENTITY, U(BASE + "/prop/P1"), STATEMENT),
            # a neighbouring entity described alongside: dropped
            (U(BASE + "/entity/Q2"), U("http://www.w3.org/2000/01/rdf-schema#label"),
             L("other", None, "en")),
            # the statement
            (STATEMENT, U(RDF_TYPE), U(WB + "Statement")),
            (STATEMENT, U(RDF_TYPE), U(WB + "BestRank")),
            (STATEMENT, U(WB + "rank"), U(WB + "NormalRank")),
            (STATEMENT, U(BASE + "/prop/statement/value/P1"), VALUE),
            (STATEMENT, U("http://www.w3.org/ns/prov#wasDerivedFrom"), REFERENCE),
            # a statement that nothing links to: dropped
            (U(BASE + "/entity/statement/Q1-ORPHAN"), U(WB + "rank"), U(WB + "NormalRank")),
            # reference and value nodes
            (REFERENCE, U(RDF_TYPE), U(WB + "Reference")),
            (REFERENCE, U(BASE + "/prop/reference/P2"), L("src")),
            (VALUE, U(RDF_TYPE), U(WB + "QuantityValue")),
            (VALUE, U(WB + "quantityAmount"), L("1", resync.XSD + "decimal")),
            # property ontology: only rdf:type survives
            (U(BASE + "/prop/direct/P1"), U(RDF_TYPE),
             U("http://www.w3.org/2002/07/owl#ObjectProperty")),
            (U(BASE + "/prop/direct/P1"), U("http://example.org/other"), L("x")),
            # novalue class: kept whole, including the blank node
            (U(BASE + "/prop/novalue/P1"), U("http://www.w3.org/2002/07/owl#complementOf"),
             B("genid1")),
            (B("genid1"), U(RDF_TYPE), U("http://www.w3.org/2002/07/owl#Restriction")),
            # sitelink
            (SITELINK, U(RDF_TYPE), U("http://schema.org/Article")),
            (SITELINK, U("http://schema.org/about"), ENTITY),
            # the wiki group
            (U(BASE + "/"), U(WB + "wikiGroup"), L("mathematics")),
            # something genuinely unknown: dropped
            (U("https://elsewhere.invalid/thing"), U("http://example.org/p"), L("y")),
        ]

    def munged(self):
        return set(munge(self.dump(), "Q1", URIS))

    def test_entity_data_predicates_move_to_the_entity(self):
        out = self.munged()
        self.assertIn((ENTITY, U("http://schema.org/version"),
                       L("42", resync.XSD + "integer")), out)
        self.assertIn((ENTITY, U(WB + "statements"), L("7", resync.XSD + "integer")), out)
        self.assertIn((ENTITY, U("http://schema.org/dateModified"),
                       L("2026-08-18T00:00:00Z", resync.XSD + "dateTime")), out)

    def test_entity_data_node_disappears_entirely(self):
        self.assertEqual([t for t in self.munged() if t[0] == ENTITY_DATA], [])

    def test_license_and_software_version_are_dropped(self):
        out = self.munged()
        self.assertNotIn(U("http://creativecommons.org/ns#license"), {t[1] for t in out})
        self.assertNotIn(U("http://schema.org/softwareVersion"), {t[1] for t in out})

    def test_duplicate_labels_are_dropped(self):
        out = self.munged()
        predicates = {t[1] for t in out if t[0] == ENTITY}
        self.assertNotIn(U("http://schema.org/name"), predicates)
        self.assertNotIn(U("http://www.w3.org/2004/02/skos/core#prefLabel"), predicates)
        self.assertIn(U("http://www.w3.org/2000/01/rdf-schema#label"), predicates)
        self.assertIn(U("http://schema.org/description"), predicates)

    def test_boilerplate_types_are_dropped_but_others_kept(self):
        out = self.munged()
        self.assertNotIn((ENTITY, U(RDF_TYPE), U(WB + "Item")), out)
        self.assertNotIn((STATEMENT, U(RDF_TYPE), U(WB + "Statement")), out)
        self.assertNotIn((REFERENCE, U(RDF_TYPE), U(WB + "Reference")), out)
        # ... these two are informative and stay
        self.assertIn((STATEMENT, U(RDF_TYPE), U(WB + "BestRank")), out)
        self.assertIn((VALUE, U(RDF_TYPE), U(WB + "QuantityValue")), out)

    def test_other_entities_are_dropped(self):
        self.assertEqual(
            [t for t in self.munged() if t[0] == U(BASE + "/entity/Q2")], [])

    def test_unreachable_statement_is_dropped(self):
        self.assertEqual(
            [t for t in self.munged()
             if t[0] == U(BASE + "/entity/statement/Q1-ORPHAN")], [])

    def test_property_namespace_keeps_only_types(self):
        out = self.munged()
        prop = {t for t in out if t[0] == U(BASE + "/prop/direct/P1")}
        self.assertEqual(prop, {(U(BASE + "/prop/direct/P1"), U(RDF_TYPE),
                                 U("http://www.w3.org/2002/07/owl#ObjectProperty"))})

    def test_novalue_class_and_its_blank_node_are_kept(self):
        out = self.munged()
        self.assertIn((U(BASE + "/prop/novalue/P1"),
                       U("http://www.w3.org/2002/07/owl#complementOf"), B("genid1")), out)
        self.assertIn((B("genid1"), U(RDF_TYPE),
                       U("http://www.w3.org/2002/07/owl#Restriction")), out)

    def test_sitelinks_and_wikigroup_are_kept(self):
        out = self.munged()
        self.assertIn((SITELINK, U("http://schema.org/about"), ENTITY), out)
        self.assertIn((U(BASE + "/"), U(WB + "wikiGroup"), L("mathematics")), out)

    def test_unknown_subjects_are_dropped(self):
        self.assertEqual(
            [t for t in self.munged() if t[0] == U("https://elsewhere.invalid/thing")], [])

    def test_oversized_literals_are_trimmed(self):
        big = "x" * (resync.MAX_LITERAL_LENGTH + 500)
        out = munge([(ENTITY, U(BASE + "/prop/direct/P1"), L(big, None, "en"))],
                    "Q1", URIS)
        self.assertEqual(len(out[0][2][1]), resync.MAX_LITERAL_LENGTH)
        self.assertEqual(out[0][2][3], "en")


class ScopeTest(unittest.TestCase):
    def test_statement_range_excludes_similar_ids(self):
        lo, hi = URIS.statement_range("Q1399")
        self.assertLess(lo, BASE + "/entity/statement/Q1399-ABC")
        self.assertGreater(hi, BASE + "/entity/statement/Q1399-ABC")
        # Q13990-... must fall outside, or resyncing Q1399 would delete it
        self.assertGreater(BASE + "/entity/statement/Q13990-ABC", hi)

    def test_split_puts_shared_nodes_out_of_reach(self):
        triples = [
            (ENTITY, U(WB + "statements"), L("7", resync.XSD + "integer")),
            (STATEMENT, U(WB + "rank"), U(WB + "NormalRank")),
            (SITELINK, U("http://schema.org/about"), ENTITY),
            (REFERENCE, U(BASE + "/prop/reference/P2"), L("src")),
            (VALUE, U(WB + "quantityAmount"), L("1", resync.XSD + "decimal")),
            (B("genid1"), U(RDF_TYPE), U("http://www.w3.org/2002/07/owl#Restriction")),
        ]
        owned, shared, skipped = split_scope(triples, "Q1", URIS)
        self.assertEqual({t[0] for t in owned}, {ENTITY, STATEMENT, SITELINK})
        self.assertEqual({t[0] for t in shared}, {REFERENCE, VALUE})
        self.assertEqual(skipped, 1)


class NormaliseTest(unittest.TestCase):
    def test_numeric_datatypes_compare_by_value(self):
        # QLever folds these into doubles and hands them back re-serialised
        self.assertEqual(normalise(L("1", resync.XSD + "decimal")),
                         normalise(L("1.0", resync.XSD + "double")))
        self.assertEqual(normalise(L("42", resync.XSD + "integer")),
                         normalise(L("42", resync.XSD + "int")))

    def test_numeric_precision_loss_is_tolerated(self):
        # 0.8825780749320984 comes back from QLever as 0.8825780749321
        self.assertEqual(normalise(L("0.8825780749320984", resync.XSD + "decimal")),
                         normalise(L("0.8825780749321", resync.XSD + "double")))

    def test_genuinely_different_numbers_still_differ(self):
        self.assertNotEqual(normalise(L("1", resync.XSD + "decimal")),
                            normalise(L("1.5", resync.XSD + "decimal")))

    def test_unparseable_numeric_falls_back_to_lexical(self):
        weird = L("not-a-number", resync.XSD + "decimal")
        self.assertEqual(normalise(weird), weird)

    def test_datetimes_python_cannot_represent_still_normalise(self):
        # Wikibase emits dates far outside datetime's range, and month/day 00;
        # normalisation is regex-based precisely so these keep working.
        big = L("-13798000000-00-00T00:00:00Z", resync.XSD + "dateTime")
        self.assertEqual(normalise(big)[0], "T")
        self.assertEqual(normalise(big),
                         normalise(L("-13798000000-00-00T00:00:00Z",
                                     resync.XSD + "dateTime")))
        self.assertNotEqual(normalise(big),
                            normalise(L("-13798000001-00-00T00:00:00Z",
                                        resync.XSD + "dateTime")))

    def test_leading_zero_and_zulu_forms_agree(self):
        self.assertEqual(normalise(L("2026-08-18T00:00:00Z", resync.XSD + "dateTime")),
                         normalise(L("2026-08-18T00:00:00", resync.XSD + "dateTime")))

    def test_unparseable_datetime_falls_back_to_lexical(self):
        weird = L("not-a-date", resync.XSD + "dateTime")
        self.assertEqual(normalise(weird), weird)

    def test_xsd_string_and_plain_literal_are_the_same(self):
        self.assertEqual(normalise(L("x", resync.XSD + "string")), normalise(L("x")))

    def test_language_tags_are_significant(self):
        self.assertNotEqual(normalise(L("x", None, "en")), normalise(L("x", None, "de")))


class FakeStore:
    """Enough of Store for diff(): scope query first, then shared lookups."""

    def __init__(self, scope, shared=()):
        self.scope = list(scope)
        self.shared = list(shared)
        self.calls = 0

    def construct(self, query):
        self.calls += 1
        return self.scope if self.calls == 1 else self.shared


class DiffTest(unittest.TestCase):
    LABEL = U("http://www.w3.org/2000/01/rdf-schema#label")

    def test_identical_state_is_clean(self):
        target = [(ENTITY, self.LABEL, L("i", None, "en"))]
        drift = diff(FakeStore(target), "Q1", URIS, target, 30)
        self.assertTrue(drift.clean)
        self.assertEqual(build_operations(drift), [])

    def test_surplus_value_is_deleted_exactly(self):
        store = [(ENTITY, self.LABEL, L("typo", None, "en")),
                 (ENTITY, self.LABEL, L("i", None, "en"))]
        target = [(ENTITY, self.LABEL, L("i", None, "en"))]
        drift = diff(FakeStore(store), "Q1", URIS, target, 30)
        self.assertEqual(drift.delete_triples,
                         [(ENTITY, self.LABEL, L("typo", None, "en"))])
        self.assertEqual(drift.delete_patterns, [])
        self.assertEqual(drift.inserted, [])

    def test_sibling_values_are_not_churned(self):
        # Removing 'a wikibase:Statement' must leave 'a wikibase:BestRank' alone,
        # even though both share a (subject, predicate) group.
        store = [(STATEMENT, U(RDF_TYPE), U(WB + "Statement")),
                 (STATEMENT, U(RDF_TYPE), U(WB + "BestRank"))]
        target = [(STATEMENT, U(RDF_TYPE), U(WB + "BestRank"))]
        drift = diff(FakeStore(store), "Q1", URIS, target, 30)
        self.assertEqual(drift.delete_triples,
                         [(STATEMENT, U(RDF_TYPE), U(WB + "Statement"))])
        self.assertEqual(drift.inserted, [])

    def test_numeric_change_uses_a_pattern_delete(self):
        # The stored lexical form is not recoverable from QLever's output, so the
        # whole group has to be cleared by pattern instead of matched literally.
        store = [(ENTITY, U(WB + "statements"), L("6.0", resync.XSD + "double"))]
        target = [(ENTITY, U(WB + "statements"), L("7", resync.XSD + "integer"))]
        drift = diff(FakeStore(store), "Q1", URIS, target, 30)
        self.assertEqual(drift.delete_patterns, [(ENTITY, U(WB + "statements"))])
        self.assertEqual(drift.delete_triples, [])
        self.assertEqual(drift.inserted, target)

    def test_equal_numbers_in_different_datatypes_are_left_alone(self):
        store = [(ENTITY, U(WB + "statements"), L("7.0", resync.XSD + "double"))]
        target = [(ENTITY, U(WB + "statements"), L("7", resync.XSD + "integer"))]
        self.assertTrue(diff(FakeStore(store), "Q1", URIS, target, 30).clean)

    def test_missing_entity_upstream_removes_everything(self):
        store = [(ENTITY, self.LABEL, L("i", None, "en")),
                 (STATEMENT, U(WB + "rank"), U(WB + "NormalRank"))]
        drift = diff(FakeStore(store), "Q1", URIS, None, 30)
        self.assertTrue(drift.missing_upstream)
        self.assertEqual(len(drift.deleted), 2)
        self.assertEqual(drift.inserted, [])

    def test_shared_nodes_are_insert_only(self):
        target = [(ENTITY, self.LABEL, L("i", None, "en")),
                  (VALUE, U(WB + "quantityAmount"), L("1", resync.XSD + "decimal"))]
        # store has the entity but not the value node
        drift = diff(FakeStore([target[0]], shared=[]), "Q1", URIS, target, 30)
        self.assertEqual(drift.shared_missing, [target[1]])
        self.assertEqual(drift.deleted, [])

    def test_present_shared_nodes_are_not_reinserted(self):
        shared = (VALUE, U(WB + "quantityAmount"), L("1", resync.XSD + "decimal"))
        target = [(ENTITY, self.LABEL, L("i", None, "en")), shared]
        # QLever hands the value back folded; it must still count as present
        folded = (VALUE, U(WB + "quantityAmount"), L("1.0", resync.XSD + "double"))
        drift = diff(FakeStore([target[0]], shared=[folded]), "Q1", URIS, target, 30)
        self.assertEqual(drift.shared_missing, [])
        self.assertTrue(drift.clean)


class OperationsTest(unittest.TestCase):
    def test_deletes_are_ordered_before_inserts(self):
        drift = Drift("Q1")
        drift.delete_triples = [(ENTITY, U("http://schema.org/name"), L("x", None, "en"))]
        drift.delete_patterns = [(ENTITY, U("http://schema.org/version"))]
        drift.inserted = [(ENTITY, U("http://schema.org/version"),
                           L("42", resync.XSD + "integer"))]
        kinds = [op.split()[0] + " " + op.split()[1] for op in build_operations(drift)]
        self.assertEqual(kinds, ["DELETE DATA", "DELETE {", "INSERT DATA"])

    def test_large_updates_are_chunked(self):
        drift = Drift("Q1")
        drift.inserted = [(ENTITY, U(BASE + "/prop/direct/P%d" % i), L(str(i)))
                          for i in range(10)]
        self.assertEqual(len(build_operations(drift, chunk_size=3)), 4)


class SinceTest(unittest.TestCase):
    def test_durations(self):
        for value in ("30s", "15m", "24h", "7d", "2w", "24 h"):
            self.assertRegex(parse_since(value), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_absolute_forms(self):
        self.assertEqual(parse_since("2026-08-17"), "2026-08-17T00:00:00Z")
        self.assertEqual(parse_since("2026-08-17T12:00:00Z"), "2026-08-17T12:00:00Z")

    def test_ordering(self):
        self.assertLess(parse_since("7d"), parse_since("1h"))

    def test_rejects_nonsense(self):
        for value in ("bogus", "24", "h", "-1d", "yesterday"):
            with self.assertRaises(ValueError):
                parse_since(value)


class IdListTest(unittest.TestCase):
    def test_comments_and_blank_lines_are_ignored(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# ids to fix\nQ1\n\n  P31  # inline comment\nQ2 Q3\n")
            path = fh.name
        try:
            self.assertEqual(resync.read_ids(path), ["Q1", "P31", "Q2", "Q3"])
        finally:
            os.unlink(path)

    def test_id_pattern(self):
        for good in ("Q1", "P31", "Q1234567"):
            self.assertRegex(good, resync.ID_RE)
        for bad in ("Item:Q1", "q1", "Q", "Q1x", ""):
            self.assertNotRegex(bad, resync.ID_RE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
