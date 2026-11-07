@domain:architecture-review @area:severity-ranking @from:automap/automap
Feature: Ranking findings for review
  Keeps the report readable by putting the most serious findings first and capping how many are shown unless the reader asks for everything.

  @id:architecture-review.severity-ranking.001 @rule:review.ranking.severity-first @inferred @source:automap.py#evaluate
  Scenario: Findings are listed most serious first with a count per severity
    Given the repository triggers a cycle (serious), an untested-components finding (worth attention) and the no-layers note
    When the developer maps the repository
    Then the report shows a table counting Serious, Worth attention and Notes
    And the findings appear in the order Serious, Worth attention, Note

  @id:architecture-review.severity-ranking.002 @rule:review.ranking.cap-unless-full @inferred @source:automap.py#render_findings
  Scenario Outline: Only the first twelve findings are shown unless full detail is requested
    Given the repository triggers 15 architecture findings
    When the developer maps the repository <option>
    Then the report shows <shown> findings
    And <note>

    Examples:
      | option          | shown | note                                                             |
      | without options | 12    | the report says "3 further finding(s); `--full` shows them all." |
      | with --full     | 15    | no further-findings note appears                                 |

  @id:architecture-review.severity-ranking.003 @rule:review.ranking.empty-is-a-result @inferred @source:automap.py#render_findings
  Scenario: A clean repository is reported as a real result
    Given no architecture rule fires on the repository
    When the developer maps the repository
    Then the report reads "No rule fired. That is a real result rather than an empty section"
