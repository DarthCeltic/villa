"""Regression against confusable / transposed scroll identifiers.

Confusability is tested on synthetic ids; resolution is tested against whatever the registry
declares, so nothing here depends on which scrolls are registered.
"""
from __future__ import annotations

import itertools
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core import scroll_ids as SI
from argus.core.scroll_ids import (CANONICAL, assert_no_confusable_collision,
                                   confusable_pairs, is_confusable, normalize, resolve)

SELECTED = "PHerc9012"
TRANSPOSED = "PHerc9021"
FORBIDDEN_SET = ["PHerc9300", TRANSPOSED, "PHercFixtureA", "PHerc9455"]

_KEYS = {normalize(c) for c in CANONICAL} | {normalize(a) for a in SI.ALIASES}


def _numbered_canonical():
    """Registered ids of the plain PHerc<digits> shape, with at least two distinct digits."""
    return [c for c in CANONICAL
            if re.fullmatch(r"PHerc\d{3,4}", c) and len(set(c[5:])) > 1]


def _unregistered_transposition(c):
    """A digit permutation of a registered id that is itself NOT registered, or None."""
    digits = c[5:]
    for perm in itertools.permutations(digits):
        cand = c[:5] + "".join(perm)
        if cand != c and normalize(cand) not in _KEYS:
            return cand
    return None


class TestTransposedIds(unittest.TestCase):
    def test_transposed_ids_normalize_differently(self):
        self.assertNotEqual(normalize(SELECTED), normalize(TRANSPOSED))

    def test_they_are_flagged_confusable(self):
        self.assertTrue(is_confusable(SELECTED, TRANSPOSED))
        self.assertIn((SELECTED, TRANSPOSED), confusable_pairs([SELECTED, TRANSPOSED, "PHerc9300"]))

    def test_selecting_an_id_against_a_set_holding_its_transposition_raises(self):
        with self.assertRaises(ValueError) as cm:
            assert_no_confusable_collision([SELECTED], FORBIDDEN_SET,
                                           context="fixture held-out selection")
        msg = str(cm.exception)
        self.assertIn(SELECTED, msg)
        self.assertIn(TRANSPOSED, msg)
        self.assertIn("DIFFERENT scrolls", msg)

    def test_a_selection_with_no_confusable_neighbour_passes(self):
        assert_no_confusable_collision(["PHerc9876"], FORBIDDEN_SET)


class TestNormalization(unittest.TestCase):
    def test_case_and_separators_are_absorbed(self):
        for c in _numbered_canonical():
            for v in (c.lower(), c[:5] + "_" + c[5:], c[:5] + "-" + c[5:],
                      c[:5] + " " + c[5:], c.lower()[:5] + "." + c[5:]):
                self.assertEqual(resolve(v), c)

    def test_digit_order_is_never_absorbed(self):
        self.assertNotEqual(normalize(SELECTED), normalize(TRANSPOSED))
        self.assertNotEqual(normalize("PHerc9102"), normalize("PHerc9201"))


class TestAliasesAndPlaceholders(unittest.TestCase):
    def test_a_declared_alias_resolves_to_its_canonical_id(self):
        for alias, canon in SI.ALIASES.items():
            self.assertEqual(resolve(alias), canon)
            self.assertIn(canon, CANONICAL)

    def test_placeholders_never_resolve_to_a_scroll(self):
        for ph in SI.PLACEHOLDERS:
            with self.assertRaises(KeyError) as cm:
                resolve(ph)
            self.assertIn("PLACEHOLDER", str(cm.exception))

    def test_every_canonical_id_resolves_to_itself(self):
        for c in CANONICAL:
            self.assertEqual(resolve(c), c)


class TestResolveRefuses(unittest.TestCase):
    def test_an_unknown_id_is_refused_not_guessed(self):
        with self.assertRaises(KeyError):
            resolve("NotARealScroll")

    def test_the_refusal_names_the_transposed_neighbour_as_a_hint(self):
        for c in _numbered_canonical():
            t = _unregistered_transposition(c)
            if t is None:
                continue
            with self.assertRaises(KeyError) as cm:
                resolve(t)
            self.assertIn("same digits in a different order", str(cm.exception))
            return
        self.skipTest("no registered id has an unregistered transposition")

    def test_resolve_never_returns_a_nearest_match(self):
        """A nearest-match resolver is how a contaminated scroll gets substituted."""
        for c in _numbered_canonical():
            for bad in (c + "1", c[:-1]):
                if normalize(bad) in _KEYS:
                    continue
                with self.assertRaises(KeyError):
                    resolve(bad)


class TestConfusabilitySemantics(unittest.TestCase):
    def test_an_id_is_not_confusable_with_itself(self):
        self.assertFalse(is_confusable(SELECTED, SELECTED.lower()[:5] + "_" + SELECTED[5:]))

    def test_different_letters_are_not_confusable_even_with_equal_digits(self):
        self.assertFalse(is_confusable("PHerc9012", "PHercFixture9012"))

    def test_ids_without_digits_are_not_confusable(self):
        self.assertFalse(is_confusable("PHercFixtureA", "PHercFixtureB"))

    def test_every_registered_collision_is_between_distinct_ids(self):
        for a, b in confusable_pairs():
            self.assertNotEqual(resolve(a), resolve(b))
            self.assertTrue(is_confusable(a, b))

    def test_sabotage_a_planted_transposition_is_caught(self):
        for c in _numbered_canonical():
            t = _unregistered_transposition(c)
            if t is None:
                continue
            planted = list(CANONICAL) + [t]
            self.assertIn(tuple(sorted((c, t))), confusable_pairs(planted))
            return
        planted = [SELECTED, TRANSPOSED]
        self.assertIn((SELECTED, TRANSPOSED), confusable_pairs(planted))


if __name__ == "__main__":
    loader = unittest.TestLoader()
    res = unittest.TextTestRunner(verbosity=2).run(
        loader.loadTestsFromModule(__import__(__name__)))
    ok = res.testsRun - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, res.testsRun))
    raise SystemExit(0 if ok == res.testsRun else 1)
