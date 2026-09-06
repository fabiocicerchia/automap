#!/usr/bin/env python3
"""
Regression tests for automap.

Each test pins a behaviour that was wrong at some point during development.
The fixtures are small repositories under tests/fixtures, each built to
contain a specific defect so that a rule firing on it is a true positive.

Run with: python3 tests/test_automap.py
No pytest required — the point is that this works anywhere python does.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import automap as A  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def build(name):
    root = FIX / name
    return root, A.build(root, A.load_config(root))


class Resolution(unittest.TestCase):
    """Mapping an import specifier back to a file is where these tools fail."""

    @classmethod
    def setUpClass(cls):
        cls.root, cls.R = build("poly")
        cls.edges = {(e.src, e.dst) for e in cls.R["edges"]}

    def test_tsconfig_path_alias(self):
        self.assertIn(("web.app", "lib.db"), self.edges)

    def test_go_module_prefix(self):
        self.assertIn(("api.handlers", "store"), self.edges)

    def test_java_maven_source_root(self):
        self.assertIn(("com.acme.api.Controller", "com.acme.core.Ledger"), self.edges)

    def test_csharp_namespace_not_path(self):
        self.assertIn(("Acme.Api.Endpoint", "Acme.Core.Money"), self.edges)

    def test_rust_crate_prefix(self):
        self.assertIn(("engine", "store.disk"), self.edges)

    def test_php_psr4(self):
        self.assertIn(("Http.Router", "Domain.Invoice"), self.edges)

    def test_ruby_require_relative(self):
        self.assertIn(("billing.charge", "money"), self.edges)

    def test_cpp_include(self):
        self.assertIn(("src.main", "include.engine"), self.edges)

    def test_commented_out_import_is_ignored(self):
        self.assertNotIn("lib.should-not-be-seen", {d for _, d in self.edges})

    def test_import_keyword_inside_string_literal_is_ignored(self):
        """A file containing "...is not an import" once swallowed the real
        import on the following line."""
        self.assertIn(("web.app", "web.types"), self.edges)
        for e in self.R["edges"]:
            if e.src == "web.app" and e.dst == "web.types":
                self.assertEqual(e.line, 7)

    def test_go_struct_tag_is_not_an_import(self):
        self.assertNotIn("not", {d.split(".")[0] for _, d in self.edges})

    def test_export_from_does_not_span_statements(self):
        """`export` on line 1 must not reach a `from` three lines later."""
        hits = [e for e in self.R["edges"] if (e.src, e.dst) == ("lib.db", "web.app")]
        self.assertTrue(all(e.line > 1 for e in hits))


class PythonResolution(unittest.TestCase):
    def test_relative_import_in_package_init(self):
        """`from .x import y` inside a package __init__ resolves against the
        package itself, not its parent."""
        names = {"pkg", "pkg.sub"}
        self.assertEqual(A.component_of("pkg.sub", 1), "pkg")

    def test_stdlib_is_not_third_party(self):
        lang = A.BY_EXT[".py"]
        proj = A.Project(root=Path("."))
        self.assertTrue(A.third_party("sys", "auto", lang, proj))
        self.assertTrue(A.is_stdlib("sys", "Python"))
        self.assertFalse(A.is_stdlib("requests", "Python"))

    def test_go_stdlib_recognised(self):
        self.assertTrue(A.is_stdlib("fmt", "Go"))
        self.assertTrue(A.is_stdlib("net/http", "Go"))
        self.assertFalse(A.is_stdlib("github.com/gin-gonic/gin", "Go"))


class GraphAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.R = build("poly")

    def test_cycles_detected(self):
        flat = {frozenset(c) for c in cls_cycles(self.R)}
        self.assertIn(frozenset({"api", "internal", "store"}), flat)

    def test_layer_violations_detected(self):
        pairs = {(v["from"], v["to"]) for v in self.R["violations"]}
        self.assertIn(("internal", "api"), pairs)

    def test_no_unaccounted_imports(self):
        unknown = sum(v["unknown"] for v in self.R["stats"].values())
        self.assertEqual(unknown, 0, "every local import should resolve")

    def test_propagation_cost_in_range(self):
        _, cost, _, _ = A.reachability(self.R["comp_mods"], self.R["comp_edges"])
        self.assertTrue(0.0 <= cost <= 1.0)

    def test_dsm_marks_only_back_edges(self):
        table, feedback, shown = A.dsm(self.R["comp_mods"], self.R["comp_edges"],
                                       self.R["cycles"])
        self.assertGreater(feedback, 0)
        self.assertLess(feedback, len(self.R["comp_edges"]))


def cls_cycles(R):
    return R["cycles"]


class Determinism(unittest.TestCase):
    def test_two_runs_identical(self):
        root, R1 = build("poly")
        _, R2 = build("poly")
        self.assertEqual(A.baseline(R1), A.baseline(R2))

    def test_baseline_is_sorted(self):
        _, R = build("poly")
        b = A.baseline(R)
        self.assertEqual(b["edges"], sorted(b["edges"]))


class CodeRules(unittest.TestCase):
    """The `bad` fixture contains one instance of each defect on purpose."""

    @classmethod
    def setUpClass(cls):
        cls.root, cls.R = build("bad")
        hits, funcs, clones, stats = A.scan_code(cls.root, cls.R["cfg"], cls.R["modules"])
        cls.fired = {h.rule for h in hits}
        cls.findings = {f.rule for f in
                        A.code_findings(hits, funcs, clones, cls.R["cfg"], cls.R["modules"])}

    def test_security_rules_fire(self):
        for rule in ("SEC-EVAL", "SEC-SHELL", "SEC-DESERIALIZE", "SEC-SQLCONCAT",
                     "SEC-TLSOFF", "SEC-UNSAFEC", "SEC-SECRETLIKE", "SEC-WEAKCRYPTO"):
            self.assertIn(rule, self.fired, rule)

    def test_loop_scoped_rules_fire(self):
        for rule in ("PERF-NPLUSONE", "PERF-REGEXLOOP", "SCL-SLEEPPOLL"):
            self.assertIn(rule, self.fired, rule)

    def test_python_metrics_fire(self):
        self.assertIn("RDB-NESTING", self.findings)
        self.assertIn("MNT-PARAMS", self.findings)

    def test_nplusone_ignores_plain_dict_get(self):
        """`d.get(k)` in a loop is not a database query."""
        src = "for k in keys:\n    v = d.get(k)\n"
        lang = A.BY_EXT[".py"]
        spans = A.loop_spans(src, src, lang)
        rule = next(r for r in A.CODE_RULES if r.id == "PERF-NPLUSONE")
        import re
        hits = [m for m in re.finditer(rule.pattern, src, rule.flags)
                if A.in_any(m.start(), spans)] if hasattr(A, "in_any") else \
               [m for m in re.finditer(rule.pattern, src, rule.flags)]
        self.assertEqual(hits, [])

    def test_module_state_ignores_locals(self):
        rule = next(r for r in A.CODE_RULES if r.id == "SCL-INMEMSTATE")
        import re
        self.assertIsNone(re.search(rule.pattern, "    cache = {}\n", rule.flags))
        self.assertIsNotNone(re.search(rule.pattern, "cache = {}\n", rule.flags))


class Types(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.R = build("shop")
        cls.decls = A.extract_types(cls.root, cls.R["modules"])
        cls.known, cls.inherit, cls.compose = A.type_relations(cls.decls)

    def test_python_classes_exact(self):
        names = {t.name for t in self.decls}
        for n in ("Invoice", "CreditNote", "Money", "LineItem", "StripeGateway"):
            self.assertIn(n, names)

    def test_inheritance(self):
        self.assertIn(("CreditNote", "Invoice"), self.inherit)
        self.assertIn(("StripeGateway", "PaymentGateway"), self.inherit)

    def test_composition_from_annotations(self):
        pairs = {(a, b) for a, b, _ in self.compose}
        self.assertIn(("LineItem", "Money"), pairs)
        self.assertIn(("Money", "Currency"), pairs)

    def test_typescript_types(self):
        names = {t.name for t in self.decls if t.lang == "TypeScript"}
        self.assertIn("CartView", names)
        self.assertIn("Repository", names)

    def test_untyped_field_yields_no_edge(self):
        """`lines: list` says nothing about its contents, so no edge."""
        pairs = {(a, b) for a, b, _ in self.compose}
        self.assertNotIn(("Invoice", "LineItem"), pairs)


class Journeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.R = build("app")
        cls.entries, cls.nav = A.extract_entries(cls.root, cls.R["modules"])

    def test_http_routes_found(self):
        paths = {(e.verb, e.path) for e in self.entries if e.kind == "http"}
        self.assertIn(("GET", "/products"), paths)
        self.assertIn(("POST", "/checkout"), paths)

    def test_file_routing_found(self):
        pages = {e.path for e in self.entries if e.kind == "page"}
        self.assertEqual(pages, {"/products", "/cart", "/checkout", "/help"})

    def test_navigation_edges(self):
        targets = {t for _, t, _, _ in self.nav}
        self.assertIn("/checkout", targets)
        self.assertIn("/orders/1", targets)

    def test_route_template_matches_concrete_link(self):
        import re

        def route_rx(pattern):
            parts = re.split(r'(\{[^}]*\}|:\w+|<[^>]*>|\*)', pattern)
            return re.compile("^" + "".join(
                '[^/]+' if i % 2 else re.escape(s) for i, s in enumerate(parts)) + "/?$")

        for tpl in ("/orders/{id}", "/orders/:id", "/orders/<int:id>"):
            self.assertTrue(route_rx(tpl).match("/orders/1"), tpl)
        self.assertFalse(route_rx("/products").match("/orders/1"))

    def test_blast_radius(self):
        reach = A.module_reach(self.R["edges"], "api.routes")
        comps = {A.component_of(m, 1) for m in reach}
        self.assertIn("services", comps)
        self.assertIn("store", comps)


class SelfCheck(unittest.TestCase):
    def test_automap_maps_itself(self):
        R = A.build(ROOT, A.load_config(ROOT))
        self.assertGreaterEqual(len(R["modules"]), 1)
        self.assertEqual(R["cycles"], [], "automap should have no cycles")


if __name__ == "__main__":
    unittest.main(verbosity=2)
