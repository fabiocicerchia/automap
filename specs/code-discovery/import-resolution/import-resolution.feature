@domain:code-discovery @area:import-resolution @from:automap/automap
Feature: Resolving imports across languages
  Lets polyglot teams trust that the map links each import to the right internal module, whatever language and project layout they use.

  @id:code-discovery.import-resolution.001 @rule:discovery.resolution.project-conventions @verified @source:automap.py#resolve @test:tests/test_automap.py#test_tsconfig_path_alias @test:tests/test_automap.py#test_go_module_prefix @test:tests/test_automap.py#test_java_maven_source_root @test:tests/test_automap.py#test_csharp_namespace_not_path @test:tests/test_automap.py#test_rust_crate_prefix @test:tests/test_automap.py#test_php_psr4 @test:tests/test_automap.py#test_ruby_require_relative @test:tests/test_automap.py#test_cpp_include
  Scenario Outline: Imports resolve to internal modules through each language's project conventions
    Given a <language> project using <convention>
    When the developer maps the repository
    Then module <from> is shown depending on module <to>

    Examples:
      | language   | convention                   | from                    | to                   |
      | TypeScript | a tsconfig path alias        | web.app                 | lib.db               |
      | Go         | the go.mod module prefix     | api.handlers            | store                |
      | Java       | the Maven source root        | com.acme.api.Controller | com.acme.core.Ledger |
      | C#         | namespaces rather than paths | Acme.Api.Endpoint       | Acme.Core.Money      |
      | Rust       | the crate prefix             | engine                  | store.disk           |
      | PHP        | composer PSR-4 autoload      | Http.Router             | Domain.Invoice       |
      | Ruby       | require_relative             | billing.charge          | money                |
      | C++        | #include of a local header   | src.main                | include.engine       |

  @id:code-discovery.import-resolution.002 @rule:discovery.resolution.packages-not-counted-as-gaps @verified @source:automap.py#third_party @test:tests/test_automap.py#test_stdlib_is_not_third_party @test:tests/test_automap.py#test_go_stdlib_recognised @test:tests/test_automap.py#test_no_unaccounted_imports @questionable
  Scenario: Standard library and third-party imports are not counted as gaps in the map
    Given a Python module imports sys and requests
    And a Go module imports fmt and github.com/gin-gonic/gin
    When the developer maps the repository
    Then none of these imports is counted as an unresolved local import
    And sys and fmt are treated as the standard library
    And requests is listed as a third-party package
    And the gin import is listed under the third-party package "github"
