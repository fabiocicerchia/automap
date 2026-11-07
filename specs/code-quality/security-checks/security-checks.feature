@domain:code-quality @area:security-checks @from:automap/automap
Feature: Flagging risky code
  Points reviewers at code constructs that commonly lead to security incidents.

  @id:code-quality.security-checks.001 @rule:quality.security.risky-constructs-flagged @verified @source:automap.py#scan_code @test:tests/test_automap.py#test_security_rules_fire
  Scenario Outline: Risky constructs are reported as security findings with where they occur
    Given a source file contains <construct>
    When the developer maps the repository
    Then the "Inside the files" section reports <rule> under Security with severity <severity>
    And the evidence lists the file and line of the occurrences, up to six

    Examples:
      | construct                             | rule            | severity        |
      | eval(user_input)                      | SEC-EVAL        | Serious         |
      | subprocess.run(cmd, shell=True)       | SEC-SHELL       | Serious         |
      | pickle.loads(data)                    | SEC-DESERIALIZE | Serious         |
      | requests.get(url, verify=False)       | SEC-TLSOFF      | Serious         |
      | api_key = "EXAMPLEEXAMPLEEXAMPLE1234" | SEC-SECRETLIKE  | Serious         |
      | hashlib.md5(                          | SEC-WEAKCRYPTO  | Worth attention |
