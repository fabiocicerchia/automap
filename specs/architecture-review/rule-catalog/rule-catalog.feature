@domain:architecture-review @area:rule-catalog @from:automap/automap
Feature: Auditing the rule catalog
  Lets reviewers check every rule automap can raise before trusting its findings.

  @id:architecture-review.rule-catalog.001 @rule:review.catalog.rules-command-lists-all @inferred @source:automap.py#main
  Scenario: The rules command prints every graph and code rule with its category, plus the default thresholds
    Given any working directory
    When the developer runs `automap rules`
    Then the output starts with "automap 2.0 rule catalog"
    And graph rules such as ARCH-CYCLE are grouped under their category with a one-line description
    And code rules such as SEC-EVAL are grouped by category with severity, languages and "[in loops]" where it applies
    And the output ends with the rule counts and every threshold with its default value
