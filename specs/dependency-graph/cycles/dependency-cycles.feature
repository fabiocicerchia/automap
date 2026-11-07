@domain:dependency-graph @area:cycles @from:automap/automap
Feature: Detecting dependency cycles
  Shows teams which components can no longer be built, tested or released on their own because they import each other.

  @id:dependency-graph.cycles.001 @rule:graph.cycles.component-cycle-reported @verified @source:automap.py#tarjan @test:tests/test_automap.py#test_cycles_detected @test:tests/test_automap.py#test_automap_maps_itself
  Scenario: Components that import each other, directly or transitively, are reported as a cycle
    Given component api imports internal, internal imports store, and store imports api
    When the developer maps the repository
    Then the cycle "api <-> internal <-> store" is recorded in the baseline
    And the report raises a Serious finding starting "1 dependency cycle(s); the largest binds 3 of", followed by the repository's component count
