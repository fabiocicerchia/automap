@domain:design-models @area:user-journeys @from:automap/automap
Feature: Tracing ways into the application
  Shows teams every route and page users can reach, what each one touches, and links that point nowhere.

  Background:
    Given a web app with HTTP routes GET /products and POST /checkout, and pages under pages/ for products, cart, checkout and help

  @id:design-models.user-journeys.001 @rule:design.journeys.entry-points-listed @verified @source:automap.py#extract_entries @test:tests/test_automap.py#test_http_routes_found @test:tests/test_automap.py#test_file_routing_found
  Scenario: Routes and file-based pages are listed as ways in
    When the developer runs `automap journeys`
    Then HTTP routes GET /products and POST /checkout are counted with their framework
    And the pages /products, /cart, /checkout and /help are counted under Pages

  @id:design-models.user-journeys.002 @rule:design.journeys.reach-per-entry @verified @source:automap.py#render_journeys @test:tests/test_automap.py#test_blast_radius
  Scenario: Each way in shows which components it can reach
    Given the handler for GET /products imports a service that imports the store
    When the developer runs `automap journeys`
    Then the row for "GET /products" lists services and store among the components reached

  @id:design-models.user-journeys.003 @rule:design.journeys.undeclared-links-flagged @inferred @source:automap.py#render_journeys
  Scenario: Links to paths no route declares are flagged
    Given a page links to /orders/1 and to /legacy/report
    And a route /orders/{id} is declared and nothing declares /legacy/report
    When the developer runs `automap journeys`
    Then the output lists `/legacy/report` under "Linked but not declared here"
    And /orders/1 is not listed there
