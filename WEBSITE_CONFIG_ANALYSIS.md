================================================================================
           GOVERNMENT WEBSITE SCRAPER CONFIGURATION ANALYSIS REPORT
================================================================================
Generated: April 2026
Configuration File: gov_aggregator/data/sites_config.json

================================================================================
                           EXECUTIVE SUMMARY
================================================================================

Total Sites in Configuration: 73
├── Active Sites: 71
└── Inactive Sites: 2 (ministry-of-textiles, department-of-land-resources)

Configuration Status:
├── COMPLETE (Has Sections): 42 sites (57%)
├── PARTIAL (Has Selectors Only): 25 sites (34%)
└── INCOMPLETE (No Selectors, No Sections): 6 sites (8%)

================================================================================
                    SITES REQUIRING CONFIGURATION
================================================================================

The following 6 sites have NO selectors and NO sections - they will NOT scrape
until you add proper CSS selectors or section configurations.

--------------------------------------------------------------------------------
1. ministry-of-commerce (Ministry of Commerce)
--------------------------------------------------------------------------------
   URL: https://www.commerce.gov.in/press-releases/
   Complexity: LOW
   Difficulty: EASY - Quick win, simple press release page
   Recommendation: Start with this one

--------------------------------------------------------------------------------
2. dot (Department of Telecommunications)
--------------------------------------------------------------------------------
   URL: https://www.dot.gov.in/whats-new
   Complexity: MEDIUM
   Difficulty: STANDARD - Drupal/CMS-based government portal
   Recommendation: Second priority

--------------------------------------------------------------------------------
3. irdai (Insurance Regulatory and Development Authority)
--------------------------------------------------------------------------------
   URL: https://www.irdai.gov.in/whats-new
   Complexity: MEDIUM
   Difficulty: STANDARD - Regulatory body with standard structure
   Recommendation: Third priority

--------------------------------------------------------------------------------
4. gst (GST Portal)
--------------------------------------------------------------------------------
   URL: https://services.gst.gov.in/services/advisory/advisoryandreleases
   Complexity: MEDIUM
   Difficulty: STANDARD - Standard government portal, may need pagination
   Recommendation: Fourth priority

--------------------------------------------------------------------------------
5. sebi (Securities and Exchange Board of India)
--------------------------------------------------------------------------------
   URL: https://www.sebi.gov.in/
   Complexity: HIGH
   Difficulty: HARD - Large portal with multiple sections, may need JS rendering
   Recommendation: Fifth priority - tackle after completing easier ones

--------------------------------------------------------------------------------
6. rbi (Reserve Bank of India)
--------------------------------------------------------------------------------
   URL: https://www.rbi.org.in/Scripts/NotificationUser.aspx?Year=2026&Month=0
   Complexity: HIGH
   Difficulty: HARD - ASPX pages, complex pagination, high data volume
   Recommendation: Last priority - most complex site in the list

================================================================================
                      PARTIALLY CONFIGURED SITES
================================================================================

These 25 sites have selectors configured but only scrape a single page.
They work but may miss content from other sections.

 1. department-of-food-and-public-distribution
 2. export-promotion-council
 3. industry-and-internal-trade
 4. income-tax
 5. industry-and-internal-trade-ip
 6. ministry-of-steel
 7. ministry-of-textiles [INACTIVE]
 8. project-export-promotion-council
 9. ministry-of-home-affairs
10. ministry-of-tourism-for-hotels-restaurants
11. ministry-of-women-child-development
12. department-of-fertilizers
13. department-of-fisheries
14. directorate-of-marketing-and-inspection-for-pulses-grading-and-marking
15. national-food-security-mission-for-oilseeds
16. department-of-mines
17. bis (Bureau of Indian Standards)
18. fcra (FCRA Online)
19. cbic-only-gst
20. joint-electricty-regulatory-commission-goa-uts
21. international-financial-services-centres-authority
22. directorate-general-of-fire-services-civil-defence-home-guards
23. directorate-of-cashewnut-cocoa-development
24. directorate-general-of-foreign-trade
25. employees-provident-fund-organisation

================================================================================
                           CLEANUP COMPLETED
================================================================================

During this session, the following cleanup was performed:

REMOVED DUPLICATES:
  ✓ department-of-agriculture-and-farmers-welfare (3 → 1)
  ✓ dept-of-empowerment-of-persons-with-disabilities (2 → 1)
  ✓ warehousing-development-and-regulatory-authority (2 → 1)
  ✓ pharmacy-council-of-india (merged with pharmacy-council)
  ✓ consumer-affairs-news (merged with consumer-affairs)

DEACTIVATED:
  ✓ ministry-of-textiles (marked inactive, was causing issues)

CLEANED UP auto_fixed_sites ARRAY:
  ✓ Removed: cgwb (duplicate short name)
  ✓ Removed: consumer-affairs-news (duplicate entry)

================================================================================
                      HOW TO ADD SELECTORS
================================================================================

For incomplete sites, you need to:

1. Visit the website URL
2. Inspect the HTML structure (F12 in browser)
3. Find the CSS selector for:
   - Item container (table rows, list items, cards)
   - Title element
   - Link/URL element
   - Date element (optional)

4. Add to sites_config.json in this format:

{
  "site_key": "site-key-name",
  "selectors": {
    "item_selector": "table tbody tr",      // Container for each item
    "title_selector": "td:nth-child(2)",   // Title text
    "link_selector": "td a",                // Clickable link
    "date_selector": "td:nth-child(1)"      // Date (optional)
  }
}

For multiple sections, use the "sections" array:

"selectors": {},
"sections": [
  {
    "section_label": "Notifications",
    "url": "https://example.gov.in/notifications",
    "selectors": { ... }
  },
  {
    "section_label": "Circulars",
    "url": "https://example.gov.in/circulars",
    "selectors": { ... }
  }
]

================================================================================
                         COMPLEXITY LEGEND
================================================================================

LOW (Easy):
  - Static HTML pages
  - Clear table/list structure
  - No JavaScript rendering required
  - Time to configure: 10-30 minutes

MEDIUM (Standard):
  - Standard government portals
  - May have pagination
  - May use common CMS (Drupal, WordPress)
  - Time to configure: 30-60 minutes

HIGH (Difficult):
  - ASP.NET/Webforms with ViewState
  - JavaScript-heavy pages (requires render_js: true)
  - Complex pagination systems
  - Authentication or CAPTCHA
  - Time to configure: 1-3 hours

================================================================================
