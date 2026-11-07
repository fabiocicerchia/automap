@domain:architecture-review @area:architecture-findings @from:automap/automap
Feature: Tuning architecture findings
  Lets a team turn off rules that don't apply and adjust thresholds to its own codebase.

  @id:architecture-review.architecture-findings.001 @rule:review.findings.suppressed-rule-never-reported @inferred @source:automap.py#evaluate
  Scenario Outline: A suppressed rule never appears in the report
    Given .automap.json contains "suppress": ["<rule>"]
    And the code would otherwise trigger <rule>
    When the developer maps the repository
    Then the report contains no <rule> finding

    Examples:
      | rule        |
      | ARCH-ORPHAN |
      | ARCH-CYCLE  |
      | SEC-EVAL    |
      | MNT-COMPLEX |

  @id:architecture-review.architecture-findings.002 @rule:review.findings.threshold-override @verified @source:automap.py#code_findings @test:tests/test_automap.py#test_python_metrics_fire
  Scenario: Thresholds set in the configuration replace the defaults
    Given .automap.json sets "thresholds": {"max_params": 10}
    And the largest Python function takes 8 parameters
    When the developer maps the repository
    Then the report contains no MNT-PARAMS finding
