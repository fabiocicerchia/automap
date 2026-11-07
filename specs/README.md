# Specifications of automap

What this code does, one atomic Gherkin scenario per behaviour, read from the code by code-atlas.
Tags: `@verified` a test asserts it · `@inferred` read from the code only · `@gap` an entry point no test covers · `@human` written or amended by a person, never rewritten.

## Architecture documentation
The documents automap writes so people can read and review the architecture.

- **Architecture report**: [Producing the architecture report](architecture-docs/architecture-report/architecture-report.feature) (1)
- **Decision records**: [Drafting architecture decision records](architecture-docs/decision-records/decision-records.feature) (2)

## Architecture review
Rule-based findings about the health of the architecture, with fixed explanations and evidence.

- **Architecture findings**: [Tuning architecture findings](architecture-review/architecture-findings/tuning-findings.feature) (2)
- **Rule catalog**: [Auditing the rule catalog](architecture-review/rule-catalog/rule-catalog.feature) (1)
- **Severity ranking**: [Ranking findings for review](architecture-review/severity-ranking/finding-ranking.feature) (3)

## Code discovery
How automap finds, reads and understands the source files of a repository across languages.

- **Import resolution**: [Resolving imports across languages](code-discovery/import-resolution/import-resolution.feature) (2)
- **Language support**: [Listing supported languages](code-discovery/language-support/supported-languages.feature) (1)
- **Project settings**: [Configuring automap for a repository](code-discovery/project-settings/project-configuration.feature) (1)
- **Source scanning**: [Scanning the repository's source](code-discovery/source-scanning/source-scanning.feature) (3)

## Code quality
An inside-the-files pass that looks for code-level problems beyond the dependency graph.

- **Security checks**: [Flagging risky code](code-quality/security-checks/security-checks.feature) (1)

## Dependency graph
Building and measuring the graph of how modules and components depend on each other.

- **Cycles**: [Detecting dependency cycles](dependency-graph/cycles/dependency-cycles.feature) (1)
- **Layer rules**: [Enforcing declared architectural layers](dependency-graph/layer-rules/layer-rules.feature) (2)

## Design models
Extracting the types and user-facing entry points of the code to show its design.

- **Class diagrams**: [Showing how types relate](design-models/class-diagrams/type-relations.feature) (1)
- **User journeys**: [Tracing ways into the application](design-models/user-journeys/user-journeys.feature) (3)

## Developer tooling
Commands for running, testing, installing and linting automap itself.

- **Make targets**: [Running repository chores through make](developer-tooling/make-targets/make-targets.feature) (4)

## Drift control
Keeping the architecture from changing unnoticed between versions.

- **Baseline**: [Recording the architecture baseline](drift-control/baseline/architecture-baseline.feature) (1)
- **Drift check**: [Checking the architecture against its baseline](drift-control/drift-check/drift-check.feature) (4)
