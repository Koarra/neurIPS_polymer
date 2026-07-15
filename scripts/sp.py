from abc import ABC
from typing import Callable, List, Optional

from langchain.messages import HumanMessage
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from main.constants import AZURE_OPENAI_LLM_CONFIG
from main.riskflag_detection.countries import SENSITIVE_CHARITIES_COUNTRIES
from main.riskflag_detection.scap_tree import SCAPGraph
from main.riskflag_detection.utils import (
    SIAPState,
    get_scap2_flag,
    llm_prompter,
    llm_prompter_full,
    output_format_mapping,
    TextAnswer,
    result_fetcher,
)

# Placeholders beyond {activity} that tree prompts may reference (e.g. the
# designated-country list injected into sensitive_charities' public-information
# check). str.format ignores unused kwargs, so passing them on every call is
# harmless for trees that do not use them.
EXTRA_PLACEHOLDERS = {
    "designated_countries_list": ", ".join(SENSITIVE_CHARITIES_COUNTRIES),
}


class CountryFlowShare(BaseModel):
    country: str = Field(
        description="Country name, using the most recent ISO 3166 English name"
    )
    share_pct: Optional[float] = Field(
        default=None,
        description="Share of the charity's total inflow/outflow value in percent, "
        "if stated in the notes; null when no share is stated",
    )


class CharityCountryExtraction(BaseModel):
    establishment_countries: List[str] = Field(
        description="Countries where the charity/NPO is registered, headquartered "
        "or constituted; empty if not determinable from the notes"
    )
    inflow_shares: List[CountryFlowShare] = Field(
        description="Countries the charity receives funding from (donations, "
        "grants, funding), with the % of total inflow value where stated"
    )
    outflow_shares: List[CountryFlowShare] = Field(
        description="Countries the charity disburses funds to (projects, grants "
        "made, programs), with the % of total outflow value where stated"
    )
    countries_determinable: bool = Field(
        description="False when the notes do not allow determining the charity's "
        "countries at all"
    )


CHARITY_COUNTRY_EXTRACTION_PROMPT = """\
You are an expert compliance officer. Extract ONLY the geographic facts below from
the client notes — do not judge whether any country is sensitive or designated.

1. establishment_countries: where the charity/NPO is registered, headquartered or
   constituted (its domicile / place of establishment).
2. inflow_shares: each country the charity receives funding from (donations,
   grants, funding), with the stated % of total inflow value; leave share_pct null
   when no share is stated.
3. outflow_shares: each country the charity disburses funds to (projects, grants
   made, programs), with the stated % of total outflow value; leave share_pct null
   when no share is stated.
Count indirect flows (funds routed via intermediaries) toward the ultimate origin
or destination country where the notes make it clear. Use the most recent
ISO 3166 English country names. Set countries_determinable to false when the
notes do not allow determining the countries at all.

Client notes:
{client_notes}

Activity under assessment:
{activity}
"""


