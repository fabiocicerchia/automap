# automap

Derives architecture documentation from a codebase. No model, no network, no
generation.

Every statement it produces is computed from the source tree or from git and
carries a `file:line` or a commit hash. Where only a human knows the answer —
why a boundary was drawn, what was rejected — it emits a blank and says so.

Single file, Python 3.8+, standard library only. Nothing to install.

```bash
python3 automap.py map .      # ARCHITECTURE.md + diagrams + baseline
python3 automap.py check .    # exit 1 when the architecture drifts
python3 automap.py adr .      # ADR scaffolds, facts filled, rationale blank
python3 automap.py types .    # class diagrams
python3 automap.py journeys . # entry points and navigation
python3 automap.py rules      # the full rule catalog, nothing measured
python3 automap.py langs      # supported languages and their fidelity
```

## Why it does not call a model

The problem it was built for is that LLM-generated design docs are hard to
review: you did not build the mental map, so you cannot check the artefact. A
tool that answers this by generating a second document leaves you with two
unverified texts instead of one.

So nothing here writes prose about your code. Rules fire on measurements; the
explanations attached to them are fixed text, identical on every repository,
the way a linter documents a rule. Only the numbers and the evidence change.

## What it reads

| Fidelity | Languages | Meaning |
|---|---|---|
| parsed | Python | real grammar; edges and metrics are facts |
| structural | Go, Java, Kotlin, C#, Rust, Scala, Swift, TypeScript, JavaScript | unambiguous import syntax, resolved through the project manifest |
| heuristic | PHP, Ruby, C, C++ | convention matching; can be wrong, drawn dashed |

Resolution reads `go.mod`, `tsconfig.json` paths, `composer.json` PSR-4 and
`Cargo.toml`, because mapping `@/lib/db` or `github.com/acme/svc/store` back to
a file is where these tools usually fail.

The report opens with a coverage table splitting every import into internal,
third-party, and unaccounted. Until unaccounted is zero, the graph is a lower
bound, and the document says so.

## What it produces

**Graph analysis** — component dependency graph, dependency structure matrix
(back edges above the diagonal), cycles drawn alone, reachability trees from
entry points, coupling metrics, propagation cost.

**33 architecture rules** — cycles, layer violations, layer skipping, hidden
coupling from git co-change, keystone components, stable-dependencies
violations, hubs, churn against fan-in, bus factor, god components, vendor
spread, untested components, and more.

**31 code rules** — security, performance, scalability, algorithms and data
structures, maintainability, readability. Python is analysed with `ast` so
complexity, nesting, length and parameter counts are exact. Other languages are
matched lexically against comment-stripped source: those rules report the
presence of a construct, not a proven defect. There is no dataflow analysis.

**Type extraction** — classes, interfaces, structs, traits and enums with their
fields, methods, inheritance, and the composition implied by typed fields.
Relationships are drawn only between types found in the tree.

**Entry points and navigation** — HTTP routes, pages, CLI commands, jobs and
queue handlers across a dozen frameworks, the navigation edges between screens,
and what each entry point can reach through imports.

## Configuration

`.automap.json` at the repository root:

```json
{
  "component_depth": 1,
  "layers": {
    "interface": ["api", "web"],
    "core": ["domain"],
    "infrastructure": ["store", "db"]
  },
  "source_roots": ["src", "packages"],
  "aliases": { "@/": "src/" },
  "thresholds": { "max_complexity": 15 },
  "suppress": ["ARCH-ORPHAN"]
}
```

Declaring `layers` is what turns a description into a check that can fail:
without it the tool finds cycles, but cannot know that a given dependency
points the wrong way.

## Drift detection

`automap map` writes a baseline JSON alongside the document. Commit it.
`automap check` recomputes and exits 1 on any new coupling, cycle, or layer
violation:

```
+ coupling: web -> storage
+ cycle: api <-> internal <-> store
```

This is the part that matters. A correct diagram written once is worthless six
months later; making drift a build failure is what keeps the map true.

## What it deliberately will not do

- **Explain why.** Rationale is not recoverable from code. `automap adr` fills
  in the observed state and the commit that introduced each edge, then leaves
  Decision and Alternatives blank.
- **Draw sequence diagrams or a call graph.** Both need call ordering or type
  inference. Doing it by pattern matching produces output that looks
  authoritative and is quietly wrong.
- **Report real user journeys.** Those come from analytics. The tool reports the
  journeys the code permits, which is not the same thing and is labelled as such.

## Known limits

- Components are name-based, so two languages with a same-named top-level
  directory merge into one.
- Dynamic imports, dependency injection containers and reflection are invisible
  to any static tool.
- C and C++ resolution is include-path guessing, not preprocessing.
- Security rules find risky constructs, not exploitable paths. `eval` in a
  parser is flagged as loudly as `eval` on request input.
- The code pass roughly triples runtime on large trees; `--no-code` skips it.

## Tests

```bash
python3 tests/test_automap.py
```

38 tests over four fixture repositories. Each fixture contains specific defects
on purpose, so a rule firing on it is a true positive. Most tests pin a bug that
was real during development — an `import` keyword inside a string literal
swallowing the next line, `.get()` on a dictionary counted as a database query,
relative imports in a package `__init__` resolving to the parent.

## License

MIT.
