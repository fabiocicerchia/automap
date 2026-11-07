@domain:code-discovery @area:source-scanning @from:automap/automap
Feature: Scanning the repository's source
  Makes sure the map reflects the product code people ship, not tests, build output or code that is commented out.

  @id:code-discovery.source-scanning.001 @rule:discovery.scan.tests-kept-out-of-graph @inferred @source:automap.py#build
  Scenario Outline: Test code is kept out of the architecture unless tests are included
    Given the repository has product code under src/ and test files under tests/
    When the developer maps the repository <option>
    Then the test modules <shown> in the component graph

    Examples:
      | option               | shown   |
      | without options      | are not |
      | with --include-tests | are     |

  @id:code-discovery.source-scanning.002 @rule:discovery.scan.excluded-and-hidden-dirs-skipped @inferred @source:automap.py#walk_sources
  Scenario Outline: Excluded and hidden directories are never scanned
    Given the repository has source files under <dir>
    When the developer maps the repository
    Then no module from <dir> appears in the report

    Examples:
      | dir           |
      | node_modules/ |
      | vendor/       |
      | build/        |
      | .cache/       |

  @id:code-discovery.source-scanning.003 @rule:discovery.scan.commented-imports-ignored @verified @source:automap.py#mask_comments @test:tests/test_automap.py#test_commented_out_import_is_ignored @test:tests/test_automap.py#test_import_keyword_inside_string_literal_is_ignored @test:tests/test_automap.py#test_go_struct_tag_is_not_an_import
  Scenario: Imports that are commented out or mentioned inside strings are not dependencies
    Given a TypeScript file has a commented-out import of lib/should-not-be-seen
    And the file contains the text "is not an import" in a string, followed by a real import of ./types on line 7
    When the developer maps the repository
    Then no dependency on lib/should-not-be-seen is shown
    And the dependency on module web.types is shown at line 7
