@domain:architecture-docs @area:decision-records @from:automap/automap
Feature: Drafting architecture decision records
  Gives teams a pre-filled ADR draft for every structural decision point, with the facts filled in and the rationale left for humans.

  @id:architecture-docs.decision-records.001 @rule:docs.adr.draft-per-decision-point @inferred @source:automap.py#adrs
  Scenario: A numbered ADR draft is written for each cycle, layer violation, language seam and coupling hub
    Given the repository has a cycle between api, internal and store
    And .automap.json declares api in a higher layer than internal, and internal imports api
    When the developer runs `automap adr`
    Then docs/adr/DRAFT-001-cycle-api-internal-store.md is written, titled "Cycle between api and internal and store"
    And docs/adr/DRAFT-002-layer-internal-api.md is written, titled "internal depends upward on api"
    And the output lists "wrote docs/adr/..." for each draft

  @id:architecture-docs.decision-records.002 @rule:docs.adr.none-found @inferred @source:automap.py#main
  Scenario: A repository with no decision points gets no drafts
    Given the repository has no cycles, layer violations, cross-language imports or coupling hubs
    When the developer runs `automap adr`
    Then the output reads "no decision points found"
    And no draft is written
