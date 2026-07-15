
▎ "Helping Hands e.V. is a humanitarian association registered in Munich, Germany. It funds water and sanitation projects, of which approximately 40% of annual disbursements go to programs in Syria, with the remainder in Kenya and Bolivia. Funding comes primarily from German private donors (around 85%) and EU grants."

Step 1 — LLM extraction (one call, no country list, no judgment)

The node sends the notes to the model with the instruction "extract only the geographic facts, do not judge whether any country is sensitive." The model must return this exact structure:

{
  "establishment_countries": ["Germany"],
  "inflow_shares":  [{"country": "Germany", "share_pct": 85}],
  "outflow_shares": [{"country": "Syria",   "share_pct": 40},
                     {"country": "Kenya",   "share_pct": null},
                     {"country": "Bolivia", "share_pct": null}],
  "countries_determinable": true
}

Step 2 — Code decides (no LLM involved)

The function checks, in order, against SENSITIVE_CHARITIES_COUNTRIES (say Syria is on it, Germany/Kenya/Bolivia are not):

1. Established in a designated country? Germany → not on list → no.
