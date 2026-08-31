#!/usr/bin/env python3
"""
automap 2.0 — derive architecture artefacts from a polyglot codebase.

No model, no network, no generation. Every statement in the output is computed
from the source tree or from git, and carries a file:line or a commit hash.
Where only a human knows the answer, the tool emits a blank and says so.

Languages
  Python              real parse (ast)
  Go, Java, Kotlin,   structural: unambiguous import syntax, resolved through
  C#, Rust, Scala     the project manifest (go.mod, Cargo.toml, source roots)
  TS, JS, Swift       structural: path resolution incl. tsconfig path aliases
  PHP, Ruby           heuristic: convention-based resolution
  C, C++              heuristic: #include path resolution only

Each edge carries the fidelity of the scanner that found it, and the report
states the resolution rate per language so you know how much of the tree the
map actually accounts for.

Commands
  map    ARCHITECTURE.md + mermaid + baseline JSON
  check  recompute, diff against committed baseline, exit 1 on new coupling
  adr    scaffold one ADR per decision point, facts filled, rationale blank
  langs  what is supported, and how well
"""

from __future__ import annotations
import argparse, ast, json, os, re, subprocess, sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "2.0"

# =====================================================================
# 1. LANGUAGE TABLE
#    Fidelity is stated, not implied. PARSED means a real grammar was used.
#    STRUCTURAL means unambiguous import syntax resolved through a project
#    manifest. HEURISTIC means convention matching that can be wrong.
# =====================================================================

PARSED, STRUCTURAL, HEURISTIC = "parsed", "structural", "heuristic"

C_STYLE = dict(line=["//"], block=[("/*", "*/")], strings=['"', "'"], raw=["`"])
HASH = dict(line=["#"], block=[], strings=['"', "'"], raw=[])


@dataclass
class Lang:
    name: str
    exts: tuple
    fidelity: str
    comments: dict
    imports: tuple = ()
    exports: tuple = ()
    resolver: str = "dotted"
    module_is_dir: bool = False
    source_roots: tuple = ()
    index_names: tuple = ()


LANGS = [
    Lang("Python", (".py",), PARSED, HASH, resolver="python", index_names=("__init__",)),

    Lang("TypeScript", (".ts", ".tsx", ".mts", ".cts"), STRUCTURAL, C_STYLE,
         imports=(
             (r'\bimport\s+(?:type\s+)?(?:\{[^}]*\}|\*(?:\s+as\s+\w+)?|[\w$]+)'
              r'(?:\s*,\s*(?:\{[^}]*\}|[\w$]+))?\s+from\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bimport\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bexport\s+(?:type\s+)?(?:\{[^}]*\}|\*(?:\s+as\s+\w+)?)'
              r'\s+from\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\brequire\(\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bimport\(\s*[\'"]([^\'"\n]*)[\'"]', 1),
         ),
         exports=((r'^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?'
                   r'(function|class|interface|type|enum|const)\s+(\w+)', 1, 2),),
         resolver="path", index_names=("index",)),

    Lang("JavaScript", (".js", ".jsx", ".mjs", ".cjs"), STRUCTURAL, C_STYLE,
         imports=(
             (r'\bimport\s+(?:type\s+)?(?:\{[^}]*\}|\*(?:\s+as\s+\w+)?|[\w$]+)'
              r'(?:\s*,\s*(?:\{[^}]*\}|[\w$]+))?\s+from\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bimport\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bexport\s+(?:type\s+)?(?:\{[^}]*\}|\*(?:\s+as\s+\w+)?)'
              r'\s+from\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\brequire\(\s*[\'"]([^\'"\n]*)[\'"]', 1),
             (r'\bimport\(\s*[\'"]([^\'"\n]*)[\'"]', 1),
         ),
         exports=((r'^\s*export\s+(?:default\s+)?(?:async\s+)?'
                   r'(function|class|const|let|var)\s+(\w+)', 1, 2),),
         resolver="path", index_names=("index",)),

    Lang("Go", (".go",), STRUCTURAL, C_STYLE,
         imports=((r'"([^"\n]+)"', 1),),
         exports=((r'^\s*(func|type|var|const)\s+(?:\([^)]*\)\s*)?([A-Z]\w*)', 1, 2),),
         resolver="go", module_is_dir=True),

    Lang("Java", (".java",), STRUCTURAL, C_STYLE,
         imports=((r'^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;', 1),),
         exports=((r'^\s*public\s+(?:final\s+|abstract\s+|sealed\s+|static\s+)*'
                   r'(class|interface|enum|record)\s+(\w+)', 1, 2),),
         resolver="dotted",
         source_roots=("src/main/java", "src/test/java", "app/src/main/java", "src")),

    Lang("Kotlin", (".kt", ".kts"), STRUCTURAL, C_STYLE,
         imports=((r'^\s*import\s+([\w.]+?)(?:\.\*)?\s*$', 1),),
         exports=((r'^\s*(?:public\s+)?(fun|class|object|interface)\s+(\w+)', 1, 2),),
         resolver="dotted",
         source_roots=("src/main/kotlin", "src/test/kotlin", "app/src/main/kotlin", "src")),

    Lang("C#", (".cs",), STRUCTURAL, C_STYLE,
         imports=((r'^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;', 1),),
         exports=((r'^\s*(?:public|internal)\s+(?:sealed\s+|abstract\s+|static\s+|partial\s+)*'
                   r'(class|interface|struct|enum|record)\s+(\w+)', 1, 2),),
         resolver="namespace", source_roots=("src", "Source")),

    Lang("Rust", (".rs",), STRUCTURAL, C_STYLE,
         imports=((r'^\s*(?:pub\s+)?use\s+([\w:]+)', 1),),
         exports=((r'^\s*pub\s+(?:async\s+)?(fn|struct|enum|trait|type|const|mod)\s+(\w+)', 1, 2),),
         resolver="rust", source_roots=("src",), index_names=("mod", "lib", "main")),

    Lang("Scala", (".scala",), STRUCTURAL, C_STYLE,
         imports=((r'^\s*import\s+([\w.]+)', 1),),
         exports=((r'^\s*(?:final\s+|sealed\s+)*(class|object|trait)\s+(\w+)', 1, 2),),
         resolver="dotted", source_roots=("src/main/scala", "src")),

    Lang("Swift", (".swift",), STRUCTURAL, C_STYLE,
         imports=((r'^\s*import\s+(\w+)', 1),),
         exports=((r'^\s*public\s+(?:final\s+)?(func|class|struct|enum|protocol)\s+(\w+)', 1, 2),),
         resolver="dotted", source_roots=("Sources", "src")),

    Lang("PHP", (".php",), HEURISTIC,
         dict(line=["//", "#"], block=[("/*", "*/")], strings=['"', "'"], raw=[]),
         imports=(
             (r'^\s*use\s+([\w\\]+)', 1),
             (r'\brequire(?:_once)?\s*\(?\s*[\'"]([^\'"]+)[\'"]', 1),
             (r'\binclude(?:_once)?\s*\(?\s*[\'"]([^\'"]+)[\'"]', 1),
         ),
         exports=((r'^\s*(?:final\s+|abstract\s+)?(class|interface|trait)\s+(\w+)', 1, 2),),
         resolver="php", source_roots=("src", "app", "lib")),

    Lang("Ruby", (".rb",), HEURISTIC, HASH,
         imports=(
             (r'^\s*require_relative\s+[\'"]([^\'"]+)[\'"]', 1),
             (r'^\s*require\s+[\'"]([^\'"]+)[\'"]', 1),
         ),
         exports=((r'^\s*(class|module)\s+([A-Z]\w*)', 1, 2),),
         resolver="path", source_roots=("lib", "app", "src")),

    Lang("C/C++", (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"), HEURISTIC, C_STYLE,
         imports=(),          # handled inline: the bracket kind matters
         exports=(),
         resolver="path", source_roots=("src", "include", "lib")),
]

BY_EXT = {e: l for l in LANGS for e in l.exts}

DEFAULTS = {
    "component_depth": 1,
    "layers": {},
    "exclude": ["node_modules", ".venv", "venv", "build", "dist", "target", "bin", "obj",
                "__pycache__", ".git", ".idea", "vendor", "third_party", "generated",
                "migrations", ".next", "coverage", "Pods", "DerivedData"],
    "roots": ["."],
    "include_tests": False,
    "test_dirs": ["test", "tests", "spec", "__tests__", "testdata"],
    "source_roots": [],
    "aliases": {},
    "languages": [],
    "thresholds": {},
    "suppress": [],
}

TEST_FILE = re.compile(r'(^|[._-])(test|tests|spec)\.|(^|/)(test|tests|spec|__tests__)/', re.I)


# =====================================================================
# 2. COMMENT MASKING
#    Regexes must never see a commented-out import, and must never mistake
#    a // inside a string for a comment. One state machine does both,
#    preserving offsets so line numbers stay exact.
# =====================================================================

def mask_comments(text: str, c: dict, blank_strings: bool = False):
    """Blank out comments, and optionally the interiors of string literals,
    replacing them with spaces so every offset and line number is preserved.

    Blanking interiors is what stops a keyword inside a string ("...is not an
    import") from matching, while the delimiters stay put so an import pattern
    still matches the quoted span. The specifier is then read back out of the
    original text at the captured offsets."""
    out = list(text)
    i, n = 0, len(text)
    line_toks, blocks = c.get("line", []), c.get("block", [])
    strs = set(c.get("strings", [])) | set(c.get("raw", []))
    raws = set(c.get("raw", []))

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        if ch in strs:
            j, esc, raw = i + 1, False, ch in raws
            while j < n:
                if not raw and text[j] == "\\" and not esc:
                    esc = True; j += 1; continue
                if text[j] == ch and not esc:
                    break
                if not raw and text[j] == "\n":
                    break
                esc = False; j += 1
            if blank_strings:
                blank(i + 1, j)
            i = j + 1
            continue
        hit = False
        for tok in line_toks:
            if text.startswith(tok, i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                blank(i, j); i = j; hit = True; break
        if hit:
            continue
        for a, b in blocks:
            if text.startswith(a, i):
                j = text.find(b, i + len(a))
                j = n if j < 0 else j + len(b)
                blank(i, j); i = j; hit = True; break
        if hit:
            continue
        i += 1
    return "".join(out)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


BAD_SPEC = re.compile(r'[\s;{}()\[\]<>=,]')


def valid_spec(s: str) -> bool:
    return bool(s) and len(s) < 200 and not BAD_SPEC.search(s)


# =====================================================================
# 3. PROJECT MANIFESTS — resolution is only as good as these
# =====================================================================

@dataclass
class Project:
    root: Path
    go_module: str = ""
    ts_aliases: dict = field(default_factory=dict)
    ts_base: str = ""
    php_psr4: dict = field(default_factory=dict)
    rust_crate: str = ""
    source_roots: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)


def strip_jsonc(t: str) -> str:
    return mask_comments(t, C_STYLE)


def read_project(root: Path, cfg: dict) -> Project:
    p = Project(root=root, source_roots=list(cfg.get("source_roots", [])),
                aliases=dict(cfg.get("aliases", {})))

    gomod = root / "go.mod"
    if gomod.exists():
        m = re.search(r'^\s*module\s+(\S+)', gomod.read_text(errors="replace"), re.M)
        if m:
            p.go_module = m.group(1)

    for name in ("tsconfig.json", "jsconfig.json"):
        f = root / name
        if f.exists():
            try:
                d = json.loads(strip_jsonc(f.read_text(errors="replace")))
                co = d.get("compilerOptions", {})
                p.ts_base = co.get("baseUrl", "") or ""
                p.ts_aliases = co.get("paths", {}) or {}
            except Exception:
                pass
            break

    comp = root / "composer.json"
    if comp.exists():
        try:
            d = json.loads(comp.read_text(errors="replace"))
            for section in ("autoload", "autoload-dev"):
                for ns, path in (d.get(section, {}).get("psr-4", {}) or {}).items():
                    p.php_psr4[ns] = path if isinstance(path, str) else path[0]
        except Exception:
            pass

    cargo = root / "Cargo.toml"
    if cargo.exists():
        m = re.search(r'^\s*name\s*=\s*"([^"]+)"', cargo.read_text(errors="replace"), re.M)
        if m:
            p.rust_crate = m.group(1).replace("-", "_")
    return p


# =====================================================================
# 4. SCAN
# =====================================================================

@dataclass
class Edge:
    src: str
    dst: str
    file: str
    line: int
    fidelity: str = STRUCTURAL


@dataclass
class Module:
    name: str
    path: str
    lang: str
    fidelity: str
    loc: int = 0
    public: list = field(default_factory=list)
    namespaces: list = field(default_factory=list)


def walk_sources(root: Path, cfg: dict):
    """Yields (path, lang, is_test). Test files are always scanned — they are
    excluded from the architecture graph, but which components they exercise is
    itself a measurement worth having."""
    ex, tdirs = set(cfg["exclude"]), set(cfg["test_dirs"])
    want = {l.name for l in LANGS if not cfg["languages"] or l.name in cfg["languages"]}
    for base in cfg["roots"]:
        start = (root / base).resolve()
        if not start.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in ex and not d.startswith("."))
            # relative to the scan root: a repository checked out under a
            # directory that happens to be called "tests" is not all tests
            rel_dir = os.path.relpath(dirpath, root)
            in_test_dir = any(part in tdirs for part in Path(rel_dir).parts)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                lang = BY_EXT.get(p.suffix)
                if not lang or lang.name not in want:
                    continue
                rel = str(p.relative_to(root))
                yield p, lang, bool(in_test_dir or TEST_FILE.search(rel))


def strip_source_root(rel: str, lang: Lang, proj: Project) -> str:
    roots = sorted(set(lang.source_roots) | set(proj.source_roots), key=len, reverse=True)
    for r in roots:
        r = r.rstrip("/") + "/"
        if rel.startswith(r):
            return rel[len(r):]
    return rel


def module_id(path: Path, root: Path, lang: Lang, proj: Project) -> str:
    rel = str(path.relative_to(root)).replace(os.sep, "/")
    if lang.module_is_dir:
        d = str(Path(rel).parent)
        return "." if d == "." else d.replace("/", ".")
    rel = strip_source_root(rel, lang, proj)
    parts = rel.split("/")
    stem = Path(parts[-1]).stem
    parts = parts[:-1] if stem in lang.index_names else parts[:-1] + [stem]
    return ".".join(parts) or "."


def extract_go_imports(masked: str, text: str):
    """Go quotes many things. Only look inside import declarations."""
    hits = []
    for m in re.finditer(r'^\s*import\s*\(([\s\S]*?)^\s*\)', masked, re.M):
        block, off = m.group(1), m.start(1)
        for q in re.finditer(r'"([^"\n]*)"', block):
            spec = text[off + q.start(1): off + q.end(1)]
            if valid_spec(spec):
                hits.append((spec, line_of(text, off + q.start())))
    for m in re.finditer(r'^\s*import\s+(?:\w+\s+|\.\s+|_\s+)?"([^"\n]*)"', masked, re.M):
        spec = text[m.start(1):m.end(1)]
        if valid_spec(spec):
            hits.append((spec, line_of(text, m.start())))
    return hits


