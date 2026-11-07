@domain:code-discovery @area:language-support @from:automap/automap
Feature: Listing supported languages
  Tells developers which languages automap reads and how much to trust the result for each.

  @id:code-discovery.language-support.001 @rule:discovery.languages.listed-with-fidelity @inferred @source:automap.py#main
  Scenario: The languages command lists every language with its fidelity and file extensions
    Given any working directory
    When the developer runs `automap langs`
    Then the output starts with "automap 2.0"
    And each language is listed with its fidelity (parsed, structural or heuristic) and file extensions, ordered by fidelity then name
    And the output explains: "parsed real grammar; edges are facts", "structural unambiguous import syntax, manifest-driven resolution", "heuristic convention matching; can be wrong, drawn dashed"
