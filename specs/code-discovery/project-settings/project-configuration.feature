@domain:code-discovery @area:project-settings @from:automap/automap
Feature: Configuring automap for a repository
  Lets a team describe its layout and intent in a committed .automap.json.

  @id:code-discovery.project-settings.001 @rule:discovery.settings.unknown-keys-ignored @inferred @source:automap.py#load_config @questionable
  Scenario: Comments are allowed in the configuration and unrecognised settings are silently ignored
    Given .automap.json contains // comments, "component_depth": 2 and a misspelt key "layer"
    When the developer maps the repository
    Then components are grouped two levels deep
    And the misspelt "layer" setting has no effect and no warning is shown
