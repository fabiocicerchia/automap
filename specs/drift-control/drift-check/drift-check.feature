@domain:drift-control @area:drift-check @from:automap/automap
Feature: Checking the architecture against its baseline
  Lets a team fail a build when new coupling, cycles, layer violations or third-party dependencies appear that nobody re-baselined, so architectural drift is caught at review time.

  Background:
    Given Given a repository that automap can scan

  @id:drift-control.drift-check.001 @rule:drift.check.requires-baseline @inferred @source:automap.py#main
  Scenario: Checking without a committed baseline is refused
    Given no baseline file exists at the baseline path
    When the developer runs `automap check`
    Then the error output reads "no baseline at .automap.baseline.json; run `automap map` and commit it"
    And the command exits with status 2

  @id:drift-control.drift-check.002 @rule:drift.check.matching-architecture-passes @verified @source:automap.py#main @test:tests/test_automap.py#test_two_runs_identical
  Scenario: An unchanged architecture passes the check
    Given the committed baseline was written by `automap map` from the current code
    When the developer runs `automap check`
    Then the output reads "architecture matches baseline"
    And the command exits with status 0

  @id:drift-control.drift-check.003 @rule:drift.check.new-dependency-fails @inferred @source:automap.py#diff_baseline
  Scenario Outline: New coupling, cycles, layer violations or external dependencies fail the check
    Given the committed baseline does not contain the <kind> "<item>"
    When the developer runs `automap check` after the code introduced it
    Then the output lists "+ <kind>: <item>"
    And the summary reports "1 new, 0 removed. If intended, run `automap map` and commit the baseline."
    And the command exits with status 1

    Examples:
      | kind                | item                       |
      | coupling            | web -> store               |
      | cycle               | api <-> internal <-> store |
      | layer violation     | internal -> api            |
      | external dependency | requests                   |

  @id:drift-control.drift-check.004 @rule:drift.check.removals-only-pass @inferred @source:automap.py#main
  Scenario: Dependencies that disappeared are reported but do not fail the check
    Given the committed baseline contains the coupling "web -> store"
    And the code no longer has that dependency
    When the developer runs `automap check`
    Then the output lists "- coupling: web -> store"
    And the summary reports "0 new, 1 removed"
    And the command exits with status 0
