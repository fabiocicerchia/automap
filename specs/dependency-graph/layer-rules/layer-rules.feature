@domain:dependency-graph @area:layer-rules @from:automap/automap
Feature: Enforcing declared architectural layers
  Lets architects declare their intended layering and see every dependency that points the wrong way.

  Background:
    Given Given .automap.json declares the layers in order "interface" (web, api), "core" (domain), "infrastructure" (store, internal)

  @id:dependency-graph.layer-rules.001 @rule:graph.layers.upward-dependency-is-violation @verified @source:automap.py#layer_violations @test:tests/test_automap.py#test_layer_violations_detected
  Scenario Outline: Only a dependency from a lower layer to a higher one is a layer violation
    Given component <from> imports component <to>
    When the developer maps the repository
    Then the dependency is reported as a layer violation: <violation>

    Examples:
      | from     | to    | violation                             |
      | internal | api   | yes, "internal depends upward on api" |
      | domain   | web   | yes                                   |
      | api      | store | no                                    |
      | web      | api   | no                                    |
      | scripts  | api   | no (scripts is in no layer)           |

  @id:dependency-graph.layer-rules.002 @rule:graph.layers.undeclared-layers-noted @inferred @source:automap.py#evaluate
  Scenario: Without declared layers the report notes that layer checks are off
    Given .automap.json declares no layers
    When the developer maps the repository
    Then the report contains the note "No layering declared, so layer checks are off."
    And no layer violations are reported
