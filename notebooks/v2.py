
def get_designated_countries() -> list[dict]:
    """Current '**' designated countries for sensitive charities."""
    return _load_country_list("designated_countries.json")


def get_sensitive_countries() -> list[dict]:
    """Current sensitive countries (SCAP jurisdictions) for public construction.

    Distinct tier from the charity '**' designated set - kept in its own file so
    the two policies can diverge without touching each other's logic.
    """
    return _load_country_list("sensitive_countries.json")


def _country_matcher(codes: set, names: set):
    """Build the designation/sensitivity predicate shared by the country checks.

    ISO code is the primary key (immune to naming variants such as 'Syria' vs
    'Syrian Arab Republic'); name match is the fallback when the extraction
    could not supply a code.
    """

    def is_match(ref: "CountryRef") -> bool:
        if ref.iso_alpha2 and ref.iso_alpha2.strip().upper() in codes:
            return True
        return ref.country.strip().lower() in names

    return is_match


def _country_sets(rows: list[dict]) -> tuple[set, set]:
    """Split a country list into upper-cased codes and lower-cased names."""
    return (
        {r["code"].strip().upper() for r in rows},
        {r["name"].strip().lower() for r in rows},
    )


def _extract_countries(state, schema, prompt_template):
    """Run the geographic-fact extraction for a country check.

    Returns the parsed schema instance, or None when the LLM call fails - the
    caller turns that into material Missing Information.
    """
    try:
        llm = AzureChatOpenAI(**AZURE_OPENAI_LLM_CONFIG).with_structured_output(schema)
        return llm.invoke(
            [
                HumanMessage(
                    prompt_template.format(
                        client_notes=state["client_notes"],
                        activity=state["client_activity"],
                    )
                )
            ]
        )
    except Exception:
        return None


def _finish_country_check(
    state, node_name: str, answer: str, reason: str, mi_details: dict, **fmt
):
    """Record a country-check outcome on the state.

    Reason and materiality first, answer last: result_fetcher routes on the
    LAST entry in node_outputs, so the final append must be the Yes/No/MI answer.
    """
    state["node_outputs"].append({f"{node_name}_reason": reason})
    if answer == "Missing Information":
        state["node_outputs"].append({f"{node_name}_materiality": "material"})
        state["missing_info_reasons"] = state.get("missing_info_reasons", []) + [
            mi_details[reason].format(**fmt)
        ]

    state["node_outputs"].append({node_name: answer})
    return state



=======

def decide_designated_country(
    extraction: "CharityCountryExtraction", designated_codes: set, designated_names: set
) -> tuple[str, str, List[str]]:
    """Decide the charity designated-country outcome. Pure: no LLM, no IO.


    Returns (answer, reason, unknown_designated) so the caller can render the
    MI detail text.
    """
    is_designated = _country_matcher(designated_codes, designated_names)




        return "Yes", "established_in_designated_country", []




         return "Yes", limb, []



        return "Missing Information", "countries_not_determinable", []




  return "Missing Information", "designated_share_not_stated", unknown_designated















def designated_country_check_node(state):
    """Compute node for the sensitive_charities tree (state: SIAPState).

    Fail safe, never crash: this node's failures become material Missing
    Information - an already-screened charity must surface, not error out.
    """
    node = "designated_country_check"

    try:
        codes, names = _country_sets(get_designated_countries())
    except Exception:
        return _finish_country_check(
            state, node, "Missing Information", "designated_list_unavailable",
            _MI_DETAILS, countries="",
        )

    extraction = _extract_countries(
        state, CharityCountryExtraction, CHARITY_COUNTRY_EXTRACTION_PROMPT
    )
    if extraction is None:
        return _finish_country_check(
            state, node, "Missing Information", "extraction_failed",
            _MI_DETAILS, countries="",
        )

    answer, reason, unknown = decide_designated_country(extraction, codes, names)
    return _finish_country_check(
        state, node, answer, reason, _MI_DETAILS, countries=", ".join(unknown)
    )


function_mapping["designated_country_check"] = designated_country_check_node


# ---------------------------------------------------------------------------
# P
# ---------------------------------------------------------------------------


class ConstructionCountryExtraction(BaseModel):
    contracting_government_countries: List[CountryRef] = Field(
        description="Countries whose government awarded the client's construction "
        "/ infrastructure contracts, or where those government construction "
        "projects are located; empty if not determinable from the notes"
    )
    countries_determinable: bool = Field(
        description="False when the notes do not allow determining the countries "
        "of the client's government construction contracts at all"
    )


CONSTRUCTION_COUNTRY_EXTRACTION_PROMPT = """
You are an expert compliance officer. Extract ONLY the geographic facts below from
the client notes - do not judge whether any country is sensitive or high-risk.

contracting_government_countries: every country whose government (national/central/
federal, or regional/local) awarded the client's construction or infrastructure
contracts, or where the government construction/infrastructure projects are
located. Include past and present projects. Use the most recent ISO 3166 English
country names, and give each country's ISO 3166-1 alpha-2 code (2 letters); leave
the code null if unsure. Set countries_determinable to false when the notes do not
allow determining the countries of the government construction work at all.

Client notes:
{client_notes}

Activity under assessment:
{activity}
"""

_PC_MI_DETAILS = {
    "countries_not_determinable": (
        "MATERIAL missing information: the countries of the client's government "
        "construction / infrastructure contracts cannot be determined from the "
        "notes, so the sensitive-country check cannot be assessed."
    ),
    "extraction_failed": (
        "MATERIAL missing information: the country extraction failed, so the "
        "sensitive-country check could not be assessed."
    ),
    "sensitive_list_unavailable": (
        "MATERIAL missing information: the sensitive-country list could not be "
        "loaded, so the sensitive-country check could not be assessed."
    ),
}


def decide_sensitive_country(
    extraction: "ConstructionCountryExtraction",
    sensitive_codes: set,
    sensitive_names: set,
) -> tuple[str, str]:
    """Decide the public-construction sensitive-country outcome. Pure: no LLM, no IO."""
    is_sensitive = _country_matcher(sensitive_codes, sensitive_names)

    # Guard: a 'determinable' claim with nothing extracted is not determinable.
    if (
        extraction.countries_determinable
        and not extraction.contracting_government_countries
    ):
        extraction.countries_determinable = False

    if any(is_sensitive(c) for c in extraction.contracting_government_countries):
        return "Yes", "government_in_sensitive_country"

    if not extraction.countries_determinable:
        return "Missing Information", "countries_not_determinable"

    # Countries are known and none is sensitive -> out of scope for this category.
    return "No", "no_sensitive_country_nexus"


def sensitive_country_check_node(state):
    """Compute node for the public_construction tree (state: SIAPState).

    Fail safe, never crash: this node's failures become material Missing
    Information - the category-defining criterion must surface, not error out.
    """
    node = "sensitive_country_check"

    try:
        codes, names = _country_sets(get_sensitive_countries())
    except Exception:
        return _finish_country_check(
            state, node, "Missing Information", "sensitive_list_unavailable",
            _PC_MI_DETAILS,
        )

    extraction = _extract_countries(
        state, ConstructionCountryExtraction, CONSTRUCTION_COUNTRY_EXTRACTION_PROMPT
    )
    if extraction is None:
        return _finish_country_check(
            state, node, "Missing Information", "extraction_failed", _PC_MI_DETAILS
        )

    answer, reason = decide_sensitive_country(extraction, codes, names)
    return _finish_country_check(state, node, answer, reason, _PC_MI_DETAILS)


function_mapping["sensitive_country_check"] = sensitive_country_check_node
﻿
