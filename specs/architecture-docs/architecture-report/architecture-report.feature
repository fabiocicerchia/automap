@domain:architecture-docs @area:architecture-report @from:automap/automap
Feature: Producing the architecture report
  Gives developers a generated ARCHITECTURE.md plus a baseline in one command, with a summary of what was found.

  @id:architecture-docs.architecture-report.001 @rule:docs.report.map-writes-report-and-baseline @inferred @source:automap.py#main
  Scenario: Mapping writes the report and the baseline and summarises the result
    Given a repository with 42 modules in 5 components, 1 cycle and no layer violations
    When the developer runs `automap map`
    Then ARCHITECTURE.md and .automap.baseline.json are written at the repository root
    And the output reads "wrote ARCHITECTURE.md and .automap.baseline.json"
    And the output reads "42 modules, 5 components, 1 cycles, 0 layer violations"
    And the output lists the languages found with their file counts