class SIAPTree(ABC):

    def __init__(self, info: dict, function_mapping: dict):
        self.name = info["name"]
        self.field = info["field"] if "field" in info else ""

        function_mapping.update(
            {
                "scap_check": lambda s: self.scap_check_node(s),
                "scap_check_charities": lambda s: self.scap_check_node(
                    s, is_charity=True
                ),
                "designated_country_check": lambda s: self.designated_country_check_node(
                    s
                ),
            }
        )

        self.graph = self.load_graph_from_json(info, function_mapping).compile()

    def load_graph_from_json(self, info: dict, function_mapping: dict):
        graph = StateGraph(SIAPState)

        graph.add_edge(START, info["start"])

        result_nodes = {
            "siap": self.siap,
            "exit": self.exit_node,
            "missing_information": self.missing_information,
            "prohibited_relation": self.prohibited_relation,
            "cooled_down_siap": self.cooled_down_siap,
        }

        for label, pointer in result_nodes.items():
            graph.add_node(label, pointer)
            graph.add_edge(label, END)

        for label, node_info in info["nodes"].items():
            pointer = None
            transitions = node_info["transitions"]

            if node_info["type"] == "prompt_chain":
                graph.add_node(label, lambda state: state)
                graph.add_edge(label, f"{label}_1")

                for idx, prompt in enumerate(node_info["prompts"], start=1):
                    is_last: bool = idx == len(node_info["prompts"])

                    subnode_label = f"{label}_{idx}"
                    pointer = self.prompt_node(
                        subnode_label,
                        prompt,
                        output_format_mapping[node_info["output_format"]],
                    )

                    subnode_transitions = transitions.copy()
                    if not is_last:
                        for key in node_info["chain_key"]:
                            subnode_transitions[key] = f"{label}_{idx+1}"

                    graph.add_node(subnode_label, pointer)
                    fetcher = result_fetcher
                    if label == "verification" and is_last:
                        fetcher = self.verification_result_fetcher
                    graph.add_conditional_edges(
                        subnode_label, fetcher, subnode_transitions
                    )

            else:
                if node_info["type"] == "prompt":
                    pointer = self.prompt_node(
                        label,
                        node_info["text"],
                        output_format_mapping[node_info["output_format"]],
                    )
                else:
                    pointer = function_mapping[label]

                graph.add_node(label, pointer)
                graph.add_conditional_edges(label, result_fetcher, transitions)

        return graph

    def verification_result_fetcher(self, state: SIAPState):
        last = result_fetcher(state)
        if last != "No":
            return last

        has_missing = any(
            v == "Missing Information" and k.startswith("verification_")
            for output in state["node_outputs"]
            for k, v in output.items()
        )
        return "Missing Information" if has_missing else "No"

    def prompt_helper(
        self, state: SIAPState, node_label: str, prompt: str, output_format: str
    ) -> SIAPState:
        formatted_question = prompt.format(
            activity=f'"{state["client_activity"]}"',
            **EXTRA_PLACEHOLDERS,
        )
        llm_result = llm_prompter_full(
            state,
            formatted_question,
            output_format,
        )

        llm_answer = llm_result["answer"]
        state["node_outputs"].append({node_label: llm_answer})
        if llm_answer == "Missing Information":
            reasoning = llm_result.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                reason = reasoning.strip()
            else:
                reason = f"Insufficient information to answer: {formatted_question}"
            state["missing_info_reasons"] = state.get("missing_info_reasons", []) + [
                reason
            ]

        return state

    def prompt_node(
        self, node_label: str, prompt: str, output_format: str
    ) -> Callable[[SIAPState], SIAPState]:
        return lambda s: self.prompt_helper(s, node_label, prompt, output_format)

    def exit_node(self, state: SIAPState) -> SIAPState:
        state["result"] = "Not SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def missing_information(self, state: SIAPState) -> SIAPState:
        state["result"] = "Missing Information"
        reasons = state.get("missing_info_reasons", [])
        unique_reasons = []
        seen = set()
        for reason in reasons:
            if reason not in seen:
                unique_reasons.append(reason)
                seen.add(reason)

        reasons_text = (
            "; ".join(unique_reasons)
            if unique_reasons
            else "Client notes do not contain enough information to reach a determination."
        )
        prompt = "\n".join(
            [
                "Rewrite the following missing-information reasons into a single, concise justification (1-2 sentences).",
                "Use professional KYC/AML analyst language.",
                "Do not introduce assumptions.",
                "Do not add facts not present in the reasons.",
                "",
                "Reasons:",
                f"<<<\n{reasons_text}\n>>>",
            ]
        )
        summary = llm_prompter(state, prompt, TextAnswer)
        state["missing_info_summary"] = (
            summary.strip()
            if isinstance(summary, str) and summary.strip()
            else reasons_text
        )

        return state

    def siap(self, state: SIAPState) -> SIAPState:
        state["result"] = "SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def cooled_down_siap(self, state: SIAPState) -> SIAPState:
        state["result"] = "Cooled Down SIAP"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def prohibited_relation(self, state: SIAPState) -> SIAPState:
        state["result"] = "Prohibited Relation"
        state.pop("missing_info_summary", None)
        state.pop("missing_info_reasons", None)

        return state

    def designated_country_check_node(self, state: SIAPState) -> SIAPState:
        """Sensitive-charities criterion 4, limbs 1-3 (established in / 25%+
        inflow / 25%+ outflow). The LLM only extracts countries and shares;
        designation membership and the '25% or more' threshold are decided in
        code against SENSITIVE_CHARITIES_COUNTRIES. Limb 4 (public information)
        is a separate prompt node in the tree."""
        llm = AzureChatOpenAI(**AZURE_OPENAI_LLM_CONFIG).with_structured_output(
            CharityCountryExtraction
        )
        extraction: CharityCountryExtraction = llm.invoke(
            [
                HumanMessage(
                    CHARITY_COUNTRY_EXTRACTION_PROMPT.format(
                        client_notes=state["client_notes"],
                        activity=state["client_activity"],
                    )
                )
            ]
        )

        designated = {c.strip().lower() for c in SENSITIVE_CHARITIES_COUNTRIES}

        def is_designated(country: str) -> bool:
            return country.strip().lower() in designated

        answer, reason = None, None
        if any(is_designated(c) for c in extraction.establishment_countries):
            answer, reason = "Yes", "established_in_designated_country"
        else:
            for limb, flows in (
                ("designated_country_inflows", extraction.inflow_shares),
                ("designated_country_outflows", extraction.outflow_shares),
            ):
                if any(
                    f.share_pct is not None and f.share_pct >= 25
                    for f in flows
                    if is_designated(f.country)
                ):
                    answer, reason = "Yes", limb
                    break

        if answer is None:
            if not extraction.countries_determinable:
                answer, reason = "Missing Information", "countries_not_determinable"
            elif any(
                f.share_pct is None and is_designated(f.country)
                for f in extraction.inflow_shares + extraction.outflow_shares
            ):
                # A designated country appears in the flows but its share is not
                # stated: the 25% threshold cannot be confirmed either way.
                answer, reason = "Missing Information", "designated_share_not_stated"
            else:
                answer, reason = "No", "no_designated_country_nexus"

        state["node_outputs"].append({"designated_country_check": answer})
        state["node_outputs"].append({"designated_country_check_reason": reason})
        if answer == "Missing Information":
            state["missing_info_reasons"] = state.get("missing_info_reasons", []) + [
                "Designated-country nexus could not be determined: "
                + (reason or "unknown").replace("_", " ")
            ]

        return state

    def scap_check_node(self, state: SIAPState, is_charity: bool = False):
        tag = "scap_check" if not is_charity else "scap_check_charity"

        resulting_state = SCAPGraph(true_if_charity=is_charity).invoke(
            state["client_notes"], state["client_activity"]
        )
        mapping = {
            "SCAP": "Yes",
            "No SCAP": "No",
            "Missing Information": "Missing Information",
        }

        resulting_state = get_scap2_flag(resulting_state)

        state["node_outputs"].append({tag: mapping[resulting_state["scap2_flag"][0]]})

        return state
