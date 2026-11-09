@domain:design-models @area:class-diagrams @from:automap/automap
Feature: Showing how types relate
  Shows developers the system's nouns and how they inherit from and contain one another, without guessing at what it can't see.

  @id:design-models.class-diagrams.001 @rule:design.types.relations-only-between-found-types @verified @source:automap.py#type_relations @test:tests/test_automap.py#test_inheritance @test:tests/test_automap.py#test_composition_from_annotations @test:tests/test_automap.py#test_untyped_field_yields_no_edge
  Scenario: Inheritance and composition are drawn only between types found in the code
    Given CreditNote extends Invoice, and LineItem has a field typed Money
    And Invoice has a field `lines: list` with no element type
    When the developer runs `automap types`
    Then CreditNote is shown inheriting from Invoice
    And LineItem is shown containing Money
    And no containment is drawn from Invoice to LineItem
