@domain:developer-tooling @area:make-targets @from:automap/makefile
Feature: Running repository chores through make
  Gives a contributor or CI operator one fixed set of make verbs for this repository, each either doing real work or saying plainly why it does nothing.

  Background:
    Given the operator is in a checkout of the automap repository

  @id:developer-tooling.make-targets.001 @rule:tooling.make.bare-make-lists-targets @inferred @source:Makefile#help
  Scenario: Running make with no target lists every target with its description
    When the operator runs make with no target
    Then the output lists help, run, test, analyze, setup, install, build, lint and format
    And each target is shown with its one-line description, such as "analyze    Fail if this repo's architecture has drifted from its baseline"
    And no target's work is performed

  @id:developer-tooling.make-targets.002 @rule:tooling.make.not-applicable-targets-explain-why @inferred @source:Makefile#setup @questionable
  Scenario Outline: A target with nothing to do says why and succeeds
    When the operator runs make <target>
    Then the output explains "<reason>"
    And the output points to "See README > Not applicable."
    And the command exits with status 0

    Examples:
      | target | reason                                                                                                                 |
      | setup  | Nothing to set up: automap.py is standard library only, and this repo has no pre-commit config to install a hook from. |
      | build  | Nothing to build: one pure-Python script.                                                                              |
      | format | No formatter in this repo: nothing rewrites automap.py.                                                                |

  @id:developer-tooling.make-targets.003 @rule:tooling.make.run-maps-the-checkout-by-default @inferred @source:Makefile#run
  Scenario Outline: The run target maps the current checkout unless told otherwise
    When the operator runs make run with ARGS "<args>"
    Then automap runs as "automap <args>"
    And <outcome>

    Examples:
      | args      | outcome                                                            |
      | (not set) | ARCHITECTURE.md and the baseline file are written for the checkout |
      | rules     | the rule catalog is printed                                        |
      | langs     | the supported languages are printed                                |

  @id:developer-tooling.make-targets.004 @rule:tooling.make.analyze-fails-on-new-architecture @inferred @source:Makefile#analyze @questionable
  Scenario Outline: The analyze target fails when the repository's architecture gains something its baseline lacks
    Given the committed baseline was recorded earlier
    And since then the architecture has <change>
    When the operator runs make analyze
    Then each difference is listed, prefixed "+" for new and "-" for removed
    And a summary such as "1 new, 0 removed" is printed
    And make analyze <result>

    Examples:
      | change                            | result                                              |
      | gained a new component dependency | fails (exit status 1)                               |
      | only lost a component dependency  | succeeds (exit status 0)                            |
      | not changed                       | succeeds and prints "architecture matches baseline" |
