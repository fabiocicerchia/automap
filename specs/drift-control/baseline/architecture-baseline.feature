@domain:drift-control @area:baseline @from:automap/automap
Feature: Recording the architecture baseline
  Gives teams a committed, diff-friendly snapshot of their architecture that later checks compare against.

  @id:drift-control.baseline.001 @rule:drift.baseline.deterministic-snapshot @verified @source:automap.py#baseline @test:tests/test_automap.py#test_two_runs_identical @test:tests/test_automap.py#test_baseline_is_sorted
  Scenario: Mapping unchanged code always produces the same sorted baseline
    Given a repository whose code has not changed between two runs
    When the developer runs `automap map` twice
    Then both runs write identical baseline files
    And the baseline lists the version, each component's module count, fan-in and fan-out, the component dependencies as "a -> b", the cycles, the layer violations and the external packages
    And the component dependencies and external packages are listed in sorted order