def scan_file(path: Path, root: Path, lang: Lang, proj: Project):
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = str(path.relative_to(root)).replace(os.sep, "/")
    mid = module_id(path, root, lang, proj)
    mod = Module(mid, rel, lang.name, lang.fidelity, len(text.splitlines()))
    specs = []

    if lang.name == "Python":
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            return mod, [], f"{rel}: {e}"
        # inside a package's __init__, `from .x import y` means this package's x,
        # not the parent package's x
        if path.stem in lang.index_names:
            pkg = mid
        else:
            pkg = mid.rsplit(".", 1)[0] if "." in mid else ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    specs.append((a.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".") if pkg else []
                    up = node.level - 1
                    base = base[: len(base) - up] if up else base
                    tgt = ".".join([b for b in base if b] + ([node.module] if node.module else []))
                else:
                    tgt = node.module or ""
                if tgt:
                    # `from x import y` may import a submodule or a symbol.
                    # Emit the qualified form; longest-prefix resolution picks
                    # the submodule when one exists and falls back to x when
                    # y is just a name.
                    for al in node.names:
                        specs.append((f"{tgt}.{al.name}" if al.name != "*" else tgt,
                                      node.lineno))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    kind = "class" if isinstance(node, ast.ClassDef) else "def"
                    mod.public.append(f"{kind} {node.name}:{node.lineno}")
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        mod.public.append(f"const {t.id}:{node.lineno}")
        mod.public.sort()
        return mod, specs, None

    masked = mask_comments(text, lang.comments, blank_strings=True)

    def spec_at(m, g):                       # read the real specifier back
        return text[m.start(g):m.end(g)]

    if lang.name == "Go":
        specs = extract_go_imports(masked, text)
    elif lang.name == "C/C++":
        for m in re.finditer(r'^\s*#\s*include\s+([<"])([^>"\n]*)[>"]', masked, re.M):
            spec = spec_at(m, 2)
            if valid_spec(spec):
                specs.append((spec, line_of(text, m.start()),
                              "system" if m.group(1) == "<" else "local"))
    else:
        for pat, grp in lang.imports:
            for m in re.finditer(pat, masked, re.M):
                spec = spec_at(m, grp)
                if valid_spec(spec):
                    specs.append((spec, line_of(text, m.start())))

    for pat, kg, ng in lang.exports:
        for m in re.finditer(pat, masked, re.M):
            mod.public.append(f"{m.group(kg)} {m.group(ng)}:{line_of(text, m.start())}")
    mod.public = sorted(set(mod.public))

    if lang.name in ("C#", "PHP"):
        for m in re.finditer(r'^\s*namespace\s+([\w.\\]+)', masked, re.M):
            mod.namespaces.append(m.group(1).replace("\\", "."))

    return mod, specs, None


# =====================================================================
# 5. RESOLUTION — one strategy per language family
# =====================================================================

class Index:
    def __init__(self, modules, proj: Project):
        self.proj = proj
        self.mods = modules
        self.ids = set(modules)
        self.by_path = {}
        self.by_ns = defaultdict(list)
        for mid, m in modules.items():
            noext = re.sub(r'\.\w+$', '', m.path)
            self.by_path.setdefault(noext, mid)
            self.by_path.setdefault(m.path, mid)
            if Path(m.path).stem in ("index", "mod", "lib", "main", "__init__"):
                self.by_path.setdefault(str(Path(m.path).parent), mid)
            for ns in m.namespaces:
                self.by_ns[ns].append(mid)

    def dotted(self, spec: str):
        parts = [p for p in spec.replace("/", ".").replace("\\", ".").split(".") if p]
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.ids:
                return cand
        return None

    def path(self, spec: str, from_file: str):
        cands = []
        if spec.startswith("."):
            cands.append(os.path.normpath(
                str(Path(from_file).parent / spec)).replace(os.sep, "/"))
        else:
            for pat, targets in self.proj.ts_aliases.items():
                rx = "^" + re.escape(pat).replace(r"\*", "(.*)") + "$"
                m = re.match(rx, spec)
                if m:
                    tail = m.group(1) if m.groups() else ""
                    for t in (targets if isinstance(targets, list) else [targets]):
                        cands.append(os.path.normpath(os.path.join(
                            self.proj.ts_base or ".", t.replace("*", tail))).replace(os.sep, "/"))
            for pat, tgt in self.proj.aliases.items():
                if spec.startswith(pat):
                    cands.append(os.path.normpath(
                        tgt + spec[len(pat):]).replace(os.sep, "/"))
            if self.proj.ts_base:
                cands.append(os.path.normpath(
                    os.path.join(self.proj.ts_base, spec)).replace(os.sep, "/"))
            cands.append(spec.lstrip("/"))

        for c in cands:
            c = c.lstrip("./")
            for probe in (c, re.sub(r'\.\w+$', '', c), f"{c}/index", f"{c}/mod"):
                if probe in self.by_path:
                    return self.by_path[probe]
            cn = re.sub(r'\.\w+$', '', c)
            for m in self.mods.values():
                noext = re.sub(r'\.\w+$', '', m.path)
                if noext.endswith("/" + cn) or noext == cn:
                    return m.name
        return None

    def namespace(self, spec: str):
        hits = self.by_ns.get(spec)
        if hits:
            return sorted(hits)[0]
        best = None
        for ns, mids in self.by_ns.items():
            if spec.startswith(ns + ".") and (best is None or len(ns) > len(best[0])):
                best = (ns, sorted(mids)[0])
        return best[1] if best else None


def third_party(spec: str, kind: str, lang: Lang, proj: Project) -> bool:
    """True when failing to resolve is expected rather than a gap in the map."""
    if kind == "system":                       # #include <...>
        return True
    if lang.name == "Go":
        return True            # no relative imports: unresolved means stdlib or vendored
    if lang.resolver in ("path",):             # bare specifier = package manager
        return not (spec.startswith(".") or spec.startswith("/"))
    return not (spec.startswith(".") or "/" in spec)


def resolve(spec: str, from_file: str, lang: Lang, idx: Index, proj: Project):
    r = lang.resolver
    if r == "python":
        return idx.dotted(spec)
    if r == "path":
        return idx.path(spec, from_file)
    if r == "go":
        if proj.go_module and spec.startswith(proj.go_module):
            rel = spec[len(proj.go_module):].strip("/")
            return idx.dotted(rel.replace("/", ".")) if rel else idx.dotted(".")
        if "." in spec.split("/")[0]:
            return None                      # github.com/... : third party
        return idx.dotted(spec)
    if r == "rust":
        s = spec.replace("::", ".")
        for pre in ("crate.", (proj.rust_crate + ".") if proj.rust_crate else "\0"):
            if s.startswith(pre):
                s = s[len(pre):]
        if s.startswith("self."):
            s = s[5:]
        if s.startswith("super."):
            here = strip_source_root(from_file, lang, proj)
            parent = str(Path(re.sub(r'\.\w+$', '', here)).parent)
            base = [] if parent == "." else parent.split("/")
            s = ".".join(base[:-1] + [s[6:]]) if base else s[6:]
        return idx.dotted(s)
    if r == "namespace":
        return idx.namespace(spec) or idx.dotted(spec)
    if r == "php":
        if spec.startswith(".") or "/" in spec:
            return idx.path(spec, from_file)
        s = spec.replace("\\", ".")
        for ns, p in proj.php_psr4.items():
            n = ns.replace("\\", ".").rstrip(".")
            if s.startswith(n + "."):
                return idx.path(p.rstrip("/") + "/" + s[len(n) + 1:].replace(".", "/"), from_file)
        return idx.namespace(s) or idx.dotted(s)
    return idx.dotted(spec)


# =====================================================================
# 6. ANALYSIS
# =====================================================================

def component_of(mod: str, depth: int) -> str:
    if mod == ".":
        return "(root)"
    return ".".join(mod.split(".")[:depth]) or mod


def tarjan(nodes, adj):
    idx, low, stack, on, out, counter = {}, {}, [], set(), [], [0]

    def strong(v):
        idx[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); on.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in idx:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    sys.setrecursionlimit(20000)
    for v in sorted(nodes):
        if v not in idx:
            strong(v)
    return sorted(out)


def metrics(comp_mods, comp_edges):
    ca, ce = defaultdict(set), defaultdict(set)
    for (a, b) in comp_edges:
        ce[a].add(b); ca[b].add(a)
    rows = []
    for c in sorted(comp_mods):
        o, i = len(ce[c]), len(ca[c])
        rows.append({"component": c, "modules": len(comp_mods[c]),
                     "loc": sum(m.loc for m in comp_mods[c]),
                     "langs": sorted({m.lang for m in comp_mods[c]}),
                     "fan_in": i, "fan_out": o,
                     "instability": round(o / (o + i), 2) if (o + i) else 0.0})
    return rows


def layer_violations(cfg, comp_edges):
    where = {}
    for rank, (layer, comps) in enumerate(cfg.get("layers", {}).items()):
        for c in comps:
            where[c] = (rank, layer)
    bad = []
    for (a, b), es in comp_edges.items():
        if a in where and b in where and where[b][0] < where[a][0]:
            bad.append({"from": a, "to": b, "from_layer": where[a][1], "to_layer": where[b][1],
                        "sites": [f"{e.file}:{e.line}" for e in es[:5]]})
    return sorted(bad, key=lambda x: (x["from"], x["to"]))


def build(root: Path, cfg: dict):
    proj = read_project(root, cfg)
    modules, raw, errors = {}, [], []
    test_mods, test_raw = {}, []
    for path, lang, is_test in walk_sources(root, cfg):
        m, specs, err = scan_file(path, root, lang, proj)
        if err:
            errors.append(err)
        if is_test and not cfg["include_tests"]:
            test_mods[m.name] = m
            test_raw.append((m, lang, specs))
            continue
        modules[m.name] = m
        raw.append((m, lang, specs))

    idx = Index(modules, proj)
    edges, external, unresolved = [], defaultdict(list), defaultdict(int)
    stdlib_deps = defaultdict(list)
    deep_relative = []
    stats = defaultdict(lambda: {"files": 0, "specs": 0, "internal": 0,
                                 "external": 0, "unknown": 0})

    for m, lang, specs in raw:
        st = stats[lang.name]
        st["files"] += 1
        for entry in specs:
            spec, line = entry[0], entry[1]
            kind = entry[2] if len(entry) > 2 else "auto"
            st["specs"] += 1
            if spec.count("../") >= 2 or spec.count("..\\") >= 2:
                deep_relative.append((m.path, line, spec))
            hit = resolve(spec, m.path, lang, idx, proj)
            if hit:
                st["internal"] += 1
                if hit != m.name:
                    edges.append(Edge(m.name, hit, m.path, line, lang.fidelity))
                continue
            head = re.split(r'[./\\:]', spec)[0] or spec
            if is_stdlib(spec, lang.name):
                stdlib_deps[head].append(Edge(m.name, spec, m.path, line, lang.fidelity))
            else:
                external[head].append(Edge(m.name, spec, m.path, line, lang.fidelity))
            if third_party(spec, kind, lang, proj):
                st["external"] += 1
            else:
                st["unknown"] += 1
                unresolved[lang.name] += 1

    seen_e = set()
    deduped = []
    for e in edges:                    # one statement can yield several specs
        k = (e.src, e.dst, e.file, e.line)
        if k not in seen_e:
            seen_e.add(k)
            deduped.append(e)
    edges = sorted(deduped, key=lambda e: (e.src, e.dst, e.file, e.line))
    depth = cfg["component_depth"]
    cm = defaultdict(list)
    for m in modules.values():
        cm[component_of(m.name, depth)].append(m)
    ce = defaultdict(list)
    for e in edges:
        a, b = component_of(e.src, depth), component_of(e.dst, depth)
        if a != b:
            ce[(a, b)].append(e)
    comp_mods, comp_edges = dict(sorted(cm.items())), dict(sorted(ce.items()))

    # which components the test suite actually reaches
    depth_ = cfg["component_depth"]
    tested = set()
    for tm, tlang, tspecs in test_raw:
        for entry in tspecs:
            hit = resolve(entry[0], tm.path, tlang, idx, proj)
            if hit:
                tested.add(component_of(hit, depth_))

    adj = defaultdict(set)
    for (a, b) in comp_edges:
        adj[a].add(b)
    return dict(proj=proj, modules=modules, edges=edges, external=external,
                test_modules=test_mods, tested=tested, deep_relative=deep_relative,
                stdlib=stdlib_deps,
                comp_mods=comp_mods, comp_edges=comp_edges,
                cycles=tarjan(set(comp_mods), adj),
                metrics=metrics(comp_mods, comp_edges),
                violations=layer_violations(cfg, comp_edges),
                errors=errors, stats=dict(stats), unresolved=dict(unresolved), cfg=cfg)


# =====================================================================
# 7. GIT
# =====================================================================

def git(root: Path, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=120)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def churn(root: Path, since="12.months"):
    per = defaultdict(int)
    for line in git(root, "log", f"--since={since}", "--numstat", "--format=%H").splitlines():
        p = line.split("\t")
        if len(p) == 3 and p[0].isdigit():
            per[p[2]] += int(p[0]) + int(p[1])
    return per


def edge_origin(root: Path, e: Edge):
    needle = re.split(r'[./\\:]', e.dst)[-1]
    txt = git(root, "log", "-1", "--format=%h\t%ad\t%an\t%s", "--date=short",
              "-S", needle, "--", e.file)
    if not txt.strip():
        return None
    f = (txt.strip().split("\t") + ["", "", "", ""])[:4]
    return {"commit": f[0], "date": f[1], "author": f[2], "subject": f[3]}


# =====================================================================
# 8. RENDER
# =====================================================================

def sanitize(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", s) or "n"


MAX_NODES = 40
MAX_CYCLE_MEMBERS = 10
MAX_CYCLE_EDGES = 12
MAX_SURFACE = 40


def graph_subset(comp_mods, comp_edges, cycles, violations, limit):
    """Which components to draw. A graph nobody can read is not a map, so keep
    the ones that carry the findings and the heaviest coupling, and say so."""
    if len(comp_mods) <= limit:
        return set(comp_mods), False
    keep = {c for cy in cycles for c in cy[:MAX_CYCLE_MEMBERS]}
    for v in violations:
        keep.add(v["from"]); keep.add(v["to"])
    weight = defaultdict(int)
    for (a, b), es in comp_edges.items():
        weight[a] += len(es); weight[b] += len(es)
    for c in sorted(comp_mods, key=lambda x: (-weight[x], x)):
        if len(keep) >= limit:
            break
        keep.add(c)
    return keep, True


def mermaid(cfg, comp_mods, comp_edges, cycles, keep=None, violations=()):
    incyc = {c for cy in cycles for c in cy}
    keep = keep if keep is not None else set(comp_mods)
    comp_mods = {k: v for k, v in comp_mods.items() if k in keep}
    comp_edges = {(a, b): e for (a, b), e in comp_edges.items() if a in keep and b in keep}
    out, placed = ["```mermaid", "graph LR"], set()

    def node(c):
        n, loc = len(comp_mods[c]), sum(m.loc for m in comp_mods[c])
        langs = sorted({m.lang for m in comp_mods[c]})
        tag = "/".join(langs[:2]) + ("+" if len(langs) > 2 else "")
        return f'{sanitize(c)}["{c}<br/><small>{tag} · {n} mod · {loc} loc</small>"]'

    for layer, comps in cfg.get("layers", {}).items():
        present = [c for c in sorted(comps) if c in comp_mods]
        if not present:
            continue
        out.append(f'  subgraph {sanitize(layer)}["{layer}"]')
        for c in present:
            out.append("    " + node(c)); placed.add(c)
        out.append("  end")
    for c in sorted(comp_mods):
        if c not in placed:
            out.append("  " + node(c))
    for (a, b), es in sorted(comp_edges.items()):
        arrow = "-.->" if all(e.fidelity == HEURISTIC for e in es) else "-->"
        out.append(f"  {sanitize(a)} {arrow}|{len(es)}| {sanitize(b)}")
    for c in sorted(incyc):
        out.append(f"  style {sanitize(c)} stroke-width:3px")
    if violations:
        bad = {(v["from"], v["to"]) for v in violations}
        for i, (a, b) in enumerate(sorted(comp_edges)):
            if (a, b) in bad:
                out.append(f"  linkStyle {i} stroke-width:2px")
    out.append("```")
    return "\n".join(out)



# ---------------------------------------------------------------------
# Additional derived diagrams. A node-link graph turns to soup past about
# thirty nodes; a matrix does not, so that is the default at scale.
# ---------------------------------------------------------------------

def condensed_order(comp_mods, comp_edges, cycles):
    """Reverse-topological order of the cycle-condensed graph, ties broken by
    name so the ordering is stable across runs. Leaves come first, so an honest
    dependency always points to an earlier column and lands below the diagonal;
    anything above the diagonal is pointing backwards."""
    scc_of = {}
    for i, cy in enumerate(cycles):
        for c in cy:
            scc_of[c] = f"scc{i}"
    for c in comp_mods:
        scc_of.setdefault(c, c)

    nodes = sorted(set(scc_of.values()))
    out_edges = defaultdict(set)
    indeg = {n: 0 for n in nodes}
    for (a, b) in comp_edges:
        x, y = scc_of[a], scc_of[b]
        if x != y and y not in out_edges[x]:
            out_edges[x].add(y)
            indeg[y] += 1

    ready = sorted(n for n in nodes if indeg[n] == 0)
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(out_edges[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
                ready.sort()
    order += sorted(n for n in nodes if n not in order)

    members = defaultdict(list)
    for c in sorted(comp_mods):
        members[scc_of[c]].append(c)
    flat = []
    for grp in order:
        flat.extend(members[grp])
    flat.reverse()          # leaves first, so a dependency always sits earlier
    return flat, scc_of


def dsm(comp_mods, comp_edges, cycles, limit=30):
    """Dependency structure matrix. Row depends on column; the number is how
    many import sites hold that dependency. Anything above the diagonal is a
    dependency pointing backwards, which is where cycles live."""
    order, scc_of = condensed_order(comp_mods, comp_edges, cycles)
    if len(order) > limit:
        keep, _ = graph_subset(comp_mods, comp_edges, cycles, [], limit)
        order = [c for c in order if c in keep]
    pos = {c: i for i, c in enumerate(order)}
    idx = {c: i + 1 for i, c in enumerate(order)}

    head = "| # | component | " + " | ".join(str(idx[c]) for c in order) + " |"
    rule = "|---|---|" + "---|" * len(order)
    rows, feedback = [head, rule], 0
    for r in order:
        cells = []
        for c in order:
            n = len(comp_edges.get((r, c), []))
            if not n:
                cells.append("·" if r != c else "—")
            elif pos[c] > pos[r]:
                cells.append(f"**{n}**")      # above the diagonal: feedback
                feedback += 1
            else:
                cells.append(str(n))
        rows.append(f"| {idx[r]} | `{r}` | " + " | ".join(cells) + " |")
    return "\n".join(rows), feedback, len(order)


def entry_points(modules, edges, comp_edges):
    """Modules nothing else imports, plus conventional entry filenames. These
    are the roots of whatever the reader is trying to hold in their head."""
    imported = {e.dst for e in edges}
    named = re.compile(r'(^|[./])(main|index|app|cli|server|program|__main__)$', re.I)
    roots = [m for n, m in sorted(modules.items()) if n not in imported]
    return sorted(roots, key=lambda m: (not bool(named.search(m.name)), -m.loc, m.name))


def import_tree(root_mod, modules, edges, depth=3, max_children=8):
    """Depth-limited import tree from one module. Repeats are marked rather
    than expanded twice, so the shape stays readable."""
    out_by = defaultdict(list)
    for e in edges:
        if e.dst not in out_by[e.src]:
            out_by[e.src].append(e.dst)
    lines, seen = [], set()

    def walk(name, prefix, d, last):
        conn = "└─ " if last else "├─ "
        mark = ""
        kids = sorted(set(out_by.get(name, [])))
        if name in seen and kids:
            mark = "  ↑ shown above"
            kids = []
        seen.add(name)
        lang = modules[name].lang if name in modules else "?"
        lines.append(f"{prefix}{conn}{name}  ({lang}){mark}")
        if d >= depth or not kids:
            return
        shown = kids[:max_children]
        pre = prefix + ("   " if last else "│  ")
        for i, k in enumerate(shown):
            walk(k, pre, d + 1, i == len(shown) - 1 and len(shown) == len(kids))
        if len(kids) > len(shown):
            lines.append(f"{pre}└─ … {len(kids) - len(shown)} more")

    lines.append(f"{root_mod}  ({modules[root_mod].lang})")
    kids = sorted(set(out_by.get(root_mod, [])))
    seen.add(root_mod)
    for i, k in enumerate(kids[:max_children]):
        walk(k, "", 1, i == min(len(kids), max_children) - 1)
    if len(kids) > max_children:
        lines.append(f"└─ … {len(kids) - max_children} more")
    return "\n".join(lines)



# =====================================================================
# 11. MEASUREMENTS THE RULES NEED
#     All derived from the tree or from git. Nothing inferred.
# =====================================================================

# The standard library is not a vendor dependency. Wrapping `sys` is not advice.
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "console", "constants", "crypto",
    "dgram", "dns", "domain", "events", "fs", "http", "http2", "https", "module",
    "net", "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "timers", "tls", "tty", "url",
    "util", "v8", "vm", "worker_threads", "zlib",
}
RUBY_STDLIB = {
    "json", "yaml", "set", "date", "time", "logger", "fileutils", "pathname", "uri",
    "net", "openssl", "digest", "csv", "erb", "ostruct", "securerandom", "socket",
    "stringio", "tempfile", "thread", "timeout", "benchmark", "optparse", "abbrev",
}


def is_stdlib(pkg: str, lang_name: str) -> bool:
    head = re.split(r'[./\\:]', pkg)[0]
    if lang_name == "Python":
        try:
            return head in sys.stdlib_module_names or head.startswith("_")
        except AttributeError:
            return False
    if lang_name == "Go":
        # split on "/" only: the domain test needs the whole first segment,
        # and splitting on "." first turns github.com into "github"
        return "." not in pkg.split("/")[0]
    if lang_name in ("JavaScript", "TypeScript"):
        return pkg.startswith("node:") or head in NODE_BUILTINS
    if lang_name in ("Java", "Kotlin", "Scala"):
        return head in ("java", "javax", "jdk", "kotlin", "scala")
    if lang_name == "C#":
        return head in ("System", "Microsoft")
    if lang_name == "Rust":
        return head in ("std", "core", "alloc")
    if lang_name == "Ruby":
        return head in RUBY_STDLIB
    if lang_name == "Swift":
        return head in ("Foundation", "UIKit", "SwiftUI", "Combine", "Dispatch")
    return False


def reachability(comp_mods, comp_edges):
    """Transitive closure over components, as bitmasks. Yields propagation cost:
    the share of ordered component pairs where one can reach the other."""
    names = sorted(comp_mods)
    bit = {c: i for i, c in enumerate(names)}
    adj = defaultdict(int)
    for (a, b) in comp_edges:
        adj[a] |= 1 << bit[b]
    reach = {c: adj[c] for c in names}
    for _ in range(len(names)):
        changed = False
        for c in names:
            m = reach[c]
            acc, mm, i = m, m, 0
            while mm:
                if mm & 1:
                    acc |= reach[names[i]]
                mm >>= 1
                i += 1
            if acc != m:
                reach[c] = acc
                changed = True
        if not changed:
            break
    n = len(names)
    total = n * (n - 1)
    hits = sum(bin(reach[c] & ~(1 << bit[c])).count("1") for c in names)
    return reach, (hits / total if total else 0.0), names, bit


def longest_chain(comp_mods, comp_edges, cycles):
    """Depth of the cycle-condensed dependency graph."""
    order, scc_of = condensed_order(comp_mods, comp_edges, cycles)
    depth, adj = defaultdict(int), defaultdict(set)
    for (a, b) in comp_edges:
        if scc_of[a] != scc_of[b]:
            adj[scc_of[a]].add(scc_of[b])
    for grp in [scc_of[c] for c in order]:
        for nxt in adj[grp]:
            depth[grp] = max(depth[grp], depth[nxt] + 1)
    return (max(depth.values()) + 1) if depth else 1


def cochange(root: Path, comp_of, max_commits=800, max_files=30):
    """Files changed in the same commit. This finds coupling the import graph
    cannot see: two components that always move together are coupled through
    something — a shared format, a protocol, an implicit contract — whether or
    not either imports the other."""
    txt = git(root, "log", f"-{max_commits}", "--name-only", "--format=@%H")
    pairs, commits, wide = defaultdict(int), 0, 0
    cur = []

    def flush(files):
        nonlocal commits, wide
        comps = sorted({c for f in files if (c := comp_of(f))})
        if not comps:
            return
        commits += 1
        if len(comps) >= 4:
            wide += 1
        if len(files) > max_files:
            return                      # sweeping commits say nothing specific
        for i, a in enumerate(comps):
            for b in comps[i + 1:]:
                pairs[(a, b)] += 1

    for line in txt.splitlines():
        if line.startswith("@"):
            flush(cur); cur = []
        elif line.strip():
            cur.append(line.strip())
    flush(cur)
    return pairs, commits, wide


def authorship(root: Path, max_commits=2000):
    """Who has touched each file. Bus factor is a property of the code as much
    as of the team: a component only one person has ever edited is one that
    only one person can safely change."""
    txt = git(root, "log", f"-{max_commits}", "--name-only", "--format=@%an")
    per, cur = defaultdict(set), None
    for line in txt.splitlines():
        if line.startswith("@"):
            cur = line[1:].strip()
        elif line.strip() and cur:
            per[line.strip()].add(cur)
    return per


def articulation_points(comp_mods, comp_edges):
    """Components whose removal would disconnect the dependency graph. These
    are the load-bearing walls: everything routes through them."""
    adj = defaultdict(set)
    for (a, b) in comp_edges:
        adj[a].add(b); adj[b].add(a)
    disc, low, parent, ap = {}, {}, {}, set()
    timer = [0]

    def dfs(u):
        children = 0
        disc[u] = low[u] = timer[0]; timer[0] += 1
        for v in sorted(adj[u]):
            if v not in disc:
                children += 1
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if u in parent and low[v] >= disc[u]:
                    ap.add(u)
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])
        if u not in parent and children > 1:
            ap.add(u)

    sys.setrecursionlimit(20000)
    for n in sorted(comp_mods):
        if n not in disc:
            dfs(n)
    return sorted(ap)


ABSTRACT_KINDS = {"interface", "trait", "protocol", "type", "abstract class", "object"}
CONCRETE_KINDS = {"class", "struct", "record", "enum", "func", "fun", "function", "def", "const"}


ABSTRACTION_LANGS = {"TypeScript", "Java", "C#", "Kotlin", "Rust", "Scala", "Swift", "PHP"}


def abstractness(comp_mods):
    """Martin's A: the share of a component's public surface that is abstract.
    Only computed where the language marks it, which is why some components are
    absent rather than scored zero."""
    out = {}
    for c, mods in comp_mods.items():
        a = k = 0
        for m in mods:
            if m.lang not in ABSTRACTION_LANGS:
                continue      # no reliable abstract/concrete marker: score nothing
            for p in m.public:
                kind = p.split(" ")[0]
                if kind in ABSTRACT_KINDS:
                    a += 1
                elif kind in CONCRETE_KINDS:
                    k += 1
        if a + k >= 5:
            out[c] = a / (a + k)
    return out


def module_cycles(modules, edges, depth):
    """Cycles between modules inside a single component. Invisible at component
    level, but they are what makes a component hard to read from the top."""
    by_comp = defaultdict(list)
    for e in edges:
        a, b = component_of(e.src, depth), component_of(e.dst, depth)
        if a == b:
            by_comp[a].append(e)
    found = []
    for c, es in sorted(by_comp.items()):
        nodes = {e.src for e in es} | {e.dst for e in es}
        adj = defaultdict(set)
        for e in es:
            adj[e.src].add(e.dst)
        for cy in tarjan(nodes, adj):
            found.append((c, cy))
    return found


VAGUE_NAMES = re.compile(
    r'^(utils?|util|common|shared|core|base|helpers?|misc|lib|libs|tools?|'
    r'general|global|stuff|extras?|support|infra|framework|managers?|'
    r'handlers?|services?|models?|data|internals?)$', re.I)

# Packages that solve the same problem. Two from one row usually means two
# conventions in one codebase, which readers pay for at every call site.
OVERLAP_GROUPS = {
    "HTTP client (JS)": {"axios", "request", "node-fetch", "got", "superagent", "ky"},
    "date handling (JS)": {"moment", "dayjs", "date-fns", "luxon"},
    "test runner (JS)": {"jest", "mocha", "jasmine", "ava", "vitest", "tape"},
    "state (JS)": {"redux", "mobx", "zustand", "recoil", "jotai"},
    "HTTP client (Python)": {"requests", "httpx", "urllib3", "aiohttp", "treq"},
    "ORM (Python)": {"sqlalchemy", "peewee", "tortoise", "pony"},
    "settings (Python)": {"configparser", "dynaconf", "environs", "pydantic_settings"},
    "test runner (Python)": {"pytest", "nose", "nose2", "unittest2"},
    "HTTP router (Go)": {"gin", "echo", "chi", "mux", "fiber", "gorilla"},
    "logging (Go)": {"logrus", "zap", "zerolog", "glog"},
    "JSON (Java)": {"jackson", "gson", "json-simple", "org.json"},
    "logging (Java)": {"log4j", "slf4j", "logback", "commons-logging"},
    "async runtime (Rust)": {"tokio", "async_std", "smol"},
    "serialization (Rust)": {"serde_json", "simd_json", "json"},
}


# =====================================================================
# 12. RULE CATALOG
#
#   A rule fires when a predicate over the measurements is true. The prose
#   attached to it is fixed text, written once, the way a linter documents a
#   rule: it explains the pattern, never your code. Nothing is generated per
#   run; only the numbers and the evidence change.
#
#   Thresholds are in DEFAULTS["thresholds"] and can be overridden in
#   .automap.json. Rules can be turned off with "suppress": ["ARCH-ORPHAN"].
#
#   What no rule attempts is why your team built it this way. That stays
#   blank, in the ADR scaffolds, on purpose.
# =====================================================================

@dataclass
class Finding:
    rule: str
    category: str
    severity: str          # high | medium | low | info
    headline: str
    why: str
    cause: str
    action: str
    evidence: list = field(default_factory=list)


SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

CATEGORIES = ["Evidence quality", "Structure", "Boundaries", "Change over time",
              "Size and shape", "Dependencies", "Testing"]
CODE_CATEGORIES = ["Security", "Performance", "Scalability",
                   "Algorithms and data structures", "Maintainability", "Readability"]

THRESHOLDS = {
    "propagation_medium": 0.20, "propagation_high": 0.35,
    "hub_fan_in": 4, "hub_instability": 0.5,
    "sdp_gap": 0.25, "distance_main_sequence": 0.6,
    "chain_depth": 6, "weld_sites": 8,
    "god_file_multiple": 4.0, "god_file_min_loc": 500,
    "god_component_share": 0.30, "wide_api": 80,
    "orphan_min_loc": 30, "orphan_count": 3,
    "entry_sprawl": 0.60, "dup_name_components": 4,
    "cochange_min": 4, "shotgun_share": 0.25, "min_commits": 20,
    "dep_fanout": 15, "vendor_sites": 20, "vendor_components": 3,
    "coverage_high": 0.05, "heuristic_share": 0.30,
    "fanin_one_count": 3, "untested_share": 0.4,
    "deep_relative": 3, "single_author_share": 0.8,
    "max_complexity": 12, "max_func_lines": 80, "max_nesting": 4,
    "max_params": 6, "clone_groups": 3,
}



RULE_INDEX = [
    ("ARCH-COVERAGE", "Evidence quality", "imports that resolve to nothing on disk"),
    ("ARCH-FIDELITY", "Evidence quality", "share of edges found by heuristic scanners"),
    ("ARCH-NOLAYERS", "Evidence quality", "no layering declared, so those checks are off"),
    ("ARCH-NOVCS", "Evidence quality", "no git history, so change-over-time rules are off"),
    ("ARCH-CYCLE", "Structure", "components that import each other, directly or transitively"),
    ("ARCH-FILECYCLE", "Structure", "module cycles inside a single component"),
    ("ARCH-PROPAGATION", "Structure", "share of the system an average change can reach"),
    ("ARCH-KEYSTONE", "Structure", "components whose removal would split the graph"),
    ("ARCH-SDP", "Structure", "dependencies pointing toward less stable components"),
    ("ARCH-MAINSEQ", "Structure", "rigid concrete hubs, and abstractions nothing uses"),
    ("ARCH-DEPTH", "Structure", "long dependency chains, i.e. onboarding depth"),
    ("ARCH-WELD", "Structure", "one coupling held by many separate import sites"),
    ("ARCH-SOLOUSE", "Structure", "boundaries with a single consumer and no dependencies"),
    ("ARCH-LAYER", "Boundaries", "dependencies pointing up through declared layers"),
    ("ARCH-SKIP", "Boundaries", "dependencies jumping over an intermediate layer"),
    ("ARCH-SEAM", "Boundaries", "imports crossing a language boundary"),
    ("ARCH-REACHUP", "Boundaries", "imports climbing two or more directories"),
    ("ARCH-VAGUE", "Boundaries", "components named utils, common, core, shared"),
    ("ARCH-DUPNAME", "Boundaries", "the same filename repeated across many components"),
    ("ARCH-CHURN", "Change over time", "heavily depended on and heavily edited at once"),
    ("ARCH-COCHANGE", "Change over time", "components changed together with no import between them"),
    ("ARCH-SHOTGUN", "Change over time", "commits that routinely span many components"),
    ("ARCH-BUSFACTOR", "Change over time", "depended-on components with a single author"),
    ("ARCH-GODFILE", "Size and shape", "modules far above the median size"),
    ("ARCH-GODCOMPONENT", "Size and shape", "one component holding most of the code"),
    ("ARCH-WIDEAPI", "Size and shape", "very large public surface for its number of dependents"),
    ("ARCH-ORPHAN", "Size and shape", "sizeable modules nothing in the tree imports"),
    ("ARCH-ENTRYSPRAWL", "Size and shape", "an entry point that reaches most of the system"),
    ("ARCH-DEPFANOUT", "Dependencies", "one component importing many third-party packages"),
    ("ARCH-VENDORSPREAD", "Dependencies", "a third-party API used directly across many components"),
    ("ARCH-DUPLIB", "Dependencies", "two libraries doing the same job"),
    ("ARCH-UNTESTED", "Testing", "depended-on components no test file imports"),
    ("ARCH-NOTESTS", "Testing", "no test files found at all"),
]


def F_(rule, cat, sev, headline, why, cause, action, evidence=()):
    return Finding(rule, cat, sev, headline, why, cause, action, list(evidence))


def evaluate(R, churn_map, root=None):
    """Every rule in one place. Reading this function tells you exactly what the
    tool can and cannot notice."""
    T = dict(THRESHOLDS); T.update(R["cfg"].get("thresholds", {}))
    off = set(R["cfg"].get("suppress", []))
    F = []
    add = lambda f: F.append(f) if f.rule not in off else None

    mets = {m["component"]: m for m in R["metrics"]}
    comp_mods, comp_edges = R["comp_mods"], R["comp_edges"]
    depth = R["cfg"]["component_depth"]
    reach, propcost, names, bit = reachability(comp_mods, comp_edges)
    total_specs = sum(v["specs"] for v in R["stats"].values()) or 1
    unaccounted = sum(v["unknown"] for v in R["stats"].values())
    ncomp = max(1, len(comp_mods))
    comp_of_path = {m.path: component_of(m.name, depth) for m in R["modules"].values()}

    # =========================== EVIDENCE QUALITY ======================
    if unaccounted:
        share = unaccounted / total_specs
        add(F_("ARCH-COVERAGE", "Evidence quality",
               "high" if share > T["coverage_high"] else "low",
               f"{unaccounted} of {total_specs} imports ({share:.0%}) point at "
               f"something this tool could not find on disk.",
               "Every conclusion below is drawn from the edges that did resolve. "
               "Unresolved local imports mean real dependencies are missing from "
               "the graph, so cycles may go undetected and coupling is understated. "
               "A map with unknown holes is more dangerous than no map, because it "
               "invites confidence.",
               "Usually a source root, path alias, or monorepo package boundary "
               "that has not been declared. Occasionally generated code, or "
               "imports assembled at runtime from strings.",
               "Add the missing `source_roots` or `aliases` to `.automap.json` and "
               "rerun until this is zero, or publish the graph as a lower bound "
               "and say so where it is published.",
               [f"{k}: {v['unknown']} unaccounted" for k, v in sorted(R["stats"].items())
                if v["unknown"]]))

    heur = sum(1 for e in R["edges"] if e.fidelity == HEURISTIC)
    if R["edges"] and heur / len(R["edges"]) > T["heuristic_share"]:
        add(F_("ARCH-FIDELITY", "Evidence quality", "medium",
               f"{heur} of {len(R['edges'])} edges "
               f"({heur/len(R['edges']):.0%}) come from heuristic scanners.",
               "Heuristic scanning matches import syntax by convention rather than "
               "by grammar. It finds most edges and occasionally invents or misses "
               "one. Findings that rest mostly on heuristic edges deserve a spot "
               "check against the source before anyone acts on them.",
               "The codebase is mostly in languages this tool reads lexically: "
               "PHP, Ruby, C and C++.",
               "Spot-check the evidence links on any finding you intend to act on. "
               "Heuristic edges are drawn dashed in the diagrams so they are easy "
               "to tell apart.",
               [f"{k}: {v['files']} files, {next(l.fidelity for l in LANGS if l.name==k)}"
                for k, v in sorted(R["stats"].items())
                if next(l.fidelity for l in LANGS if l.name == k) == HEURISTIC]))

    if not R["cfg"].get("layers"):
        add(F_("ARCH-NOLAYERS", "Evidence quality", "info",
               "No layering declared, so layer checks are off.",
               "Cycles and coupling are measurable without knowing your intent, "
               "but 'this dependency should not exist' is not. Declaring layers is "
               "how you tell the tool what the design is supposed to be, which "
               "turns a description into a check that can fail in CI.",
               "Most repositories never write the layering down; it lives in "
               "review comments and in whoever has been there longest.",
               "Add a `layers` map to `.automap.json`, ordered top to bottom. "
               "Start with the layering you believe you have — the first run will "
               "tell you whether you have it.", []))

    if churn_map is not None and not churn_map:
        add(F_("ARCH-NOVCS", "Evidence quality", "info",
               "No usable git history, so every change-over-time rule is off.",
               "Structure alone cannot tell you which coupling actually hurts. "
               "Coupling to something that never changes is nearly free; coupling "
               "to something edited weekly is not. History is what separates them.",
               "Run outside a repository, a shallow clone, or history not fetched.",
               "Run inside a full clone. A shallow clone will silently produce "
               "weaker findings rather than an error.", []))

    # =============================== STRUCTURE =========================
    if R["cycles"]:
        biggest = max(R["cycles"], key=len)
        add(F_("ARCH-CYCLE", "Structure", "high",
               f"{len(R['cycles'])} dependency cycle(s); the largest binds "
               f"{len(biggest)} of {ncomp} components ({len(biggest)/ncomp:.0%}).",
               "Components in a cycle cannot be built, tested, versioned, or "
               "understood one at a time. They are a single unit of change wearing "
               "several names. It is also why reading them in order is impossible: "
               "there is no order.",
               "Cycles are rarely designed. They appear when a lower-level "
               "component needs one convenience from a higher one, and the quickest "
               "fix is an import back upward rather than moving the shared thing "
               "down or inverting the dependency behind an interface.",
               "Take the edge inside the cycle held by the fewest import sites and "
               "break that one: move the shared type down into a component both "
               "sides may depend on, or define the interface on the lower side and "
               "inject the implementation. Then rerun and confirm it is gone.",
               [f"`{' <-> '.join(c[:6])}`" + (" …" if len(c) > 6 else "")
                for c in R["cycles"][:5]]))

    mcyc = module_cycles(R["modules"], R["edges"], depth)
    if mcyc:
        add(F_("ARCH-FILECYCLE", "Structure", "medium",
               f"{len(mcyc)} cycle(s) between modules inside a single component.",
               "These do not show on a component diagram, so they survive reviews "
               "that only look at the big picture. Inside the component they have "
               "the same effect: no file can be read first, and no file can be "
               "moved out on its own.",
               "Two modules that grew apart from one file, or a pair that each "
               "needed one thing from the other and resolved it locally.",
               "These are usually the cheapest cycles in the codebase to fix, "
               "because both files are owned by the same team and often by the "
               "same person. Fix them before touching component-level cycles.",
               [f"`{c}`: {' ↔ '.join(cy[:4])}" for c, cy in mcyc[:6]]))

    if propcost > T["propagation_medium"] and len(comp_mods) >= 5:
        add(F_("ARCH-PROPAGATION", "Structure",
               "high" if propcost > T["propagation_high"] else "medium",
               f"Propagation cost {propcost:.0%}: a change in an average component "
               f"can reach {propcost:.0%} of the others through import paths.",
               "This one number is the honest answer to 'how hard is this system "
               "to change'. Below roughly 10% the architecture is containing "
               "change. Above 30% most edits are potentially system-wide, review "
               "cannot be scoped, and the safe habit becomes changing nothing.",
               "Almost always one large cyclic core rather than many separate "
               "mistakes. A handful of components everything routes through will "
               "produce a high figure on their own.",
               "Do not attack the number directly. Break the largest cycle and "
               "re-measure; propagation cost falls in steps as cycles go, not "
               "gradually as edges go.",
               [f"{ncomp} components, {len(comp_edges)} couplings"]))

    keystones = [c for c in articulation_points(comp_mods, comp_edges)
                 if mets[c]["fan_in"] + mets[c]["fan_out"] >= 3]
    if keystones:
        add(F_("ARCH-KEYSTONE", "Structure", "medium",
               f"{len(keystones)} component(s) hold the graph together: removing "
               f"one would split the system into disconnected pieces.",
               "A keystone is where every path between two halves of the system "
               "runs. That concentrates risk: an outage, a rewrite, or a long-lived "
               "branch here blocks work on both sides. It also concentrates "
               "review, because nothing can be changed without someone who knows "
               "this component.",
               "Often deliberate and correct — a gateway, a bus, a shared kernel. "
               "Sometimes accidental, where a utility became the only path between "
               "two areas that should talk directly or not at all.",
               "Decide which it is. If deliberate, make it explicit: document the "
               "contract, and treat it as an interface with a compatibility policy. "
               "If accidental, the two sides usually want either a direct edge or "
               "no edge.",
               [f"`{c}` — {mets[c]['fan_in']} in, {mets[c]['fan_out']} out"
                for c in keystones[:6]]))

    sdp = [(a, b, mets[a]["instability"], mets[b]["instability"], es)
           for (a, b), es in comp_edges.items()
           if mets[b]["instability"] > mets[a]["instability"] + T["sdp_gap"]
           and mets[b]["fan_in"] >= 2]
    if sdp:
        sdp.sort(key=lambda t: t[3] - t[2], reverse=True)
        add(F_("ARCH-SDP", "Structure", "medium",
               f"{len(sdp)} dependencies point toward something less stable than "
               f"the thing depending on it.",
               "Instability here is fan-out over total coupling: how free a "
               "component is to change. Depending on something more volatile than "
               "yourself means inheriting its churn without owning it. Dependency "
               "should follow stability, or the stable side is not actually stable.",
               "Typically a settled component reaching for a helper that happens "
               "to live in an actively changing area, or a utility that has quietly "
               "accumulated dependencies of its own.",
               "Either move the borrowed piece into a component at least as stable "
               "as its consumer, or invert the dependency: define the interface on "
               "the stable side and let the volatile side implement it.",
               [f"`{a}` (I={ia}) → `{b}` (I={ib}) — {es[0].file}:{es[0].line}"
                for a, b, ia, ib, es in sdp[:6]]))

    A = abstractness(comp_mods)
    far = [(c, A[c], mets[c]["instability"], abs(A[c] + mets[c]["instability"] - 1))
           for c in A if abs(A[c] + mets[c]["instability"] - 1) > T["distance_main_sequence"]]
    if far:
        far.sort(key=lambda t: -t[3])
        add(F_("ARCH-MAINSEQ", "Structure", "low",
               f"{len(far)} component(s) sit far from the balance between how "
               f"abstract they are and how much depends on them.",
               "Two bad corners exist. A component that is concrete and widely "
               "depended on is rigid: it cannot change without breaking its "
               "dependents, and it offers no seam to extend through. A component "
               "that is abstract and depended on by nothing is unused indirection: "
               "interfaces with one implementation and no callers.",
               "Rigidity comes from exposing concrete types across a boundary "
               "instead of an interface. Unused abstraction comes from designing "
               "for a second implementation that never arrived.",
               "For the rigid ones, introduce an interface on the depended-on side "
               "and let dependents bind to that. For the unused abstractions, "
               "collapse the indirection until a second implementation actually "
               "exists.",
               [f"`{c}` — abstractness {a:.2f}, instability {i}, distance {d:.2f}"
                for c, a, i, d in far[:6]]))

    d = longest_chain(comp_mods, comp_edges, R["cycles"])
    if d >= T["chain_depth"]:
        add(F_("ARCH-DEPTH", "Structure", "low",
               f"The longest dependency chain is {d} components deep.",
               "Depth is what a newcomer has to hold at once. Following a request "
               "through a chain this long means keeping several intermediate "
               "abstractions in mind before reaching anything that does work. Depth "
               "is not a defect by itself, but it sets the floor on onboarding time.",
               "Layers added one at a time, each reasonable, each forwarding to the "
               "next. Pass-through components that only translate and delegate are "
               "the usual sign.",
               "Look for components in the chain that only forward. Collapsing a "
               "genuine pass-through removes a step from every future reader "
               "without changing behaviour.",
               [f"longest chain: {d} components"]))

    welded = sorted(((len(es), a, b) for (a, b), es in comp_edges.items()), reverse=True)
    if welded and welded[0][0] >= T["weld_sites"]:
        add(F_("ARCH-WELD", "Structure", "low",
               f"The heaviest coupling is held by {welded[0][0]} separate import "
               f"sites (`{welded[0][1]}` → `{welded[0][2]}`).",
               "The number of import sites is the cost of removing a dependency. "
               "One site is a decision; thirty is a merger. Two edges that look "
               "identical on a diagram can differ by an order of magnitude in the "
               "effort to cut, and planning that ignores this is planning fiction.",
               "Two components that grew as one, or a dependency used for many "
               "small unrelated reasons rather than one clear one.",
               "Before attempting a split, check whether the many uses are really "
               "one concept. If so, route them through a single facade first: that "
               "turns thirty sites into one and makes the later split cheap.",
               [f"`{a}` → `{b}` — {n} import sites" for n, a, b in welded[:5]]))

    solo = [m for m in R["metrics"] if m["fan_in"] == 1 and m["fan_out"] == 0]
    if len(solo) >= T["fanin_one_count"]:
        add(F_("ARCH-SOLOUSE", "Structure", "low",
               f"{len(solo)} component(s) are used by exactly one other component "
               f"and depend on nothing.",
               "A component boundary costs something to cross: a directory to "
               "navigate, a name to learn, an import to write, a place for "
               "circular dependencies to appear later. A boundary with one consumer "
               "and no dependencies is paying that cost without buying reuse, "
               "substitutability, or independent testing.",
               "Usually anticipated reuse that never happened, or a split made for "
               "tidiness rather than for a boundary that was actually needed.",
               "Check whether a second consumer is genuinely expected. If not, "
               "folding it into its one consumer removes a boundary from every "
               "future reader's map. If yes, leave it and note why.",
               [f"`{m['component']}` — used only by its single dependent, "
                f"{m['loc']} lines" for m in solo[:6]]))

    # =============================== BOUNDARIES ========================
    if R["violations"]:
        add(F_("ARCH-LAYER", "Boundaries", "high",
               f"{len(R['violations'])} dependency(ies) point upward through your "
               f"declared layers.",
               "A layering is a claim that the lower side can be understood, "
               "tested, and reused without the upper side. Each upward edge "
               "withdraws that claim. The cost is not aesthetic: the lower "
               "component can no longer be exercised in isolation.",
               "Usually a lower component needing a type, constant, or helper that "
               "was defined upward because that is where it was first needed.",
               "Move the shared definition down, or invert the call behind an "
               "interface the lower layer owns. If neither is right, the layering "
               "itself is wrong and `.automap.json` should change — an accurate "
               "declaration beats an aspirational one.",
               [f"`{v['from']}` ({v['from_layer']}) → `{v['to']}` ({v['to_layer']}) "
                f"— {v['sites'][0]}" for v in R["violations"][:6]]))

    layers = R["cfg"].get("layers", {})
    if len(layers) >= 3:
        rank = {c: i for i, (ln, cs) in enumerate(layers.items()) for c in cs}
        skips = [(a, b, es) for (a, b), es in comp_edges.items()
                 if a in rank and b in rank and rank[b] - rank[a] >= 2]
        if skips:
            add(F_("ARCH-SKIP", "Boundaries", "medium",
                   f"{len(skips)} dependency(ies) skip past an intermediate layer.",
                   "A middle layer usually exists to hold a policy: validation, "
                   "authorisation, caching, translation between models. Every edge "
                   "that jumps over it is a path where that policy does not run. "
                   "The layering still looks intact on a diagram, which is what "
                   "makes this one easy to miss.",
                   "A caller needing something the middle layer does not expose, "
                   "and reaching past it rather than widening it. Performance work "
                   "is a common origin.",
                   "Decide whether the middle layer's policy should apply on this "
                   "path. If yes, route through it and widen its interface. If no, "
                   "the layer is not a policy boundary and the declared layering "
                   "should say so.",
                   [f"`{a}` → `{b}` — {es[0].file}:{es[0].line}"
                    for a, b, es in skips[:6]]))

    seams = defaultdict(list)
    for e in R["edges"]:
        la, lb = R["modules"][e.src].lang, R["modules"][e.dst].lang
        if la != lb:
            seams[(la, lb)].append(e)
    if seams:
        add(F_("ARCH-SEAM", "Boundaries", "medium",
               f"{sum(len(v) for v in seams.values())} imports cross a language "
               f"boundary, across {len(seams)} language pair(s).",
               "No compiler or linter validates both sides of these edges. A "
               "rename or signature change on one side is caught at runtime, by "
               "whoever runs that path first, which is often a user.",
               "Gradual migrations that stalled, or a shared module that predates "
               "a language decision and was never moved.",
               "Give the seam an explicit contract with its own tests: generated "
               "types, a schema, or at minimum one narrow module both sides go "
               "through, so the surface that can break is small and named.",
               [f"{la} → {lb}: {len(es)} edges, first at {es[0].file}:{es[0].line}"
                for (la, lb), es in sorted(seams.items())[:5]]))

    dr = R.get("deep_relative", [])
    if len(dr) >= T["deep_relative"]:
        add(F_("ARCH-REACHUP", "Boundaries", "low",
               f"{len(dr)} imports climb two or more directories to reach their "
               f"target.",
               "A path like `../../../lib/db` is a dependency that refuses to name "
               "itself. It breaks on any move, it hides the real coupling from "
               "anyone scanning imports, and it is a reliable sign that the "
               "directory a file lives in is not the one it belongs to.",
               "Files placed by feature but depending by layer, or the reverse. "
               "Also common right after a directory reshuffle that moved files "
               "without revisiting their dependencies.",
               "Configure path aliases or package roots so these become named "
               "imports. Once they are named, the coupling shows up in this graph "
               "instead of hiding in punctuation.",
               [f"`{f}:{ln}` → `{sp}`" for f, ln, sp in dr[:6]]))

    vague = [c for c in comp_mods if VAGUE_NAMES.match(c.split(".")[-1])]
    if vague:
        add(F_("ARCH-VAGUE", "Boundaries", "low",
               f"{len(vague)} component(s) are named for what they contain rather "
               f"than what they do: {', '.join('`%s`' % c for c in vague[:5])}.",
               "A name like `utils` or `common` states no membership rule, so no "
               "code can ever be argued out of it. These components grow "
               "monotonically, acquire dependents from everywhere, and reliably "
               "turn into the hubs and cycles reported elsewhere in this document. "
               "The naming is not the problem; it is the earliest visible symptom.",
               "A file needed in two places, no obvious home, and a directory that "
               "accepts anything.",
               "Split by what the code is for, not by what it is. If a rule for "
               "what belongs cannot be written in one sentence, the component is "
               "not one component.",
               [f"`{c}` — {len(comp_mods[c])} modules, "
                f"{sum(m.loc for m in comp_mods[c])} lines, "
                f"{mets[c]['fan_in']} dependents" for c in vague[:6]]))

    CONVENTIONAL = {"__init__", "index", "mod", "lib", "main", "setup", "types",
                    "constants", "conftest", "package-info", "__main__"}
    basenames = defaultdict(set)
    for m in R["modules"].values():
        stem = Path(m.path).stem
        if stem in CONVENTIONAL:
            continue          # a convention repeated on purpose is not a collision
        basenames[stem].add(component_of(m.name, depth))
    dupes = sorted(((len(cs), n) for n, cs in basenames.items()
                    if len(cs) >= T["dup_name_components"]), reverse=True)
    if dupes:
        add(F_("ARCH-DUPNAME", "Boundaries", "low",
               f"{len(dupes)} filename(s) appear in four or more components, "
               f"the most repeated being `{dupes[0][1]}` in {dupes[0][0]}.",
               "Repeated names make every conversation and every search ambiguous. "
               "More practically, a stack trace or a review comment naming the file "
               "no longer identifies it, and readers open the wrong one.",
               "A per-component convention like `models.py` or `handler.go` in "
               "every package. Sometimes genuine parallel structure, sometimes "
               "copy-paste that has since diverged.",
               "Where the parallel structure is real, this is fine and worth "
               "leaving. Where the files have diverged, rename to what each "
               "actually holds. The test is whether you can predict a file's "
               "contents from its name alone.",
               [f"`{n}` — in {c} components" for c, n in dupes[:6]]))

    # =========================== CHANGE OVER TIME ======================
    if churn_map:
        by_comp = defaultdict(int)
        for m in R["modules"].values():
            if m.path in churn_map:
                by_comp[component_of(m.name, depth)] += churn_map[m.path]
        if by_comp:
            vals = sorted(by_comp.values(), reverse=True)
            cut = vals[max(0, len(vals) // 4 - 1)]
            hot = sorted(((n, c) for c, n in by_comp.items()
                          if n >= cut and mets.get(c, {}).get("fan_in", 0) >= 3),
                         reverse=True)
            if hot:
                add(F_("ARCH-CHURN", "Change over time", "medium",
                       f"{len(hot)} component(s) are both heavily depended on and "
                       f"heavily edited in the last year.",
                       "Coupling only costs something when the coupled thing moves. "
                       "A stable hub is fine; a hub that changes weekly means every "
                       "dependent is repeatedly exposed to churn it did not ask "
                       "for. This is also where any mental model decays fastest, so "
                       "it is the first place documentation goes stale.",
                       "Often a component holding two responsibilities with "
                       "different rates of change: a stable core interface bundled "
                       "with the part that tracks a moving external requirement.",
                       "Separate along the rate-of-change line. The volatile half "
                       "keeps the churn and few dependents; the stable half keeps "
                       "the dependents and stops moving.",
                       [f"`{c}` — {n} lines touched, {mets[c]['fan_in']} dependents"
                        for n, c in hot[:6]]))

    if root is not None and churn_map:
        pairs, ncommits, wide = cochange(root, lambda f: comp_of_path.get(f))
        hidden = [] if ncommits < T["min_commits"] else [
                  (n, a, b) for (a, b), n in pairs.items()
                  if n >= T["cochange_min"]
                  and (a, b) not in comp_edges and (b, a) not in comp_edges]
        hidden.sort(reverse=True)
        if hidden:
            add(F_("ARCH-COCHANGE", "Change over time", "high",
                   f"{len(hidden)} component pair(s) are repeatedly changed in the "
                   f"same commit despite having no import between them.",
                   "This is coupling the import graph cannot see, and it is often "
                   "the coupling that actually hurts. Two components that must "
                   "change together are coupled through something — a wire format, "
                   "a database column, a duplicated constant, an assumption — and "
                   "because nothing links them in code, nothing warns the person "
                   "who changes only one.",
                   "A shared schema or protocol with no shared definition, "
                   "copy-pasted logic that has to be kept in step, or a genuine "
                   "feature that was split across a boundary in the wrong place.",
                   "Make the hidden contract explicit: one shared type, schema, or "
                   "constant that both sides import, so the next change to it "
                   "cannot silently miss a side. Where the split itself was wrong, "
                   "moving the code together is cheaper than maintaining the "
                   "coincidence.",
                   [f"`{a}` and `{b}` — {n} commits together, no import"
                    for n, a, b in hidden[:6]]))

        if ncommits >= T["min_commits"] and wide / ncommits > T["shotgun_share"]:
            add(F_("ARCH-SHOTGUN", "Change over time", "medium",
                   f"{wide/ncommits:.0%} of commits touch four or more components "
                   f"at once ({wide} of {ncommits}).",
                   "When a typical change has to be made in several places, the "
                   "boundaries are not aligned with the way the system actually "
                   "changes. The cost shows up as large reviews, wide blast "
                   "radius, and merge conflicts between people working on "
                   "unrelated features.",
                   "Components organised by technical kind — controllers, models, "
                   "services — while work arrives organised by feature. Every "
                   "feature then crosses every component.",
                   "Look at what the wide commits have in common. If the same "
                   "group of components appears repeatedly, that group is the real "
                   "module, and the current boundaries are cutting across it.",
                   [f"{wide} of {ncommits} recent commits span 4+ components"]))

        auth = authorship(root) if ncommits >= T["min_commits"] else {}
        if auth:
            per_comp = defaultdict(lambda: defaultdict(int))
            files_per = defaultdict(int)
            for f, who in auth.items():
                c = comp_of_path.get(f)
                if not c:
                    continue
                files_per[c] += 1
                if len(who) == 1:
                    per_comp[c][next(iter(who))] += 1
            risky = []
            for c, byauthor in per_comp.items():
                if files_per[c] < 3:
                    continue
                top, n = max(byauthor.items(), key=lambda kv: kv[1])
                if n / files_per[c] >= T["single_author_share"] and mets.get(c, {}).get("fan_in", 0) >= 1:
                    risky.append((n / files_per[c], c, top, files_per[c]))
            risky.sort(reverse=True)
            if risky:
                add(F_("ARCH-BUSFACTOR", "Change over time", "medium",
                       f"{len(risky)} component(s) have had essentially one author, "
                       f"while other components depend on them.",
                       "Concentrated authorship is a property of the code, not just "
                       "of the roster. It means the design decisions inside were "
                       "never explained to a second person at the time they were "
                       "made, and reconstructing them later costs far more than "
                       "writing them down would have.",
                       "Ownership that worked well and was never revisited, or a "
                       "component nobody else wants to touch because it is hard.",
                       "Route the next non-urgent change here to someone else and "
                       "have the original author review rather than write. That "
                       "surfaces the undocumented assumptions while there is still "
                       "someone around to confirm them.",
                       [f"`{c}` — {share:.0%} of {nf} files by a single author"
                        for share, c, who, nf in risky[:6]]))

    # ============================ SIZE AND SHAPE =======================
    locs = sorted(m.loc for m in R["modules"].values())
    if locs:
        med = locs[len(locs) // 2] or 1
        giants = sorted(((m.loc, m.path) for m in R["modules"].values()
                         if m.loc > max(T["god_file_min_loc"], med * T["god_file_multiple"])),
                        reverse=True)
        if giants:
            add(F_("ARCH-GODFILE", "Size and shape", "medium",
                   f"{len(giants)} module(s) are more than {T['god_file_multiple']:g}× "
                   f"the median size ({med} lines); the largest is {giants[0][0]} lines.",
                   "A file this far from the median is rarely one idea. It cannot "
                   "be reviewed in one sitting, it produces merge conflicts between "
                   "people working on unrelated things, and it hides its internal "
                   "structure from every tool that works at file granularity — "
                   "including this one, which sees it as a single node.",
                   "Accretion. Each addition was small and reasonable, and no "
                   "single commit was the one that made it too large.",
                   "Split along the lines its own imports suggest: the groups of "
                   "functions that share dependencies are usually the natural "
                   "modules. Do it before it becomes the file everyone avoids.",
                   [f"`{p_}` — {n} lines" for n, p_ in giants[:6]]))

    total_loc = sum(m.loc for m in R["modules"].values()) or 1
    if len(comp_mods) >= 5:
        big = [(sum(m.loc for m in ms) / total_loc, c) for c, ms in comp_mods.items()]
        big.sort(reverse=True)
        if big and big[0][0] > T["god_component_share"]:
            share, c = big[0]
            add(F_("ARCH-GODCOMPONENT", "Size and shape", "medium",
                   f"`{c}` holds {share:.0%} of all code in the tree.",
                   "One component holding a third of the system means the "
                   "decomposition is mostly nominal: the other boundaries exist, "
                   "but the bulk of the work happens in one place that has no "
                   "internal boundaries at all. Everything this document says about "
                   "component structure says very little about most of your code.",
                   "A core that grew while peripheral concerns were split out "
                   "around it, or a monolith with a few extracted services.",
                   "Increase `component_depth` in `.automap.json` and rerun. If the "
                   "sub-structure looks meaningful, that is the level your "
                   "architecture actually lives at; if it looks arbitrary, that is "
                   "the finding.",
                   [f"`{cc}` — {sh:.0%} of {total_loc} lines" for sh, cc in big[:5]]))

    wide_api = sorted(((sum(len(m.public) for m in ms), c)
                       for c, ms in comp_mods.items()), reverse=True)
    if wide_api and wide_api[0][0] >= T["wide_api"] and mets[wide_api[0][1]]["fan_in"] >= 2:
        n, c = wide_api[0]
        add(F_("ARCH-WIDEAPI", "Size and shape", "low",
               f"`{c}` exposes {n} public symbols to {mets[c]['fan_in']} dependents.",
               "Everything exported is a promise. A surface this wide cannot be "
               "changed safely because no one knows which parts are actually used, "
               "and it cannot be learned quickly because there is no indication of "
               "where to start. Wide surfaces also disguise how much of a component "
               "is genuinely internal.",
               "Exporting by default. Most languages make it easier to expose a "
               "symbol than to hide one, so surfaces grow without a decision.",
               "Check which exports are imported anywhere in this tree; the "
               "evidence sections of this document list the actual import sites. "
               "Anything unused outside the component should stop being public.",
               [f"`{cc}` — {nn} public symbols, {mets[cc]['fan_in']} dependents"
                for nn, cc in wide_api[:5] if nn >= T["wide_api"] // 2]))

    imported = {e.dst for e in R["edges"]}
    named = re.compile(r'(^|[./])(main|index|app|cli|server|program|__main__|lib|setup|conftest)$', re.I)
    orphans = [m for n_, m in sorted(R["modules"].items())
               if n_ not in imported and not named.search(n_)
               and m.loc > T["orphan_min_loc"]]
    if len(orphans) >= T["orphan_count"]:
        add(F_("ARCH-ORPHAN", "Size and shape", "low",
               f"{len(orphans)} modules over {T['orphan_min_loc']} lines are "
               f"imported by nothing in this tree.",
               "Unreferenced code still gets read, still gets updated during "
               "refactors, and still appears in searches. If it is genuinely unused "
               "it is a tax on every future reader. If it is used through a "
               "mechanism no static tool can see, that mechanism is exactly the "
               "thing worth writing down, because nobody will infer it.",
               "Entry points invoked by a runner or framework, plugins loaded by "
               "name, code kept 'just in case', or genuine leftovers.",
               "Check each against how it is actually invoked. Delete what is dead; "
               "for the rest, record the invocation mechanism where a reader will "
               "find it.",
               [f"`{m.path}` — {m.loc} lines" for m in orphans[:8]]))

    eps = entry_points(R["modules"], R["edges"], comp_edges)
    for m in eps[:3]:
        c = component_of(m.name, depth)
        if c in bit:
            n_reach = bin(reach[c]).count("1")
            if n_reach / ncomp > T["entry_sprawl"] and ncomp >= 5:
                add(F_("ARCH-ENTRYSPRAWL", "Size and shape", "low",
                       f"Entry point `{m.path}` reaches {n_reach} of {ncomp} "
                       f"components ({n_reach/ncomp:.0%}).",
                       "An entry point that pulls in most of the system has no "
                       "meaningful startup boundary. In practice this means slow "
                       "start-up, slow tests, and no way to run a part of the "
                       "system without running nearly all of it.",
                       "Convenience imports at the top level, or a root module that "
                       "wires everything eagerly instead of on demand.",
                       "Look at what the entry point imports directly rather than "
                       "transitively; the fix is usually a handful of eager imports "
                       "that could be deferred to the code path that needs them.",
                       [f"{m.path} reaches {n_reach}/{ncomp} components"]))
                break

    # ============================= DEPENDENCIES ========================
    ext = R["external"]
    if ext:
        per_comp_ext = defaultdict(set)
        sites, comps_of_pkg = defaultdict(int), defaultdict(set)
        for pkg, es in ext.items():
            for e in es:
                c = component_of(e.src, depth)
                per_comp_ext[c].add(pkg)
                sites[pkg] += 1
                comps_of_pkg[pkg].add(c)
        heavy = sorted(((len(v), c) for c, v in per_comp_ext.items()), reverse=True)
        if heavy and heavy[0][0] >= T["dep_fanout"]:
            add(F_("ARCH-DEPFANOUT", "Dependencies", "low",
                   f"`{heavy[0][1]}` imports {heavy[0][0]} distinct third-party "
                   f"packages.",
                   "Each third-party package is an upgrade obligation, a "
                   "vulnerability surface, and a set of assumptions the component "
                   "now carries. Concentrated in one component this is often fine "
                   "and sometimes deliberate; it becomes a problem when that "
                   "component is also widely depended on, because its dependents "
                   "inherit all of it.",
                   "A component doing integration work, or one that has become the "
                   "place where anything requiring a library ends up.",
                   "Check whether the dependents of this component need any of "
                   "these packages in their own right. If not, the packages belong "
                   "behind a narrower interface so upgrades stay local.",
                   [f"`{c}` — {n} third-party packages" for n, c in heavy[:5]]))

        locked = sorted(((sites[p_], len(comps_of_pkg[p_]), p_) for p_ in ext
                         if sites[p_] >= T["vendor_sites"]
                         and len(comps_of_pkg[p_]) >= T["vendor_components"]), reverse=True)
        if locked:
            add(F_("ARCH-VENDORSPREAD", "Dependencies", "medium",
                   f"`{locked[0][2]}` is imported directly at {locked[0][0]} sites "
                   f"across {locked[0][1]} components.",
                   "A third-party API used directly in this many places is no "
                   "longer a dependency, it is part of your architecture. "
                   "Replacing it, upgrading across a breaking change, or even "
                   "adding cross-cutting behaviour like retries or logging becomes "
                   "a change to every one of those sites.",
                   "Adopting a library everywhere it is useful, which is the "
                   "natural thing to do and only becomes visible as a problem at "
                   "the first breaking upgrade.",
                   "Wrap it at one boundary: a thin module that exposes only what "
                   "you use, with everything else importing that. The wrapper does "
                   "not need to be clever, only singular.",
                   [f"`{p_}` — {n} sites across {k} components"
                    for n, k, p_ in locked[:6]]))

        present = {p_.lower() for p_ in ext}
        clashes = [(g, sorted(present & {x.lower() for x in members}))
                   for g, members in OVERLAP_GROUPS.items()
                   if len(present & {x.lower() for x in members}) >= 2]
        if clashes:
            add(F_("ARCH-DUPLIB", "Dependencies", "low",
                   f"{len(clashes)} job(s) in this codebase are done by two or more "
                   f"different libraries.",
                   "Two libraries for one job means two APIs, two sets of edge-case "
                   "behaviour, two upgrade schedules, and a decision every reader "
                   "has to make about which to use next. The cost is not the extra "
                   "dependency; it is that the codebase no longer answers the "
                   "question for you.",
                   "Usually a migration that was started and not finished, or "
                   "different teams choosing independently before a convention "
                   "existed.",
                   "Pick one and record the choice where the next person will see "
                   "it. Then either finish the migration or scope the old one "
                   "explicitly, so its presence is a decision rather than a "
                   "leftover.",
                   [f"{g}: {', '.join('`%s`' % x for x in found)}"
                    for g, found in clashes[:6]]))

    # ================================ TESTING ==========================
    tested, testmods = R.get("tested", set()), R.get("test_modules", {})
    if testmods:
        untested = sorted(c for c in comp_mods
                          if c not in tested and mets[c]["fan_in"] >= 1)
        if untested and len(untested) / ncomp >= T["untested_share"]:
            add(F_("ARCH-UNTESTED", "Testing", "medium",
                   f"{len(untested)} of {ncomp} components are not imported by any "
                   f"test file, yet other components depend on them.",
                   "This measures reach, not quality: whether the test suite ever "
                   "loads the component at all. A component nothing depends on and "
                   "nothing tests is low risk. A component others depend on and no "
                   "test touches is where a refactor will be silently wrong.",
                   "Tests written against the outermost layer only, so inner "
                   "components are exercised incidentally through it or not at all.",
                   "Start with the untested components that have the highest "
                   "fan-in; those are the ones whose breakage propagates furthest.",
                   [f"`{c}` — {mets[c]['fan_in']} dependents, no test imports it"
                    for c in sorted(untested, key=lambda x: -mets[x]["fan_in"])[:8]]))
    elif R["cfg"].get("test_dirs"):
        add(F_("ARCH-NOTESTS", "Testing", "medium",
               "No test files were found anywhere in the tree.",
               "Without tests, every finding in this document is harder to act on. "
               "Cycles, layer violations, and oversized components are all fixed by "
               "moving code, and moving code without tests means each fix carries "
               "risk that has nothing to do with the fix itself.",
               "Tests kept in a separate repository, or named in a way this tool "
               "does not recognise.",
               "If they exist elsewhere, add their directory to `roots` or their "
               "naming to `test_dirs` in `.automap.json` so this check means "
               "something.", []))

    F.sort(key=lambda f: (SEV_ORDER[f.severity], CATEGORIES.index(f.category), f.rule))
    return F, propcost


def render_findings(F, full=False):
    L = []; w = L.append
    w("## What this says about the system\n")
    w("Each item fired because a measurement crossed a threshold. The numbers and "
      "the evidence are from your code; the explanation is fixed text from a rule "
      "catalog, identical every time that rule fires on any repository. "
      "`automap rules` prints the catalog on its own so you can audit the claims "
      "before trusting them here. What none of it can tell you is why your team "
      "built it this way — that is what `automap adr` leaves blank.\n")
    if not F:
        w("No rule fired. That is a real result rather than an empty section: no "
          "cycles, no upward dependencies, no hubs or hidden coupling above "
          "threshold.\n")
        return "\n".join(L)

    counts = defaultdict(int)
    for f in F:
        counts[f.severity] += 1
    w("| | count |")
    w("|---|---:|")
    for sev, label in [("high", "Serious"), ("medium", "Worth attention"),
                       ("low", "Minor"), ("info", "Notes")]:
        if counts[sev]:
            w(f"| {label} | {counts[sev]} |")
    w("")

    label = {"high": "Serious", "medium": "Worth attention", "low": "Minor", "info": "Note"}
    shown = F if full else F[:12]
    for f in shown:
        w(f"### {label[f.severity]} · {f.headline}\n")
        w(f"**Why it matters.** {f.why}\n")
        w(f"**What usually causes it.** {f.cause}\n")
        w(f"**What to do.** {f.action}\n")
        if f.evidence:
            w("<details><summary>Evidence</summary>\n")
            for e in f.evidence:
                w(f"- {e}")
            w("\n</details>\n")
        w(f"<sub>`{f.rule}` · {f.category}</sub>\n")
    if len(F) > len(shown):
        w(f"_{len(F) - len(shown)} further finding(s); `--full` shows them all._\n")
    return "\n".join(L)


# =====================================================================
# 13. CODE-LEVEL ANALYSIS
#
#   Everything above reasons about the import graph. This section reads
#   inside files, which is a different kind of evidence with a different
#   error profile, and it is reported separately for that reason.
#
#   Python is analysed with ast, so its metrics are exact. Every other
#   language is matched lexically over comment-masked source: the tool
#   reports the PRESENCE OF A CONSTRUCT, not a proven defect. There is no
#   dataflow here. A flagged line may be entirely correct in context, and
#   an unflagged file may still be wrong. Treat these as places to look.
# =====================================================================

@dataclass
class CodeRule:
    id: str
    category: str
    severity: str
    langs: tuple            # () means every language
    pattern: str
    why: str
    cause: str
    action: str
    in_loop: bool = False   # only counts when the match is inside a loop body
    flags: int = re.M


SEC = "Security"; PERF = "Performance"; ALGO = "Algorithms and data structures"
MNT = "Maintainability"; RDB = "Readability"; SCL = "Scalability"

CODE_RULES = [
 CodeRule("SEC-EVAL", SEC, "high", (),
   r'\b(eval|exec)\s*\(|new\s+Function\s*\(|\bassert\s*\(\s*eval|setTimeout\s*\(\s*["\']',
   "Evaluating a string as code means the set of things this program can do is "
   "not fixed at build time. If any part of that string is influenced by input, "
   "the answer is 'anything the process can do'. It also defeats every other tool "
   "in the pipeline: type checkers, linters, and this one cannot see through it.",
   "Usually dynamic dispatch, config-driven behaviour, or deserialising something "
   "convenient. Almost always reachable another way.",
   "Replace with an explicit dispatch table mapping allowed names to functions. "
   "If the input really is arbitrary code, isolate it in a sandboxed process with "
   "its own privileges."),

 CodeRule("SEC-SHELL", SEC, "high", (),
   r'os\.system\s*\(|subprocess\.\w+\([^)]*shell\s*=\s*True|child_process\.exec\s*\(|'
   r'Runtime\.getRuntime\(\)\.exec|\bpopen\s*\(|shell_exec\s*\(|`[^`\n]*\$\{',
   "Handing a string to a shell means the shell parses it: quoting, globbing, "
   "pipes, and semicolons all apply. Any input that reaches that string can add "
   "another command. This is command injection, and it is one of the oldest and "
   "most reliably exploited defects there is.",
   "Building a command line by concatenation because it is the shortest way to "
   "call an external tool.",
   "Pass an argument list rather than a string, and do not involve a shell: "
   "`subprocess.run([...], shell=False)`, `execFile`, `ProcessBuilder`. If a "
   "shell feature is genuinely needed, validate against an allowlist first."),

 CodeRule("SEC-DESERIALIZE", SEC, "high", (),
   r'pickle\.loads?\s*\(|cPickle\.loads?\s*\(|yaml\.load\s*\((?![^)]*Safe)|'
   r'Marshal\.load|ObjectInputStream|unserialize\s*\(|jsonpickle\.decode',
   "These formats do not merely carry data: they carry instructions for "
   "reconstructing objects, which can include running code. Deserialising "
   "untrusted input with them is equivalent to running it. No amount of "
   "validation after the fact helps, because execution happens during parsing.",
   "Choosing the format that round-trips native objects with least effort, often "
   "for a cache or an inter-process queue that later grew an external input.",
   "Use a data-only format: JSON, or `yaml.safe_load`. Where native objects are "
   "genuinely needed, keep the channel internal and authenticated, and write down "
   "that assumption next to the call."),

 CodeRule("SEC-SQLCONCAT", SEC, "high", (),
   r'(?i)(SELECT|INSERT|UPDATE|DELETE)\b[^;\n]{0,120}?(\+\s*\w+|%\s*[\(\w]|\$\{|'
   r'\.format\s*\(|f["\'][^"\']*\{|\|\|\s*\w+)',
   "A query assembled by concatenation cannot distinguish the query's structure "
   "from its data, so any input that reaches it can change what the query does. "
   "Escaping by hand is not a fix; the parser rules are more complicated than the "
   "escaping usually accounts for.",
   "A query that started static and gained one dynamic value, most often an ORDER "
   "BY or an IN clause that parameter binding makes awkward.",
   "Use parameter binding for values. Where the dynamic part is an identifier or "
   "a sort direction, validate it against an explicit allowlist, since binding "
   "cannot parameterise those."),

 CodeRule("SEC-WEAKCRYPTO", SEC, "medium", (),
   r'\b(md5|sha1|MD5|SHA1)\s*\(|MessageDigest\.getInstance\s*\(\s*"(MD5|SHA-?1)"|'
   r'\bDES\b|\bRC4\b|AES/ECB|Cipher\.getInstance\s*\(\s*"[^"]*ECB',
   "MD5 and SHA-1 have practical collision attacks, DES has an exhaustible key "
   "space, and ECB mode leaks structure because identical plaintext blocks "
   "produce identical ciphertext. Each is fine for a checksum and wrong for "
   "anything where an adversary benefits from forging or reading.",
   "Copied from an older example, or chosen when the use was non-security and "
   "later became security-relevant.",
   "For integrity use SHA-256 or better; for passwords use argon2, scrypt, or "
   "bcrypt, never a plain hash; for encryption use AES-GCM or a library that "
   "picks the mode for you. Where the use is genuinely a non-security checksum, "
   "say so in a comment so the next reader does not have to re-derive it."),

 CodeRule("SEC-TLSOFF", SEC, "high", (),
   r'verify\s*=\s*False|InsecureSkipVerify\s*:\s*true|rejectUnauthorized\s*:\s*false|'
   r'NODE_TLS_REJECT_UNAUTHORIZED|CURLOPT_SSL_VERIFYPEER\s*,\s*(0|false)|'
   r'ServicePointManager\.ServerCertificateValidationCallback',
   "Disabling certificate verification removes the only thing that distinguishes "
   "the intended server from anyone on the network path. The connection is still "
   "encrypted, which is what makes this easy to miss: it looks like TLS and "
   "provides none of the authentication.",
   "Almost always a self-signed certificate in a development or staging "
   "environment, with the workaround shipped by accident.",
   "Point the client at the correct CA bundle instead. For internal certificates, "
   "add the internal CA to the trust store rather than disabling the check."),

 CodeRule("SEC-XSS", SEC, "medium", ("JavaScript", "TypeScript"),
   r'\.innerHTML\s*=|dangerouslySetInnerHTML|document\.write\s*\(|'
   r'\.outerHTML\s*=|v-html\s*=|\$\(\s*[^)]*\)\.html\s*\(',
   "Assigning to innerHTML parses the string as markup, so any input reaching it "
   "can introduce script, event handlers, or elements that change the page's "
   "meaning. The framework's escaping does not apply here — this is the escape "
   "hatch from it.",
   "Rendering a fragment of server-provided HTML, or a rich-text field where "
   "plain text seemed insufficient.",
   "Set `textContent` when the value is text. When markup is genuinely required, "
   "sanitise with a maintained library at the point of assignment, not earlier."),

 CodeRule("SEC-UNSAFEC", SEC, "high", ("C/C++",),
   r'\b(strcpy|strcat|sprintf|gets|scanf|alloca)\s*\(',
   "These functions take no destination size, so the caller is responsible for "
   "guaranteeing capacity. That guarantee is easy to write and easy to break "
   "later when a nearby buffer changes size. The result is a memory-corruption "
   "bug, which is the most exploitable class there is.",
   "Idiom carried over from older code, or a buffer that was provably large "
   "enough when written.",
   "Use the bounded forms: `snprintf`, `strlcpy`, `strncat` with an explicit "
   "size, `fgets`. In C++, prefer `std::string` and `std::vector` so the size "
   "travels with the data."),

 CodeRule("SEC-SECRETLIKE", SEC, "high", (),
   r'(?i)\b(api[_-]?key|secret|passwd|password|token|private[_-]?key|access[_-]?key)\b'
   r'\s*[:=]\s*["\'][A-Za-z0-9/+_\-]{16,}["\']|AKIA[0-9A-Z]{16}|'
   r'ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY',
   "A credential in source is a credential in every clone, every fork, every CI "
   "cache, and the entire git history. Deleting the line does not remove it; the "
   "commit still contains it. Rotation is the only real remediation, which is why "
   "these are worth catching before they are committed rather than after.",
   "A value needed to make something work locally, added with the intention of "
   "moving it to configuration later.",
   "Rotate the credential first, on the assumption it is already public. Then "
   "move it to environment configuration or a secret manager, and add a "
   "pre-commit scan so the next one is caught before it lands."),

 CodeRule("SEC-DEBUGLEFT", SEC, "medium", (),
   r'\bdebugger\s*;|pdb\.set_trace\s*\(|breakpoint\s*\(\s*\)|binding\.pry|'
   r'console\.trace\s*\(|DEBUG\s*=\s*True|app\.run\([^)]*debug\s*=\s*True',
   "Debug facilities left enabled expose stack traces, local variables, and in "
   "some frameworks an interactive console, to whoever triggers an error. They "
   "also change behaviour: debug modes commonly disable caching, relax CORS, and "
   "log request bodies.",
   "Left from a debugging session, or a default that was never overridden for the "
   "production configuration.",
   "Remove the interactive hooks. Make debug flags read from environment "
   "configuration with a safe default, so production requires no action to be "
   "correct."),

 CodeRule("SEC-PERMS", SEC, "low", (),
   r'chmod\s*\(\s*[^,]+,\s*0?o?7[0-7][0-7]|chmod\s+777|os\.umask\s*\(\s*0\s*\)|'
   r'FileMode\(\s*0?o?777',
   "World-writable permissions mean any local account can replace the contents. "
   "For a script or a config file that is read later, this is a straightforward "
   "path to running someone else's code as this service.",
   "A permissions error solved by widening until it stopped failing.",
   "Grant the narrowest mode that works, usually 0644 for data and 0755 for "
   "executables, and set ownership rather than widening permissions."),

 # ------------------------------- PERFORMANCE ----------------------
 CodeRule("PERF-NPLUSONE", PERF, "high", (),
   r'\b(db|conn|connection|session|cursor|repo|repository|client|store|orm|em|'
   r'entityManager|dao|collection|model|Model|prisma|knex|sequelize)\b'
   r'[\w.]*\.\s*\w*(query|execute|exec|find|fetch|select|insert|update|delete|save|'
   r'aggregate|scan|get_one|first|all)\w*\s*\(|'
   r'requests\.(get|post|put|delete|patch)\s*\(|urlopen\s*\(|'
   r'\bhttpx?\.\w+\s*\(|\bfetch\s*\(\s*[\'"`]|axios\.\w+\s*\(',
   "A query or request issued once per iteration turns one operation into N. The "
   "code reads correctly and passes tests on small fixtures, then degrades "
   "linearly with data size in production. This is the single most common cause "
   "of an endpoint that was fast in development and is slow in production.",
   "Iterating over parents and fetching each one's children, which is the natural "
   "way to express it and the natural thing an ORM makes easy.",
   "Fetch the set in one call: a join, an `IN` query, a batched request, or the "
   "ORM's eager-loading option. Where the calls are independent network requests, "
   "issue them concurrently rather than in sequence.",
   in_loop=True),

 CodeRule("PERF-LOOPCONCAT", PERF, "medium", (),
   r'\w+\s*\+=\s*["\']|\w+\s*=\s*\w+\s*\+\s*["\']|\w+\.concat\s*\(',
   "In languages with immutable strings, appending inside a loop allocates and "
   "copies the whole accumulated string each time, so building an n-character "
   "result costs on the order of n². It is invisible at small sizes and sudden "
   "at large ones.",
   "The most direct way to express 'build up a string', and correct in languages "
   "where strings are mutable or the compiler rewrites it.",
   "Append to a list and join once at the end, or use the language's builder "
   "type: `''.join(parts)`, `strings.Builder`, `StringBuilder`, `array.join('')`.",
   in_loop=True),

 CodeRule("PERF-SYNCIO", PERF, "medium", ("JavaScript", "TypeScript"),
   r'\w+Sync\s*\(|execSync\s*\(|readFileSync|writeFileSync|existsSync',
   "Synchronous I/O blocks the event loop, which in a single-threaded runtime "
   "means every other request waits, not just this one. Throughput collapses "
   "under concurrency even though each individual operation looks fast.",
   "Startup and CLI code where blocking is fine, later reused inside a request "
   "path where it is not.",
   "Use the promise-based forms and await them. Where the call really is "
   "startup-only, keep it out of any module that a request path imports so it "
   "cannot be reused by accident."),

 CodeRule("PERF-REGEXLOOP", PERF, "low", (),
   r're\.compile\s*\(|new\s+RegExp\s*\(|regexp\.MustCompile\s*\(|Pattern\.compile\s*\(',
   "Compiling a pattern is far more expensive than matching with one. Doing it "
   "inside a loop repeats that cost on every iteration, and the compiled result "
   "is discarded immediately.",
   "Keeping the pattern next to its use for readability, which is a reasonable "
   "instinct in code that is not hot.",
   "Hoist the compilation out of the loop, to module scope or a constant. The "
   "pattern is fixed; only the input changes.",
   in_loop=True),

 CodeRule("PERF-SELECTSTAR", PERF, "low", (),
   r'(?i)SELECT\s+\*\s+FROM',
   "Selecting every column transfers and deserialises data the caller does not "
   "use, prevents the database from answering from an index alone, and couples "
   "the code to column order and to columns that have not been added yet.",
   "Convenience during development, or a query that genuinely needed most "
   "columns at the time it was written.",
   "Name the columns actually used. It is also the cheapest way to make the "
   "query's real dependencies visible to whoever changes the schema next."),

 CodeRule("PERF-NESTEDLOOP", PERF, "medium", (),
   r'',   # computed structurally, not by pattern
   "Three levels of loop nesting means work proportional to the product of three "
   "collection sizes. That is fine when the inner collections are bounded and "
   "quietly catastrophic when one of them grows with data.",
   "An inner lookup written as a scan because the collection was small when the "
   "code was written.",
   "Check what each level iterates over and which of them can grow. The usual fix "
   "is to replace the innermost scan with a dictionary or set built once outside "
   "the loops."),

 # ---------------------- ALGORITHMS AND DATA STRUCTURES ------------
 CodeRule("ALGO-LINEARSCAN", ALGO, "medium", (),
   r'\b(if|while|elif|assert)\b[^:\n{]{0,80}?\b(not\s+)?in\s+\w*'
   r'(list|List|arr|array|rows|results|_l)\b|'
   r'\.indexOf\s*\(|\.includes\s*\(|\.index\s*\(',
   "Membership testing against a list or array is a linear scan. Inside a loop "
   "that makes the whole operation quadratic, which is the most common accidental "
   "O(n²) in ordinary application code: no algorithm was chosen, a data structure "
   "was.",
   "A list was the obvious container when the code was written, and membership "
   "testing was added later without revisiting the choice.",
   "Build a set or dictionary once before the loop and test against that. "
   "Membership goes from linear to constant, and the change is usually one line."),

 CodeRule("ALGO-SORTLOOP", ALGO, "medium", (),
   r'\.sort\s*\(|sorted\s*\(|\.OrderBy\s*\(|sort\.\w+\s*\(',
   "Sorting inside a loop repeats an n log n operation on data that has usually "
   "not changed, or has changed in a way that could be maintained incrementally. "
   "The total cost is a factor of n above what the work requires.",
   "Needing ordered data at a point inside the loop, with the sort placed where "
   "the need appears rather than where the data is produced.",
   "Sort once before the loop. If the collection genuinely changes each "
   "iteration, a heap or a sorted container maintains order at log n per "
   "insertion instead of n log n per pass.",
   in_loop=True),

 CodeRule("ALGO-MUTATEITER", ALGO, "low", (),
   r'\.remove\s*\(|\.splice\s*\(',
   "Removing from a collection while iterating over it is both a correctness "
   "problem and a performance one. Elements get skipped or the iterator throws, "
   "and each removal from an array shifts everything after it, so removing k "
   "items costs k×n.",
   "Filtering in place because it seems to avoid an allocation.",
   "Build a new collection with the items to keep, or collect the items to "
   "remove and apply the removals afterwards. Both are clearer and usually "
   "faster than removing during iteration.",
   in_loop=True),

 # ------------------------------- SCALABILITY ----------------------
 CodeRule("SCL-INMEMSTATE", SCL, "medium", (),
   r'^(_?[A-Za-z]\w*)\s*(?::\s*[\w\[\], ]+)?\s*=\s*(\{\}|\[\]|dict\(\)|list\(\)|'
   r'new\s+(Map|Set|Array)\s*\(\s*\)|make\(map\[)|'
   r'^(var|let|const)\s+\w+\s*=\s*(\{\}|\[\]|new\s+(Map|Set)\s*\(\s*\))',
   "Module-level mutable state lives in one process. The moment a second instance "
   "runs — a second worker, a second pod, a rolling deploy — each has its own "
   "copy, and behaviour depends on which one served the request. It is also "
   "shared between concurrent requests within the process, which makes it a "
   "correctness problem before it is a scaling one.",
   "A cache, a registry, or a counter that was correct when the service ran as a "
   "single process, and was never revisited when it did not.",
   "Decide whether the state is per-request, per-process, or global. Per-request "
   "belongs in the request context; global belongs in a shared store such as "
   "Redis or the database; per-process caches need an explicit bound and must "
   "tolerate being cold."),

 CodeRule("SCL-SLEEPPOLL", SCL, "low", (),
   r'time\.sleep\s*\(|Thread\.sleep\s*\(|setTimeout\s*\([^,]+,\s*\d{3,}|'
   r'time\.Sleep\s*\(|usleep\s*\(',
   "Polling with a sleep sets a floor on latency and a ceiling on throughput at "
   "the same time: work waits for the next tick, and every waiter costs a thread "
   "or a connection while it sleeps. Under load the sleeps do not amortise, they "
   "accumulate.",
   "Waiting for something to become ready, where a notification mechanism did not "
   "exist or seemed heavier than a loop.",
   "Use the blocking primitive the library already provides: a queue, a condition "
   "variable, a notification channel, or a webhook. Where polling is genuinely "
   "required, back off exponentially and cap the wait.",
   in_loop=True),

 CodeRule("SCL-UNBOUNDEDREAD", SCL, "low", (),
   r'\.readlines\s*\(\s*\)|\.read\s*\(\s*\)|\.fetchall\s*\(\s*\)|\.findAll\s*\(\s*\)|'
   r'ReadAll\s*\(|\.ToList\s*\(\s*\)\s*;|\.collect\s*\(\s*\)',
   "Reading an entire file, result set, or response into memory works until the "
   "input grows. The failure mode is not gradual: it is a process killed for "
   "memory, usually in production, usually on the largest customer.",
   "The input was small and bounded when the code was written, and often still "
   "is in every test fixture.",
   "Stream instead: iterate the file line by line, page the query, or process the "
   "response incrementally. Where loading it all is genuinely required, enforce an "
   "explicit limit and fail clearly when it is exceeded rather than by exhaustion."),

 # ----------------------------- MAINTAINABILITY --------------------
 CodeRule("MNT-SWALLOW", MNT, "medium", (),
   r'except\s*:\s*\n\s*pass|except\s+\w+\s*:\s*\n\s*pass|catch\s*\([^)]*\)\s*\{\s*\}|'
   r'catch\s*\{\s*\}|rescue\s*\n\s*end|if\s+err\s*!=\s*nil\s*\{\s*\}',
   "An empty handler converts a failure into a silent wrong answer. The program "
   "continues in a state its author did not anticipate, and the eventual symptom "
   "appears somewhere unrelated with no trace of the original cause. Debugging "
   "time for these is measured in days.",
   "A failure that was noisy and not understood, silenced to get on with the "
   "work, and never revisited.",
   "Handle it, or log it with enough context to identify the case, or let it "
   "propagate. If it is genuinely expected and safe, catch the specific exception "
   "type and write a comment saying why nothing needs to happen."),

 CodeRule("MNT-TODO", MNT, "low", (),
   r'\b(TODO|FIXME|HACK|XXX|BUG|REFACTOR|WORKAROUND)\b',
   "Markers left in code are notes to a future reader who has no way to know "
   "whether they are current. In small numbers they are useful; in large numbers "
   "they become noise that trains everyone to stop reading them, at which point "
   "the genuinely urgent ones are invisible.",
   "The honest reflex of flagging something rather than silently leaving it. The "
   "problem is accumulation, not the individual note.",
   "Move anything real into the issue tracker where it can be prioritised and "
   "closed, and delete the rest. A marker with no owner and no date is not a plan."),
]



# ---------------------------------------------------------------------
# Code analysis engine
# ---------------------------------------------------------------------

@dataclass
class Hit:
    rule: str
    file: str
    line: int
    text: str


def loop_spans(text: str, masked: str, lang: Lang):
    """Character ranges covered by loop bodies. Braces for C-family languages,
    indentation for the rest. Approximate by construction: a rule that needs a
    loop is asking 'is this repeated', and getting the extent slightly wrong
    changes which line is reported, not whether the pattern exists."""
    spans = []
    if lang.name in ("Python", "Ruby"):
        lines = masked.split("\n")
        offs, acc = [], 0
        for ln in lines:
            offs.append(acc); acc += len(ln) + 1
        for i, ln in enumerate(lines):
            m = re.match(r'(\s*)(for|while)\b', ln)
            if not m:
                continue
            indent = len(m.group(1))
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                j += 1
            spans.append((offs[i], offs[j - 1] + len(lines[j - 1]) if j > i else offs[i]))
        return spans

    for m in re.finditer(r'\b(for|while|foreach)\s*[\(:]', masked):
        i = masked.find("{", m.end() - 1)
        if i < 0 or i - m.end() > 400:
            continue
        d, j = 0, i
        while j < len(masked):
            if masked[j] == "{":
                d += 1
            elif masked[j] == "}":
                d -= 1
                if d == 0:
                    break
            j += 1
        spans.append((m.start(), min(j + 1, len(masked))))
    return spans


def in_any(pos, spans):
    return any(a <= pos < b for a, b in spans)


def max_loop_nesting(spans):
    """Deepest overlap among loop bodies."""
    best = 0
    for a, b in spans:
        best = max(best, sum(1 for x, y in spans if x <= a and b <= y))
    return best


def python_metrics(text, rel):
    """Exact, from the grammar. Only Python gets this; everything else is
    matched lexically and labelled as such."""
    out, hits = [], []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out, hits

    BRANCH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
              ast.With, ast.AsyncWith, ast.Assert, ast.IfExp, ast.comprehension)

    def depth(node, d=0):
        best = d
        for ch in ast.iter_child_nodes(node):
            nd = d + 1 if isinstance(ch, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                          ast.Try, ast.With, ast.AsyncWith)) else d
            best = max(best, depth(ch, nd))
        return best

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cc = 1
            for sub in ast.walk(node):
                if isinstance(sub, BRANCH):
                    cc += 1
                elif isinstance(sub, ast.BoolOp):
                    cc += len(sub.values) - 1
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            args = node.args
            nparams = (len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                       + (1 if args.vararg else 0) + (1 if args.kwarg else 0))
            out.append({"file": rel, "name": node.name, "line": node.lineno,
                        "cc": cc, "loc": end - node.lineno + 1,
                        "params": nparams, "nesting": depth(node)})
            for d in args.defaults + [d for d in args.kw_defaults if d]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set, ast.Call)):
                    hits.append(Hit("MNT-MUTDEFAULT", rel, node.lineno,
                                    f"def {node.name}(...)"))
    return out, hits


def scan_code(root: Path, cfg: dict, modules):
    """One lexical pass per file, plus an exact pass for Python."""
    hits, funcs, stats = [], [], defaultdict(int)
    compiled = [(r, re.compile(r.pattern, r.flags)) for r in CODE_RULES if r.pattern]
    clone_index = defaultdict(list)

    for m in modules.values():
        path = root / m.path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lang = BY_EXT.get(path.suffix)
        if not lang:
            continue
        masked = mask_comments(text, lang.comments)          # comments blanked, strings kept
        spans = loop_spans(text, masked, lang)
        stats[lang.name] += 1

        nest = max_loop_nesting(spans)
        if nest >= 3:
            hits.append(Hit("PERF-NESTEDLOOP", m.path,
                            line_of(text, spans[0][0]) if spans else 1,
                            f"{nest} levels of loop nesting"))

        for rule, rx in compiled:
            if rule.langs and lang.name not in rule.langs:
                continue
            body = text if rule.id in ("MNT-TODO",) else masked
            for mt in rx.finditer(body):
                if rule.in_loop and not in_any(mt.start(), spans):
                    continue
                hits.append(Hit(rule.id, m.path, line_of(body, mt.start()),
                                mt.group(0)[:60].strip().replace("\n", " ")))

        if lang.name == "Python":
            f, h = python_metrics(text, m.path)
            funcs.extend(f); hits.extend(h)

        # normalised 6-line windows for clone detection
        norm = [re.sub(r'\s+', ' ', ln.strip()) for ln in masked.split("\n")]
        norm = [re.sub(r'\b\d+\b', '0', ln) for ln in norm]
        meaningful = [(i, ln) for i, ln in enumerate(norm, 1) if len(ln) > 20]
        for k in range(len(meaningful) - 5):
            window = meaningful[k:k + 6]
            if window[-1][0] - window[0][0] > 12:
                continue
            key = hash("\n".join(w[1] for w in window))
            clone_index[key].append((m.path, window[0][0]))

    # collapse overlapping windows: report distinct file pairs, with the
    # earliest line in each, so one duplicated block counts once
    pairs = defaultdict(list)
    for v in clone_index.values():
        files = sorted({p_ for p_, _ in v})
        # a block present in many files is boilerplate — a header, a licence, a
        # framework idiom — not duplication anyone should extract
        if not 2 <= len(files) <= 5:
            continue
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                key = (files[i], files[j])
                first = min(ln for p_, ln in v if p_ in key)
                pairs[key].append(first)
    clones = sorted(((len(v), k[0], k[1], min(v)) for k, v in pairs.items()
                     if len(v) >= 2), reverse=True)
    return hits, funcs, clones, dict(stats)


def code_findings(hits, funcs, clones, cfg, modules):
    """Aggregate raw hits into findings that carry the catalog's prose."""
    T = dict(THRESHOLDS); T.update(cfg.get("thresholds", {}))
    off = set(cfg.get("suppress", []))
    by_rule = defaultdict(list)
    for h in hits:
        by_rule[h.rule].append(h)

    F = []
    for rule in CODE_RULES:
        hs = by_rule.get(rule.id, [])
        if not hs or rule.id in off:
            continue
        files = len({h.file for h in hs})
        F.append(F_(rule.id, rule.category, rule.severity,
                    f"{len(hs)} occurrence(s) across {files} file(s).",
                    rule.why, rule.cause, rule.action,
                    [f"`{h.file}:{h.line}` — `{h.text}`" for h in hs[:6]]))

    if funcs and "MNT-COMPLEX" not in off:
        bad = sorted((f for f in funcs if f["cc"] >= T["max_complexity"]),
                     key=lambda f: -f["cc"])
        if bad:
            F.append(F_("MNT-COMPLEX", MNT, "medium",
                        f"{len(bad)} of {len(funcs)} Python functions "
                        f"({len(bad)/len(funcs):.0%}) have a cyclomatic complexity "
                        f"of {T['max_complexity']} or more; the highest is "
                        f"{bad[0]['cc']}.",
                        "Complexity counts the independent paths through a function, "
                        "which is also the number of test cases needed to cover it "
                        "and the number of cases a reader must hold at once. Past "
                        "about ten, reviewers stop simulating the function and start "
                        "trusting it, which is where defects survive review.",
                        "Requirements added one branch at a time. No single change "
                        "made the function complex.",
                        "Extract the branches that belong together into named "
                        "functions; the names are usually already in the comments or "
                        "the variable names. Guard clauses that return early remove "
                        "nesting without moving logic.",
                        [f"`{f['file']}:{f['line']}` — `{f['name']}` complexity "
                         f"{f['cc']}, {f['loc']} lines, nesting {f['nesting']}"
                         for f in bad[:8]]))

        longf = sorted((f for f in funcs if f["loc"] >= T["max_func_lines"]),
                       key=lambda f: -f["loc"])
        if longf and "MNT-LONGFUNC" not in off:
            F.append(F_("MNT-LONGFUNC", MNT, "low",
                        f"{len(longf)} of {len(funcs)} Python functions "
                        f"({len(longf)/len(funcs):.0%}) are "
                        f"{T['max_func_lines']} lines or longer; the longest is "
                        f"{longf[0]['loc']}.",
                        "Length is a proxy for how much has to be understood before "
                        "any part can be changed. A function that does not fit on a "
                        "screen cannot be checked against its own beginning, and "
                        "long functions accumulate local variables whose lifetimes "
                        "overlap in ways nothing enforces.",
                        "Sequential steps written where they occur, each addition "
                        "smaller than the threshold for extracting it.",
                        "Extract the steps that operate on a distinct set of locals. "
                        "If the extracted function needs six parameters, that group "
                        "of values is a type worth naming.",
                        [f"`{f['file']}:{f['line']}` — `{f['name']}`, {f['loc']} lines"
                         for f in longf[:6]]))

        deep = sorted((f for f in funcs if f["nesting"] >= T["max_nesting"]),
                      key=lambda f: -f["nesting"])
        if deep and "RDB-NESTING" not in off:
            F.append(F_("RDB-NESTING", RDB, "medium",
                        f"{len(deep)} of {len(funcs)} Python functions "
                        f"({len(deep)/len(funcs):.0%}) nest control flow "
                        f"{T['max_nesting']} levels or deeper.",
                        "Each level of nesting is a condition the reader must keep "
                        "true in their head for everything inside it. Depth "
                        "compounds: at four levels the reader is tracking four "
                        "simultaneous invariants to understand one line. Nesting "
                        "correlates with defects more strongly than length does.",
                        "Conditions added around existing code rather than in front "
                        "of it, because wrapping is a smaller diff than restructuring.",
                        "Invert the conditions and return early, so the exceptional "
                        "cases leave at the top and the main path stays at one "
                        "level. Extracting the innermost block into its own function "
                        "achieves the same and gives the block a name.",
                        [f"`{f['file']}:{f['line']}` — `{f['name']}`, depth "
                         f"{f['nesting']}" for f in deep[:6]]))

        many = sorted((f for f in funcs if f["params"] >= T["max_params"]),
                      key=lambda f: -f["params"])
        if many and "MNT-PARAMS" not in off:
            F.append(F_("MNT-PARAMS", MNT, "low",
                        f"{len(many)} of {len(funcs)} Python functions "
                        f"({len(many)/len(funcs):.0%}) take {T['max_params']} or "
                        f"more parameters; the largest takes {many[0]['params']}.",
                        "A long parameter list is usually several values that "
                        "travel together and have no name. Callers must remember an "
                        "order, positional mistakes between same-typed parameters "
                        "type-check silently, and every new requirement adds another.",
                        "Passing context down through layers, one value at a time as "
                        "each became necessary.",
                        "Group the parameters that always appear together into a "
                        "dataclass or record. The name of that group is usually a "
                        "concept the codebase was missing.",
                        [f"`{f['file']}:{f['line']}` — `{f['name']}`, "
                         f"{f['params']} parameters" for f in many[:6]]))

    if clones and "MNT-CLONE" not in off:
        cross = [c for c in clones if c[0] >= 2]
        if len(cross) >= T["clone_groups"]:
            F.append(F_("MNT-CLONE", MNT, "medium",
                        f"{len(cross)} file pair(s) share blocks of six or more "
                        f"similar lines.",
                        "Duplicated logic means a fix applied in one place and not "
                        "the others. The copies drift apart silently, and the "
                        "difference between 'intentionally different' and 'not yet "
                        "updated' becomes unrecoverable. Comparison ignores "
                        "whitespace and numeric literals, so near-copies count.",
                        "Copying a working block was faster than finding the right "
                        "shared home for it, which it usually is at the moment of "
                        "writing.",
                        "Before extracting, check whether the copies have already "
                        "diverged; if they have, the differences are requirements "
                        "nobody wrote down. Extract only where the copies should "
                        "genuinely change together.",
                        [f"`{a}` ≡ `{b}` — {n} matching blocks, from line {ln}"
                         for n, a, b, ln in cross[:6]]))
    return F



def render_code_findings(F, stats, full=False):
    L = []; w = L.append
    w("## Inside the files\n")
    w("The section above reasons about the import graph, where an edge either "
      "exists or does not. This one reads inside files, and its evidence is "
      "weaker by construction. Python is analysed with its real grammar, so "
      "complexity, nesting, length and parameter counts are exact. Every other "
      "language is matched lexically against comment-stripped source: those rules "
      "report **the presence of a construct, not a proven defect**. There is no "
      "dataflow analysis here. A flagged line may be perfectly correct in "
      "context, and an unflagged file may still be wrong. Read these as places to "
      "look, not as a verdict.\n")
    if not F:
        w("No code rule fired.\n")
        return "\n".join(L)

    by_cat = defaultdict(list)
    for f in F:
        by_cat[f.category].append(f)
    w("| category | findings |")
    w("|---|---:|")
    for cat in CODE_CATEGORIES:
        if by_cat[cat]:
            w(f"| {cat} | {len(by_cat[cat])} |")
    w("")

    label = {"high": "Serious", "medium": "Worth attention", "low": "Minor", "info": "Note"}
    for cat in CODE_CATEGORIES:
        items = sorted(by_cat[cat], key=lambda f: SEV_ORDER[f.severity])
        if not items:
            continue
        w(f"### {cat}\n")
        for f in (items if full else items[:6]):
            w(f"**{label[f.severity]} · {f.rule}** — {f.headline}\n")
            w(f"*Why it matters.* {f.why}\n")
            w(f"*What usually causes it.* {f.cause}\n")
            w(f"*What to do.* {f.action}\n")
            if f.evidence:
                w("<details><summary>Evidence</summary>\n")
                for e in f.evidence:
                    w(f"- {e}")
                w("\n</details>\n")
        if not full and len(items) > 6:
            w(f"_{len(items)-6} more in this category; `--full` shows them._\n")
    return "\n".join(L)



# =====================================================================
# 14. TYPE-LEVEL EXTRACTION
#
#   Imports say which files depend on which. They do not say what the
#   nouns of the system are, which of them contains which, or which
#   inherits from which. That is a separate extraction over declarations.
#
#   Python is read with ast, so its classes, bases, annotated fields and
#   methods are exact. Other languages are matched on declaration syntax,
#   which is reliable for the declaration itself and weaker for members.
#   Relationships are only drawn between types this tool actually found:
#   a field whose type is defined elsewhere is left out rather than guessed.
# =====================================================================

@dataclass
class TypeDecl:
    name: str
    kind: str                 # class | interface | struct | enum | trait | abstract
    module: str
    file: str
    line: int
    lang: str
    bases: list = field(default_factory=list)
    fields: list = field(default_factory=list)    # (name, type_string)
    methods: list = field(default_factory=list)   # (name, arity)
    exact: bool = False


TYPE_PATTERNS = {
    "TypeScript": [
        (r'^\s*(?:export\s+)?(?:declare\s+)?(?:(abstract)\s+)?class\s+(\w+)'
         r'(?:\s*<[^>]*>)?(?:\s+extends\s+([\w.]+))?'
         r'(?:\s*(?:<[^>]*>)?\s+implements\s+([\w.,\s<>]+?))?\s*\{', "class"),
        (r'^\s*(?:export\s+)?interface\s+(\w+)(?:\s*<[^>]*>)?'
         r'(?:\s+extends\s+([\w.,\s<>]+?))?\s*\{', "interface"),
        (r'^\s*(?:export\s+)?enum\s+(\w+)\s*\{', "enum"),
    ],
    "Java": [
        (r'^\s*(?:public\s+|final\s+|sealed\s+)*(?:(abstract)\s+)?class\s+(\w+)'
         r'(?:\s*<[^>]*>)?(?:\s+extends\s+([\w.]+))?'
         r'(?:\s+implements\s+([\w.,\s<>]+?))?\s*\{', "class"),
        (r'^\s*(?:public\s+)?interface\s+(\w+)(?:\s*<[^>]*>)?'
         r'(?:\s+extends\s+([\w.,\s<>]+?))?\s*\{', "interface"),
        (r'^\s*(?:public\s+)?(?:enum|record)\s+(\w+)', "enum"),
    ],
    "C#": [
        (r'^\s*(?:public\s+|internal\s+|sealed\s+|partial\s+)*(?:(abstract)\s+)?'
         r'class\s+(\w+)(?:\s*<[^>]*>)?(?:\s*:\s*([\w.,\s<>]+?))?\s*\{', "class"),
        (r'^\s*(?:public\s+|internal\s+)?interface\s+(\w+)'
         r'(?:\s*:\s*([\w.,\s<>]+?))?\s*\{', "interface"),
    ],
    "Kotlin": [
        (r'^\s*(?:(abstract|sealed|open|data)\s+)?class\s+(\w+)[^{:\n]*'
         r'(?:\s*:\s*([\w.,\s<>()]+?))?\s*[\{\n]', "class"),
        (r'^\s*interface\s+(\w+)(?:\s*:\s*([\w.,\s<>]+?))?\s*\{', "interface"),
    ],
}


def brace_body(text, start):
    i = text.find("{", start)
    if i < 0:
        return ""
    d, j = 0, i
    while j < len(text):
        if text[j] == "{":
            d += 1
        elif text[j] == "}":
            d -= 1
            if d == 0:
                return text[i + 1:j]
        j += 1
    return text[i + 1:]


def base_names(raw):
    if not raw:
        return []
    out = []
    for part in re.split(r'[,\s]+', re.sub(r'<[^>]*>', '', raw)):
        part = part.strip().strip("()").split(".")[-1]
        if part and part[0].isupper():
            out.append(part)
    return out


def root_type(ann):
    """Strip containers and nullability down to the named type."""
    if not ann:
        return ""
    a = re.sub(r'\b(Optional|List|Sequence|Iterable|Set|FrozenSet|Dict|Mapping|'
               r'Array|Vec|Option|Box|Rc|Arc|IEnumerable|ICollection|IList)\b', '', ann)
    a = a.replace("[]", "")
    parts = re.findall(r'\b([A-Z]\w*)\b', a)
    return parts[-1] if parts else ""


def types_python(text, mod, rel):
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(b.attr)
        decos = {d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
                 for d in node.decorator_list if not isinstance(d, ast.Call)}
        kind = "enum" if any("Enum" in b for b in bases) else \
               "interface" if any(b in ("Protocol", "ABC") for b in bases) else "class"
        t = TypeDecl(node.name, kind, mod, rel, node.lineno, "Python", bases, exact=True)
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                t.fields.append((item.target.id, ast.unparse(item.annotation)))
            elif isinstance(item, ast.Assign):
                for tgt in item.targets:
                    if isinstance(tgt, ast.Name):
                        t.fields.append((tgt.id, ""))
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("__") and item.name != "__init__":
                    continue
                t.methods.append((item.name, len(item.args.args) - 1))
                if item.name == "__init__":
                    for sub in ast.walk(item):
                        if (isinstance(sub, ast.AnnAssign)
                                and isinstance(sub.target, ast.Attribute)
                                and isinstance(sub.target.value, ast.Name)
                                and sub.target.value.id == "self"):
                            t.fields.append((sub.target.attr, ast.unparse(sub.annotation)))
                    for a in item.args.args[1:]:
                        if a.annotation is not None:
                            t.fields.append((a.arg, ast.unparse(a.annotation)))
        seen, ded = set(), []
        for n, ty in t.fields:
            if n not in seen:
                seen.add(n); ded.append((n, ty))
        t.fields = ded
        out.append(t)
    return out


def types_lexical(text, masked, mod, rel, lang):
    out = []
    pats = TYPE_PATTERNS.get(lang.name)

    if lang.name == "Go":
        for m in re.finditer(r'^\s*type\s+(\w+)\s+struct\s*\{', masked, re.M):
            t = TypeDecl(m.group(1), "struct", mod, rel, line_of(text, m.start()), "Go")
            for ln in brace_body(masked, m.start()).split("\n"):
                ln = ln.strip()
                if not ln or ln.startswith("//"):
                    continue
                fm = re.match(r'(\w+)\s+([\w\*\[\]\.]+)', ln)
                if fm:
                    t.fields.append((fm.group(1), fm.group(2)))
                elif re.match(r'^[\w\*\.]+$', ln):
                    t.bases.append(ln.lstrip("*").split(".")[-1])   # embedding
            out.append(t)
        for m in re.finditer(r'^\s*type\s+(\w+)\s+interface\s*\{', masked, re.M):
            out.append(TypeDecl(m.group(1), "interface", mod, rel,
                                line_of(text, m.start()), "Go"))
        by_name = {t.name: t for t in out}
        for m in re.finditer(r'^\s*func\s*\(\s*\w+\s+\*?(\w+)\s*\)\s*(\w+)\s*\(([^)]*)\)',
                             masked, re.M):
            if m.group(1) in by_name:
                args = [a for a in m.group(3).split(",") if a.strip()]
                by_name[m.group(1)].methods.append((m.group(2), len(args)))
        return out

    if lang.name == "Rust":
        for m in re.finditer(r'^\s*(?:pub\s+)?struct\s+(\w+)', masked, re.M):
            t = TypeDecl(m.group(1), "struct", mod, rel, line_of(text, m.start()), "Rust")
            for fm in re.finditer(r'(?:pub\s+)?(\w+)\s*:\s*([\w:<>&\' ]+)',
                                  brace_body(masked, m.start())):
                t.fields.append((fm.group(1), fm.group(2).strip()))
            out.append(t)
        for m in re.finditer(r'^\s*(?:pub\s+)?trait\s+(\w+)', masked, re.M):
            out.append(TypeDecl(m.group(1), "trait", mod, rel,
                                line_of(text, m.start()), "Rust"))
        for m in re.finditer(r'^\s*(?:pub\s+)?enum\s+(\w+)', masked, re.M):
            out.append(TypeDecl(m.group(1), "enum", mod, rel,
                                line_of(text, m.start()), "Rust"))
        by_name = {t.name: t for t in out}
        for m in re.finditer(r'^\s*impl(?:<[^>]*>)?\s+(?:(\w+)\s+for\s+)?(\w+)', masked, re.M):
            trait, target = m.group(1), m.group(2)
            if target in by_name:
                if trait:
                    by_name[target].bases.append(trait)
                for fm in re.finditer(r'(?:pub\s+)?fn\s+(\w+)\s*\(([^)]*)\)',
                                      brace_body(masked, m.start())):
                    args = [a for a in fm.group(2).split(",") if a.strip()]
                    by_name[target].methods.append((fm.group(1), max(0, len(args) - 1)))
        return out

    if not pats:
        return out

    for pat, kind in pats:
        for m in re.finditer(pat, masked, re.M):
            g = list(m.groups())
            if kind == "class" and len(g) >= 3:
                abstract, name = g[0], g[1]
                bases = base_names(g[2] or "") + base_names(g[3] if len(g) > 3 else "")
                k = "abstract" if abstract in ("abstract", "sealed") else "class"
            else:
                name, bases, k = g[0], base_names(g[1] if len(g) > 1 else ""), kind
            t = TypeDecl(name, k, mod, rel, line_of(text, m.start()), lang.name, bases)
            body = brace_body(masked, m.start())
            for fm in re.finditer(
                    r'^\s*(?:public|private|protected|readonly|internal|val|var|'
                    r'static|final)?[\s]*(\w+)\s*[?!]?\s*:\s*([\w<>\[\].,| ]+)', body, re.M):
                t.fields.append((fm.group(1), fm.group(2).strip()))
            for fm in re.finditer(
                    r'^\s*(?:public|private|protected|internal|static|final)\s+'
                    r'([\w<>\[\].]+)\s+(\w+)\s*;', body, re.M):
                t.fields.append((fm.group(2), fm.group(1)))
            for fm in re.finditer(r'^\s*(?:public\s+|private\s+|async\s+|fun\s+)*'
                                  r'(\w+)\s*\(([^)]*)\)\s*[:{]', body, re.M):
                if fm.group(1) in ("if", "for", "while", "switch", "catch", "return"):
                    continue
                args = [a for a in fm.group(2).split(",") if a.strip()]
                t.methods.append((fm.group(1), len(args)))
            out.append(t)
    return out


def extract_types(root: Path, modules):
    decls = []
    for m in modules.values():
        path = root / m.path
        lang = BY_EXT.get(path.suffix)
        if not lang:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if lang.name == "Python":
            decls.extend(types_python(text, m.name, m.path))
        else:
            decls.extend(types_lexical(text, mask_comments(text, lang.comments),
                                       m.name, m.path, lang))
    return sorted(decls, key=lambda t: (t.module, t.name))


def type_relations(decls):
    """Only between types actually found. A field whose type is defined
    elsewhere is omitted rather than invented."""
    known = {}
    for t in decls:
        known.setdefault(t.name, t)
    inherit, compose = [], []
    for t in decls:
        for b in t.bases:
            if b in known and b != t.name:
                inherit.append((t.name, b))
        for fname, ftype in t.fields:
            r = root_type(ftype)
            if r and r in known and r != t.name:
                compose.append((t.name, r, fname))
    return known, sorted(set(inherit)), sorted(set(compose))



def class_diagram(decls, known, inherit, compose, focus=None, limit=12):
    """Mermaid classDiagram. Members are trimmed to what fits: a diagram
    listing forty fields is a data dump, not a diagram."""
    degree = defaultdict(int)
    for a, b in inherit:
        degree[a] += 2; degree[b] += 2
    for a, b, _ in compose:
        degree[a] += 1; degree[b] += 1
    pool = sorted(decls, key=lambda t: (-degree[t.name], t.name))[:limit]
    names = {t.name for t in pool}
    if not names:
        return ""
    # bring in types from other components that these ones inherit from or
    # contain, so a relationship crossing a boundary is still visible
    extra = {}
    for a, b in inherit:
        if a in names and b not in names and b in known:
            extra[b] = known[b]
    for a, b, _ in compose:
        if a in names and b not in names and b in known:
            extra[b] = known[b]
    context = set(extra)
    pool = pool + sorted(extra.values(), key=lambda t: t.name)
    names |= context

    SYM = {"class": "", "interface": "<<interface>>", "abstract": "<<abstract>>",
           "struct": "<<struct>>", "enum": "<<enumeration>>", "trait": "<<trait>>"}
    out = ["```mermaid", "classDiagram"]
    for t in sorted(pool, key=lambda x: x.name):
        out.append(f"  class {t.name} {{")
        if t.name in context:
            out.append(f"    <<{t.module}>>")
            out.append("  }")
            continue
        if SYM.get(t.kind):
            out.append(f"    {SYM[t.kind]}")
        for fname, ftype in t.fields[:6]:
            ft = re.sub(r'\s+', '', ftype)[:24]
            out.append(f"    +{fname}{': ' + ft if ft else ''}")
        if len(t.fields) > 6:
            out.append(f"    +… {len(t.fields) - 6} more fields")
        for mname, arity in t.methods[:5]:
            out.append(f"    +{mname}({arity})")
        if len(t.methods) > 5:
            out.append(f"    +… {len(t.methods) - 5} more methods")
        out.append("  }")
    for a, b in inherit:
        if a in names and b in names:
            out.append(f"  {b} <|-- {a}")
    seen = set()
    for a, b, fname in compose:
        if a in names and b in names and (a, b) not in seen:
            seen.add((a, b))
            out.append(f"  {a} *-- {b} : {fname}")
    out.append("```")
    return "\n".join(out)


def render_types(decls, known, inherit, compose, comp_mods, depth, full=False):
    L = []; w = L.append
    w("## The nouns\n")
    if not decls:
        w("No type declarations found.\n")
        return "\n".join(L)
    exact = sum(1 for t in decls if t.exact)
    w(f"{len(decls)} types declared: {len(inherit)} inheritance and {len(compose)} "
      f"composition relationships between types defined in this tree. Relationships "
      f"to types declared elsewhere are omitted rather than guessed, so this is a "
      f"lower bound. {exact} types were read with a real parser; the rest come from "
      f"declaration syntax, which is reliable for the declaration and weaker for "
      f"the member lists.\n")

    by_comp = defaultdict(list)
    for t in decls:
        by_comp[component_of(t.module, depth)].append(t)
    interesting = sorted(by_comp.items(),
                         key=lambda kv: -sum(len(t.fields) + len(t.methods) + 3 * len(t.bases)
                                             for t in kv[1]))
    for comp, ts in (interesting if full else interesting[:3]):
        rel_here = any(t.name in {a for a, _ in inherit} | {a for a, _, _ in compose}
                       or t.name in {b for _, b in inherit} | {b for _, b, _ in compose}
                       for t in ts)
        if len(ts) < 2 and not rel_here:
            continue
        d = class_diagram(ts, known, inherit, compose)
        if not d:
            continue
        w(f"### `{comp}`\n")
        w(d)
        w("")
    if not full and len(interesting) > 3:
        w(f"_{len(interesting) - 3} further component(s) with types; `--full` "
          f"draws them._\n")

    roots = {b for _, b in inherit}
    depth_of = {}

    def chain(n, seen=()):
        if n in seen:
            return 0
        best = 0
        for a, b in inherit:
            if a == n:
                best = max(best, 1 + chain(b, seen + (n,)))
        return best

    for t in decls:
        depth_of[t.name] = chain(t.name)
    deep = sorted(((d, n) for n, d in depth_of.items() if d >= 3), reverse=True)
    if deep:
        w("**Inheritance depth.** " + ", ".join(f"`{n}` ({d} levels)" for d, n in deep[:6])
          + ". Each level is another file a reader must open to find where a method "
            "is actually defined, and another place a change can come from.\n")

    unimpl = sorted(t.name for t in decls
                    if t.kind in ("interface", "trait", "abstract")
                    and t.name not in roots)
    if unimpl:
        w("**Declared but never implemented in this tree:** "
          + ", ".join(f"`{n}`" for n in unimpl[:8])
          + ". Either the implementations live outside this tree, or the abstraction "
            "has no second case yet and the indirection is not paying for itself.\n")
    return "\n".join(L)



# =====================================================================
# 15. ENTRY POINTS AND NAVIGATION
#
#   A user journey is what people actually did, in what order, and where
#   they gave up. That lives in telemetry, not in source, and this tool
#   does not have it. What source does contain is the set of journeys the
#   system PERMITS: every way in, every navigation edge between screens,
#   and which machinery each way in reaches.
#
#   So this section answers "what can a user do and what does it touch",
#   not "what do users do". The difference matters: a path drawn here may
#   be dead in practice, and a path users take constantly looks identical
#   to one nobody has ever used.
# =====================================================================

@dataclass
class Entry:
    kind: str          # http | cli | job | event | page
    verb: str
    path: str
    module: str
    file: str
    line: int
    framework: str


ROUTE_PATTERNS = [
    ("http", "Flask/FastAPI",
     r'@\w+\.(get|post|put|patch|delete|route)\s*\(\s*[\'"]([^\'"]+)[\'"]'),
    ("http", "Django",
     r'\b(?:path|re_path|url)\s*\(\s*r?[\'"]([^\'"]*)[\'"]\s*,'),
    ("http", "Express",
     r'\b(?:app|router|api)\.(get|post|put|patch|delete|all)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]'),
    ("http", "NestJS",
     r'@(Get|Post|Put|Patch|Delete)\s*\(\s*[\'"]([^\'"]*)[\'"]?\s*\)'),
    ("http", "Spring",
     r'@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?[\'"]([^\'"]+)[\'"]'),
    ("http", "ASP.NET",
     r'\[Http(Get|Post|Put|Patch|Delete)\s*\(\s*"([^"]*)"\s*\)\]|\[Route\s*\(\s*"([^"]+)"\s*\)\]'),
    ("http", "Go net/http",
     r'\.(HandleFunc|Handle|GET|POST|PUT|DELETE|Get|Post)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]'),
    ("http", "Rails",
     r'^\s*(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]'),
    ("cli", "click/typer",
     r'@\w*\.?(command|group)\s*\(\s*(?:[\'"]([^\'"]*)[\'"])?'),
    ("cli", "argparse",
     r'add_parser\s*\(\s*[\'"]([^\'"]+)[\'"]'),
    ("job", "celery/scheduler",
     r'@(?:shared_task|task|periodic_task|scheduled|Scheduled|cron)\b\s*\(?\s*'
     r'(?:[\'"]([^\'"]*)[\'"])?'),
    ("event", "queue consumer",
     r'\.(subscribe|consume|on|addEventListener|listen)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]'),
]

NAV_PATTERNS = [
    r'<Link\s+[^>]*?(?:href|to)\s*=\s*[\'"{`]([^\'"}`]+)',
    r'\brouter\.(?:push|replace)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
    r'\bnavigate\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
    r'<a\s+[^>]*?href\s*=\s*[\'"]([/][^\'"#?]*)',
    r'\bredirect\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
    r'<Route\s+[^>]*?path\s*=\s*[\'"{`]([^\'"}`]+)',
    r'\bwindow\.location(?:\.href)?\s*=\s*[\'"`]([^\'"`]+)[\'"`]',
]

FILE_ROUTED = re.compile(r'(?:^|/)(?:pages|app|routes)/(.+?)(?:/(?:page|index|route))?'
                         r'\.(?:tsx?|jsx?|svelte|vue)$')


def file_route(rel: str):
    """Next.js, Nuxt, SvelteKit and friends put the URL in the path."""
    m = FILE_ROUTED.search(rel)
    if not m:
        return None
    seg = m.group(1)
    if seg.startswith("api/") or "_" in seg.split("/")[0][:1]:
        return None
    seg = re.sub(r'\[\.\.\.(\w+)\]', r'*', seg)
    seg = re.sub(r'\[(\w+)\]', r':\1', seg)
    return "/" + seg.strip("/")


def extract_entries(root: Path, modules):
    entries, nav = [], []
    for m in modules.values():
        path = root / m.path
        lang = BY_EXT.get(path.suffix)
        if not lang:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        masked = mask_comments(text, lang.comments)

        fr = file_route(m.path)
        if fr:
            entries.append(Entry("page", "VIEW", fr, m.name, m.path, 1, "file routing"))

        for kind, fw, pat in ROUTE_PATTERNS:
            for mt in re.finditer(pat, masked, re.M):
                groups = [g for g in mt.groups() if g is not None]
                if not groups:
                    continue
                if len(groups) >= 2:
                    verb, route = groups[0].upper(), groups[1]
                else:
                    verb, route = kind.upper(), groups[0]
                if kind == "http" and not (route.startswith("/") or route == ""):
                    if fw not in ("NestJS", "Rails", "Django"):
                        continue
                if len(route) > 120:
                    continue
                entries.append(Entry(kind, verb, route or "/", m.name, m.path,
                                     line_of(text, mt.start()), fw))

        for pat in NAV_PATTERNS:
            for mt in re.finditer(pat, masked):
                target = mt.group(1)
                if not target.startswith("/") or len(target) > 120:
                    continue
                nav.append((m.name, target, m.path, line_of(text, mt.start())))

    seen, ded = set(), []
    for e in entries:
        k = (e.kind, e.verb, e.path, e.file)
        if k not in seen:
            seen.add(k); ded.append(e)
    return sorted(ded, key=lambda e: (e.kind, e.path, e.verb)), sorted(set(nav))


def module_reach(edges, start, depth=4):
    out = defaultdict(set)
    for e in edges:
        out[e.src].add(e.dst)
    seen, frontier = {start}, {start}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt |= out.get(n, set()) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen - {start}


def journey_graph(entries, nav, modules, depth):
    """Screen-to-screen edges. A file that both defines a route and links
    somewhere gives an edge between two URLs; everything else is anchored on
    the module, which is honest but less useful."""
    screen_of = {}
    for e in entries:
        if e.kind in ("page", "http") and e.verb in ("VIEW", "GET"):
            screen_of.setdefault(e.module, e.path)
    edges = defaultdict(list)
    for mod, target, file, line in nav:
        src = screen_of.get(mod)
        if src and src != target:
            edges[(src, target)].append((file, line))
    return screen_of, dict(sorted(edges.items()))


def render_journeys(entries, nav, R, full=False):
    L = []; w = L.append
    depth = R["cfg"]["component_depth"]
    w("## Ways in, and where they lead\n")
    if not entries and not nav:
        w("No routes, commands, jobs, or navigation links were recognised. Either "
          "this tree has no entry points of its own, or its framework is not one "
          "this tool knows how to read.\n")
        return "\n".join(L)

    w("This is not a record of what users do. That lives in analytics, and no "
      "static tool can recover it: a route nobody has ever called looks exactly "
      "like the one every session hits. What follows is the set of journeys the "
      "code **permits** — every way in, every navigation edge between screens, "
      "and what each way in can reach.\n")

    by_kind = defaultdict(list)
    for e in entries:
        by_kind[e.kind].append(e)
    LABEL = {"http": "HTTP routes", "page": "Pages", "cli": "Commands",
             "job": "Scheduled jobs", "event": "Event and queue handlers"}
    w("| Kind | Count | Frameworks |")
    w("|---|---:|---|")
    for k in ("page", "http", "cli", "job", "event"):
        if by_kind[k]:
            fws = sorted({e.framework for e in by_kind[k]})
            w(f"| {LABEL[k]} | {len(by_kind[k])} | {', '.join(fws)} |")
    w("")

    screen_of, edges = journey_graph(entries, nav, R["modules"], depth)
    if edges:
        w("### Navigation between screens\n")
        out = ["```mermaid", "graph LR"]
        nodes = {n for pair in edges for n in pair}
        ids = {n: "s" + sanitize(n) for n in sorted(nodes)}
        reachable = {b for (a, b) in edges}
        for n in sorted(nodes):
            out.append(f'  {ids[n]}["{n}"]')
        for (a, b), sites in edges.items():
            lbl = f"|{len(sites)}|" if len(sites) > 1 else ""
            out.append(f"  {ids[a]} -->{lbl} {ids[b]}")
        for n in sorted(nodes):
            if n not in reachable:
                out.append(f"  style {ids[n]} stroke-width:3px")
        out.append("```")
        w("\n".join(out))
        w("\nThick borders mark screens nothing else links to: they are entered "
          "directly, by URL, by redirect, or not at all.\n")

    w("### What each way in reaches\n")
    w("Components a route can touch by following imports, to a depth of four. "
      "This is the blast radius of that endpoint, and the set of code a change "
      "to it can disturb.\n")
    w("| Entry | Handler | Components reached |")
    w("|---|---|---:|")
    rows = []
    for e in entries:
        reach = module_reach(R["edges"], e.module)
        comps = {component_of(m, depth) for m in reach}
        comps.discard(component_of(e.module, depth))
        rows.append((len(comps), e, sorted(comps)))
    rows.sort(key=lambda r: -r[0])
    for n, e, comps in (rows if full else rows[:12]):
        shown = ", ".join(f"`{c}`" for c in comps[:4]) + (" …" if len(comps) > 4 else "")
        w(f"| `{e.verb} {e.path}` | `{e.file}:{e.line}` | {n} {shown} |")
    if not full and len(rows) > 12:
        w("")
        w(f"_{len(rows) - 12} more; `--full` lists them._")
    w("")

    def route_rx(pattern):
        """Turn a route template into a matcher. Handles {id}, :id, <int:id>
        and *, so a link to /orders/1 is recognised as covered by /orders/{id}."""
        parts, out = re.split(r'(\{[^}]*\}|:\w+|<[^>]*>|\*)', pattern), []
        for i, seg in enumerate(parts):
            out.append('[^/]+' if i % 2 else re.escape(seg))
        return re.compile("^" + "".join(out) + "/?$")

    targets = {t for _, t, _, _ in nav}
    declared_rx = [route_rx(e.path) for e in entries]
    dangling = sorted(t for t in targets
                      if not any(rx.match(t) for rx in declared_rx))
    if dangling:
        w("**Linked but not declared here:** "
          + ", ".join(f"`{t}`" for t in dangling[:8])
          + ". Either these are served by another service or a rewrite rule, or "
            "they are broken links. Static analysis cannot tell the two apart, "
            "which is exactly why they are worth a look.\n")

    unlinked = sorted(p for p in screen_of.values()
                      if p not in {b for (a, b) in edges} and p != "/")
    if unlinked and edges:
        w("**Screens nothing links to:** " + ", ".join(f"`{p}`" for p in unlinked[:8])
          + ". Reachable only by typing the URL, by an external link, or by a "
            "redirect this tool did not recognise. Worth confirming each is "
            "deliberate; unreachable screens are a common place for stale code to "
            "survive.\n")
    return "\n".join(L)


def render_md(root, R, churn_map, full=False, code_F=None, code_stats=None,
              types_data=None, journeys=None):
    cfg, mods = R["cfg"], R["modules"]
    L = []; w = L.append
    w("# Architecture map\n")
    w(f"Derived from source by automap {VERSION}. Every line is computed, not written. "
      "Regenerate with `automap map`; do not edit by hand.\n")

    F, propcost = evaluate(R, churn_map, root)
    w(render_findings(F, full))

    if code_F is not None:
        w(render_code_findings(code_F, code_stats, full))
    w("---\n")
    w("The rest of this document is the evidence those findings were computed "
      "from.\n")

    w("## Coverage\n")
    w("What was read, and where every import went. Third-party means the target is "
      "expected to live outside this tree. Unaccounted means an import that looks "
      "local and resolved to nothing: those are edges missing from the graph below, "
      "usually a source root or path alias this tool has not been told about.\n")
    w("| Language | Fidelity | Files | Imports | Internal | Third-party | Unaccounted |")
    w("|---|---|---:|---:|---:|---:|---:|")
    for name in sorted(R["stats"]):
        s = R["stats"][name]
        lang = next(l for l in LANGS if l.name == name)
        flag = f"**{s['unknown']}**" if s["unknown"] else "0"
        w(f"| {name} | {lang.fidelity} | {s['files']} | {s['specs']} | "
          f"{s['internal']} | {s['external']} | {flag} |")
    w("")
    if R["unresolved"]:
        w("Unaccounted imports by language: "
          + ", ".join(f"{k} {v}" for k, v in sorted(R["unresolved"].items()))
          + ". Until that is zero, treat this graph as a lower bound on coupling.\n")

    w("## Shape\n")
    w(f"- {len(mods)} modules across {len(R['comp_mods'])} components")
    w(f"- {len(R['edges'])} internal import edges, {len(R['comp_edges'])} component couplings")
    w(f"- {sum(m.loc for m in mods.values())} lines")
    w(f"- propagation cost {propcost:.0%} — the share of other components an "
      f"average component can reach through import paths")
    if R["proj"].go_module:
        w(f"- Go module `{R['proj'].go_module}`")
    if R["proj"].ts_aliases:
        w(f"- {len(R['proj'].ts_aliases)} tsconfig path aliases honoured")
    if R["proj"].php_psr4:
        w(f"- {len(R['proj'].php_psr4)} PSR-4 namespace roots honoured")
    if R["proj"].rust_crate:
        w(f"- Rust crate `{R['proj'].rust_crate}`")
    w("")

    w("## Component graph\n")
    limit = 10 ** 6 if full else MAX_NODES
    keep, trimmed = graph_subset(R["comp_mods"], R["comp_edges"], R["cycles"],
                                 R["violations"], limit)
    w(mermaid(cfg, R["comp_mods"], R["comp_edges"], R["cycles"], keep,
              R["violations"]))
    if trimmed:
        w(f"\nShowing {len(keep)} of {len(R['comp_mods'])} components: everything "
          "involved in a cycle or a layer violation, then the most heavily coupled. "
          "The full edge list is in the baseline JSON; `--full` draws all of it.\n")
    w("\nDashed edges came from heuristic scanners. Thick borders are in a cycle. "
      "Labels count import sites.\n")

    if journeys:
        w(render_journeys(journeys[0], journeys[1], R, full))

    if types_data:
        decls, known, inherit, compose = types_data
        w(render_types(decls, known, inherit, compose, R["comp_mods"],
                       cfg["component_depth"], full))

    if len(R["comp_mods"]) > 1:
        w("## Dependency matrix\n")
        table, feedback, shown = dsm(R["comp_mods"], R["comp_edges"], R["cycles"],
                                     10 ** 6 if full else 30)
        w("Row depends on column; the number is how many import sites hold it. "
          "Components are ordered leaves first, so an ordinary dependency points "
          "to an earlier column and lands below the diagonal. **Every bold cell "
          "above the diagonal is a dependency pointing backwards.** Those cells "
          "are the whole review: scan the upper triangle and stop. A matrix is "
          "used rather than a drawing because it stays readable at any size.\n")
        if shown < len(R["comp_mods"]):
            w(f"Showing {shown} of {len(R['comp_mods'])} components; `--full` for all.\n")
        w(table)
        w(f"\n{feedback} cells above the diagonal.\n")

    if R["cycles"]:
        w("## Cycles, drawn alone\n")
        w("The same graph with everything acyclic removed, so the loop is visible "
          "without the rest of the system around it.\n")
        for cy in R["cycles"][:3]:
            keep_cy = set(cy if full else cy[:MAX_CYCLE_MEMBERS])
            w(mermaid({}, R["comp_mods"], R["comp_edges"], [cy], keep_cy))
            w("")

    eps = entry_points(R["modules"], R["edges"], R["comp_edges"])
    if eps:
        w("## Reachability from entry points\n")
        w("What each root actually pulls in, to a depth of three. Nothing imports "
          "these modules, so they are where a reader has to start.\n")
        for m in eps[: (len(eps) if full else 3)]:
            w(f"**{m.path}**\n")
            w("```")
            w(import_tree(m.name, R["modules"], R["edges"]))
            w("```\n")

    w("## Coupling\n")
    w("| Component | Languages | Modules | LOC | Fan-in | Fan-out | Instability |")
    w("|---|---|---:|---:|---:|---:|---:|")
    for r in R["metrics"]:
        w(f"| `{r['component']}` | {', '.join(r['langs'])} | {r['modules']} | {r['loc']} | "
          f"{r['fan_in']} | {r['fan_out']} | {r['instability']} |")
    w("\nInstability is fan-out / (fan-in + fan-out). A component many things depend on "
      "that itself depends widely propagates change in both directions.\n")

    mods_by_name = R["modules"]
    seams = defaultdict(list)
    for e in R["edges"]:
        la = mods_by_name[e.src].lang
        lb = mods_by_name[e.dst].lang
        if la != lb:
            seams[(la, lb)].append(e)
    if seams:
        w("## Language boundaries\n")
        w("Imports that cross a language line. No single compiler or linter checks "
          "these edges, so they break at runtime rather than at build time.\n")
        for (la, lb), es in sorted(seams.items()):
            w(f"- **{la} → {lb}** — {len(es)} edges")
            for e in es[:4]:
                w(f"  - `{e.src}` → `{e.dst}` at {e.file}:{e.line}")
        w("")

    w("## Cycles\n")
    if not R["cycles"]:
        w("None at component level.\n")
    for cy in R["cycles"]:
        shown = cy if (full or len(cy) <= MAX_CYCLE_MEMBERS) else cy[:MAX_CYCLE_MEMBERS]
        head = " ↔ ".join(f"`{c}`" for c in shown)
        if len(shown) < len(cy):
            head += f" … and {len(cy) - len(shown)} more"
        w(f"### Cycle of {len(cy)} components\n" if len(cy) > MAX_CYCLE_MEMBERS else f"### {head}\n")
        if len(cy) > MAX_CYCLE_MEMBERS:
            w(f"{head}\n")
        inside = [e for a in cy for b in cy for e in R["comp_edges"].get((a, b), [])]
        cuts = defaultdict(int)
        for e in inside:
            cuts[(component_of(e.src, R["cfg"]["component_depth"]),
                  component_of(e.dst, R["cfg"]["component_depth"]))] += 1
        thin = sorted(cuts.items(), key=lambda kv: kv[1])[:5]
        lim = len(inside) if full else MAX_CYCLE_EDGES
        for e in inside[:lim]:
            w(f"- `{e.src}` → `{e.dst}` — {e.file}:{e.line}")
        if len(inside) > lim:
            w(f"- … {len(inside) - lim} more edges inside this cycle")
        if len(cy) > 2 and thin:
            w("\nEdges inside this cycle held by the fewest import sites: "
              + ", ".join(f"`{a}`→`{b}` ({n})" for (a, b), n in thin)
              + ". These are the cheapest to remove; on a cycle this size removing "
                "one may not split it, so treat them as starting points rather than "
                "as a cut set.")
        w("")

    if cfg.get("layers"):
        w("## Layer violations\n")
        if not R["violations"]:
            w("None. Declared layering holds.\n")
        for v in R["violations"]:
            w(f"- `{v['from']}` ({v['from_layer']}) imports `{v['to']}` ({v['to_layer']}) — "
              + ", ".join(v["sites"]))
        w("")

    w("## External dependencies\n")
    ext, std = R["external"], R.get("stdlib", {})
    if ext:
        w("Third-party packages. Standard-library imports are counted separately "
          "below, because a dependency you cannot remove is not a design decision.\n")
        w("| Package | Sites | Components | First site |")
        w("|---|---:|---:|---|")
        for pkg in sorted(ext, key=lambda k: (-len(ext[k]), k))[:40]:
            cs = len({component_of(e.src, cfg["component_depth"]) for e in ext[pkg]})
            w(f"| `{pkg}` | {len(ext[pkg])} | {cs} | "
              f"{ext[pkg][0].file}:{ext[pkg][0].line} |")
    else:
        w("No third-party packages resolved outside the tree.")
    if std:
        top = sorted(std, key=lambda k: (-len(std[k]), k))[:12]
        w(f"\n{len(std)} standard-library modules imported; most used: "
          + ", ".join(f"`{k}` ({len(std[k])})" for k in top) + ".")
    w("")

    if churn_map:
        w("## Churn against size\n")
        w("Most-changed files in the last 12 months. This is where any map you carry "
          "in your head goes stale first.\n")
        w("| File | Lines touched | LOC | Language |")
        w("|---|---:|---:|---|")
        by_path = {m.path: m for m in mods.values()}
        for v, k in sorted(((v, k) for k, v in churn_map.items() if k in by_path), reverse=True)[:15]:
            w(f"| `{k}` | {v} | {by_path[k].loc} | {by_path[k].lang} |")
        w("")

    w("## Public surface\n")
    for c in sorted(R["comp_mods"]):
        pub = [(m.name, p) for m in sorted(R["comp_mods"][c], key=lambda x: x.name) for p in m.public]
        if not pub:
            continue
        w(f"<details><summary><code>{c}</code> — {len(pub)} exported</summary>\n")
        cur = None
        cut = len(pub) if full else MAX_SURFACE
        if len(pub) > cut:
            w(f"\n_Showing {cut} of {len(pub)}; `--full` lists them all._\n")
        for mn, p in pub[:cut]:
            if mn != cur:
                w(f"\n`{mn}`\n"); cur = mn
            w(f"- {p}")
        w("\n</details>\n")

    if R["errors"]:
        w("## Files that would not parse\n")
        for e in R["errors"]:
            w(f"- {e}")
        w("")

    w("---\n")
    w("**Not derivable from code.** Why these boundaries were chosen, what was "
      "rejected, and what constraint each one holds. `automap adr` scaffolds one "
      "file per decision point with the facts filled in and those questions blank.\n")
    return "\n".join(L)


# =====================================================================
# 9. ADR SCAFFOLDS
# =====================================================================

ADR_TMPL = """# {title}

<!-- Scaffolded by automap {version}. Facts below are derived from the tree and
     from git. The blanks are blank because no tool recovers intent from code. -->

## Status

<!-- proposed | accepted | superseded by ADR-nnn -->

## Observed state

{facts}

## Decision

<!-- BLANK. What is the rule going forward? One sentence, naming the mechanism. -->

## Why this and not the alternatives

<!-- BLANK. What else was on the table, and what ruled it out?
     Only you know this. Leaving it blank is more honest than a plausible guess. -->

## Consequences

{consequences}

<!-- Add the ones that are not mechanical: operational burden, who gets paged,
     what becomes hard. -->
"""


def adrs(root, out, R, blame):
    out.mkdir(parents=True, exist_ok=True)
    written, idx = [], [0]

    def write(slug, title, facts, cons):
        idx[0] += 1
        p = out / f"DRAFT-{idx[0]:03d}-{slug[:48]}.md"
        p.write_text(ADR_TMPL.format(version=VERSION, title=title,
                                     facts="\n".join(facts), consequences="\n".join(cons)))
        written.append(p)

    for cy in R["cycles"]:
        facts = [f"- Components `{'`, `'.join(cy)}` form an import cycle.",
                 "- Edges holding it together:"]
        for a in cy:
            for b in cy:
                for e in R["comp_edges"].get((a, b), [])[:3]:
                    s = f"  - `{e.src}` → `{e.dst}` at {e.file}:{e.line}"
                    if blame:
                        o = edge_origin(root, e)
                        if o:
                            s += f" — introduced {o['date']} in {o['commit']} ({o['subject'][:60]})"
                    facts.append(s)
        write("cycle-" + "-".join(sanitize(c) for c in cy).lower(),
              f"Cycle between {' and '.join(cy)}", facts,
              ["- These components cannot be built, tested, or released separately.",
               f"- A change in one may require rebuilding {len(cy)} components."])

    for v in R["violations"]:
        write(f"layer-{sanitize(v['from'])}-{sanitize(v['to'])}".lower(),
              f"{v['from']} depends upward on {v['to']}",
              [f"- `{v['from']}` sits in **{v['from_layer']}** and imports `{v['to']}` "
               f"in **{v['to_layer']}**.",
               "- Sites: " + ", ".join(f"`{s}`" for s in v["sites"])],
              ["- The layering declared in `.automap.json` no longer describes the code.",
               "- Either the dependency is wrong, or the declared layering is."])

    seams = defaultdict(list)
    for e in R["edges"]:
        la, lb = R["modules"][e.src].lang, R["modules"][e.dst].lang
        if la != lb:
            seams[(la, lb)].append(e)
    for (la, lb), es in sorted(seams.items()):
        write(f"seam-{sanitize(la)}-{sanitize(lb)}".lower(),
              f"{la} code depends directly on {lb} code",
              [f"- {len(es)} imports cross from {la} into {lb}."]
              + [f"  - `{e.src}` → `{e.dst}` at {e.file}:{e.line}" for e in es[:6]]
              + ["- No single language's compiler or linter checks these edges."],
              ["- Changes to the exported surface on the "
               f"{lb} side break the {la} side at runtime, not at build time."])

    for h in sorted([m for m in R["metrics"] if m["fan_in"] >= 3 and m["instability"] >= 0.5],
                    key=lambda x: -x["fan_in"])[:5]:
        write(f"hub-{sanitize(h['component'])}".lower(),
              f"{h['component']} is a coupling hub",
              [f"- Depended on by {h['fan_in']} components, itself depending on {h['fan_out']}.",
               f"- Instability {h['instability']}; {h['modules']} modules, {h['loc']} lines."],
              [f"- {h['fan_in']} components are exposed to changes here."])
    return written


# =====================================================================
# 10. BASELINE / CLI
# =====================================================================

def baseline(R):
    return {
        "version": VERSION,
        "components": {r["component"]: {k: r[k] for k in ("modules", "fan_in", "fan_out")}
                       for r in R["metrics"]},
        "edges": sorted(f"{a} -> {b}" for (a, b) in R["comp_edges"]),
        "cycles": [" <-> ".join(c) for c in R["cycles"]],
        "violations": [f"{v['from']} -> {v['to']}" for v in R["violations"]],
        "external": sorted(R["external"]),
    }


def diff_baseline(old, new):
    f = []
    for key, label in [("edges", "coupling"), ("cycles", "cycle"),
                       ("violations", "layer violation"), ("external", "external dependency")]:
        a, b = set(old.get(key, [])), set(new.get(key, []))
        f += [("new", label, x) for x in sorted(b - a)]
        f += [("gone", label, x) for x in sorted(a - b)]
    return f


def load_config(root: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    f = root / ".automap.json"
    if f.exists():
        user = json.loads(strip_jsonc(f.read_text()))
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
    return cfg


def main():
    ap = argparse.ArgumentParser(prog="automap", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["map", "check", "adr", "langs", "rules", "types",
                                        "journeys"])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("-o", "--out", default="ARCHITECTURE.md")
    ap.add_argument("--adr-dir", default="docs/adr")
    ap.add_argument("--baseline", default=".automap.baseline.json")
    ap.add_argument("--blame", action="store_true",
                    help="ask git which commit introduced each edge (slower)")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--no-journeys", action="store_true",
                    help="skip entry point and navigation extraction")
    ap.add_argument("--no-types", action="store_true",
                    help="skip type extraction and class diagrams")
    ap.add_argument("--no-code", action="store_true",
                    help="skip the inside-the-files pass; graph analysis only")
    ap.add_argument("--full", action="store_true",
                    help="no review-budget caps: every node, edge, and symbol")
    a = ap.parse_args()

    if a.command == "rules":
        import textwrap
        print(f"automap {VERSION} rule catalog\n")
        print(textwrap.fill(
            "Each rule fires on a measurement over your code. The explanation it "
            "prints is fixed text, identical on every repository; only the numbers "
            "and the evidence differ. No rule attempts to explain intent. Tune "
            "with \"thresholds\" in .automap.json, or turn one off with "
            "\"suppress\": [\"ARCH-ORPHAN\"].", 78))
        by_cat = defaultdict(list)
        for rid, cat, one in RULE_INDEX:
            by_cat[cat].append((rid, one))
        for cat in CATEGORIES:
            if not by_cat[cat]:
                continue
            print(f"\n{cat}")
            for rid, one in by_cat[cat]:
                print(f"  {rid:<20} {one}")
        print("\n--- inside the files (lexical for every language except Python) ---")
        by_c = defaultdict(list)
        for r_ in CODE_RULES:
            by_c[r_.category].append(r_)
        for cat in CODE_CATEGORIES:
            if not by_c[cat]:
                continue
            print(f"\n{cat}")
            for r_ in by_c[cat]:
                langs = ",".join(r_.langs) if r_.langs else "all"
                loop = " [in loops]" if r_.in_loop else ""
                print(f"  {r_.id:<20} {r_.severity:<7} {langs}{loop}")
        print("\n  Python metrics from the grammar: MNT-COMPLEX, MNT-LONGFUNC,")
        print("  MNT-PARAMS, MNT-MUTDEFAULT, RDB-NESTING. Clone detection: MNT-CLONE.")
        print(f"\n{len(RULE_INDEX)} graph rules, {len(CODE_RULES) + 6} code rules. "
              f"Thresholds:")
        for k, v in sorted(THRESHOLDS.items()):
            print(f"  {k:<26} {v}")
        return 0

    if a.command == "langs":
        print(f"automap {VERSION}\n")
        for l in sorted(LANGS, key=lambda x: (x.fidelity, x.name)):
            print(f"  {l.name:<12} {l.fidelity:<11} {' '.join(l.exts)}")
        print("\n  parsed      real grammar; edges are facts")
        print("  structural  unambiguous import syntax, manifest-driven resolution")
        print("  heuristic   convention matching; can be wrong, drawn dashed")
        return 0

    root = Path(a.root).resolve()
    cfg = load_config(root)
    if a.include_tests:
        cfg["include_tests"] = True
    R = build(root, cfg)
    base = baseline(R)

    if a.command == "map":
        ch = {} if a.no_git else churn(root)
        code_F = code_stats = None
        if not a.no_code:
            hits, funcs, clones, code_stats = scan_code(root, cfg, R["modules"])
            code_F = code_findings(hits, funcs, clones, cfg, R["modules"])
        types_data = None
        if not a.no_types:
            d_ = extract_types(root, R["modules"])
            types_data = (d_,) + type_relations(d_)
        journeys = None if a.no_journeys else extract_entries(root, R["modules"])
        (root / a.out).write_text(render_md(root, R, ch, a.full, code_F, code_stats,
                                            types_data, journeys))
        (root / a.baseline).write_text(json.dumps(base, indent=2, sort_keys=True) + "\n")
        print(f"wrote {a.out} and {a.baseline}")
        print(f"  {len(R['modules'])} modules, {len(R['comp_mods'])} components, "
              f"{len(R['cycles'])} cycles, {len(R['violations'])} layer violations")
        print("  languages: " + (", ".join(f"{k} {v['files']}"
              for k, v in sorted(R["stats"].items())) or "none found"))
        return 0

    if a.command == "check":
        bp = root / a.baseline
        if not bp.exists():
            print(f"no baseline at {a.baseline}; run `automap map` and commit it", file=sys.stderr)
            return 2
        findings = diff_baseline(json.loads(bp.read_text()), base)
        if not findings:
            print("architecture matches baseline")
            return 0
        for kind, label, what in findings:
            print(f"{'+' if kind == 'new' else '-'} {label}: {what}")
        new = [f for f in findings if f[0] == "new"]
        print(f"\n{len(new)} new, {len(findings)-len(new)} removed. If intended, run "
              f"`automap map` and commit the baseline.", file=sys.stderr)
        return 1 if new else 0

    if a.command == "journeys":
        en, nv = extract_entries(root, R["modules"])
        print(render_journeys(en, nv, R, True))
        return 0

    if a.command == "types":
        d_ = extract_types(root, R["modules"])
        known, inherit, compose = type_relations(d_)
        print(render_types(d_, known, inherit, compose, R["comp_mods"],
                           cfg["component_depth"], True))
        return 0

    if a.command == "adr":
        files = adrs(root, root / a.adr_dir, R, a.blame and not a.no_git)
        for f in files:
            print(f"wrote {f.relative_to(root)}")
        if not files:
            print("no decision points found")
        return 0


if __name__ == "__main__":
    sys.exit(main())
