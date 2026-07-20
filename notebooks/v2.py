"""
Generate MRMP API-formatted JSON payloads for KMPI #1 and KMPI #2
from a completed quarterly report evaluation, and for KMPI #4
(bi-annual productivity) from a completed bi-annual report.

Output format matches GET /api/v1/kmpi_value response schema.
One file per KMPI, saved alongside the internal report.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def _quarter_start_date(quarter: str) -> str:
    """Return the first day of the quarter as YYYY-MM-DD.

    Args:
        quarter: e.g. "2026_Q1"
    """
    year, q = quarter.split("_Q")
    month = (int(q) - 1) * 3 + 1
    return f"{year}-{month:02d}-01"


def _build_payload(
    definition_id: str,
    kmpi_value_id: int,
    kmpi_eval: dict,
    quarter: str,
    delivery_id: str,
    sender_name: str,
    model_version: str,
    report_link: str,
    run_id: str,
) -> dict:
    """Build a single MRMP API payload dict for one KMPI."""
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    today = now.strftime("%Y-%m-%d")

    value = kmpi_eval["average"]
    passed = kmpi_eval["passed"]
    threshold = kmpi_eval["threshold"]
    kmpi_name = kmpi_eval["name"]  # "Entity Recall" or "Crime Recall"
    kmpi_num = kmpi_eval["kmpi"]   # "#1" or "#2"

    result = "PASS" if passed else "FAIL"
    rag_status = "Green" if passed else "Red"

    if passed:
        conclusion = (
            f"PASS: {kmpi_name} of {value:.2%} met the "
            f"{threshold:.0%} threshold across 3 months in {quarter.replace('_', ' ')}."
        )
    else:
        failed = kmpi_eval.get("failed_months", [])
        failed_str = ", ".join(m.get("month", "") for m in failed)
        conclusion = (
            f"FAIL: {kmpi_name} of {value:.2%} fell below the "
            f"{threshold:.0%} threshold in {quarter.replace('_', ' ')}. "
            f"Failed months: {failed_str}. Root cause analysis required."
        )

    return {
        definition_id: [
            {
                "kmpi_value_id": kmpi_value_id,
                "definitionId": definition_id,
                "version": model_version,
                "value_time_period": _quarter_start_date(quarter),
                "sender_name": sender_name,
                "creation_date": today,
                "value": f"{value:.4f}",
                "value_commentary": (
                    f"{kmpi_name} score for Article Detective averaged across 3 monthly "
                    f"test runs ({quarter.replace('_', ' ')}). Threshold: {threshold:.0%}."
                ),
                "link": report_link,
                "run_id": run_id,
                "threshold_hit": f"{threshold:.2f}",
                "result": result,
                "rag_status": rag_status,
                "commentary": (
                    f"KMPI {kmpi_num} measures the ability of Article Detective to correctly "
                    f"{'identify named entities (persons and companies)' if kmpi_num == '#1' else 'classify financial crimes'} "
                    f"in articles compared to reference outputs. "
                    f"A score >= {threshold:.0%} is required to pass."
                ),
                "conclusion": conclusion,
                "delivery_id": delivery_id,
                "active_flag": True,
                "upload_date": now_iso,
                "last_modified_date": now_iso,
                "last_modified_user": sender_name,
                "adhoc": False,
                "skipowner": False,
            }
        ]
    }


def _semester_start_date(period: str) -> str:
    """Return the first day of the half-year period as YYYY-MM-DD.

    Args:
        period: e.g. "2026_H1"
    """
    year, semester = period.split("_")
    month = 1 if semester == "H1" else 7
    return f"{year}-{month:02d}-01"


def generate_mrmp_productivity_json(
    report: dict,
    output_dir: Path,
    config,
) -> Path:
    """Generate and save the MRMP API JSON file for KMPI #4 (productivity).

    Unlike KMPI #1/#2, the threshold is an upper bound: the KMPI passes
    only if the average number of articles processed per person stayed
    at or below MAX_ARTICLES_PER_PERSON in every month of the half-year.

    Args:
        report: Report dict produced by biannual_productivity.py.
        output_dir: Directory where the file will be saved.
        config: The kmpi_monitoring config module.

    Returns:
        Path to the saved JSON file.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    today = now.strftime("%Y-%m-%d")

    period = report["period"]  # e.g. "2026_H1"
    period_label = period.replace("_", " ")
    passed = report["passed"]
    max_avg = max(m["avg_articles_per_person"] for m in report["monthly_values"])

    result = "PASS" if passed else "FAIL"
    rag_status = "Green" if passed else "Red"

    if passed:
        conclusion = (
            f"PASS: Monthly average of articles processed per person peaked at "
            f"{max_avg:.0f}, staying below the {config.MAX_ARTICLES_PER_PERSON} "
            f"early-warning limit in every month of {period_label}."
        )
    else:
        exceeded_str = ", ".join(
            f"{m['month']} ({m['avg']:.0f})" for m in report["exceeded_months"]
        )
        conclusion = (
            f"FAIL: Monthly average of articles processed per person exceeded the "
            f"{config.MAX_ARTICLES_PER_PERSON} early-warning limit in {period_label} "
            f"({exceeded_str}). IRR re-assessment to be triggered by the Model Owner."
        )

    payload = {
        config.MRMP_KMPI4_DEFINITION_ID: [
            {
                "kmpi_value_id": 4,
                "definitionId": config.MRMP_KMPI4_DEFINITION_ID,
                "version": config.MRMP_MODEL_VERSION,
                "value_time_period": _semester_start_date(period),
                "sender_name": config.MRMP_SENDER_NAME,
                "creation_date": today,
                "value": f"{max_avg:.0f}",
                "value_commentary": (
                    f"Highest monthly average of articles processed per GFIU team "
                    f"member during {period_label}. Baseline: "
                    f"{config.BASELINE_ARTICLES_PER_PERSON} articles/person/month; "
                    f"early-warning limit: {config.MAX_ARTICLES_PER_PERSON} "
                    f"(100% increase)."
                ),
                "link": config.MRMP_REPORT_LINK,
                "run_id": f"run_{period}",
                "threshold_hit": f"{config.MAX_ARTICLES_PER_PERSON}",
                "result": result,
                "rag_status": rag_status,
                "commentary": (
                    "KMPI #4 monitors the average number of articles processed per "
                    "person per month by GFIU team members as an early-warning "
                    "indicator for materiality re-assessment. The manual baseline "
                    f"is estimated at {config.BASELINE_ARTICLES_PER_PERSON} "
                    "articles/person/month; if any month of the half-year exceeds a "
                    f"100% increase ({config.MAX_ARTICLES_PER_PERSON} articles), the "
                    "KMPI fails and an IRR re-assessment is triggered by the Model "
                    "Owner."
                ),
                "conclusion": conclusion,
                "delivery_id": config.MRMP_DELIVERY_ID_KMPI4,
                "active_flag": True,
                "upload_date": now_iso,
                "last_modified_date": now_iso,
                "last_modified_user": config.MRMP_SENDER_NAME,
                "adhoc": False,
                "skipowner": False,
            }
        ]
    }

    file = output_dir / f"mrmp_{config.MRMP_KMPI4_DEFINITION_ID}_{period}.json"
    with open(file, "w") as f:
        json.dump(payload, f, indent=2)

    return file


def generate_mrmp_api_json(
    kmpi1: dict,
    kmpi2: dict,
    quarter: str,
    output_dir: Path,
    config,
) -> tuple[Path, Path]:
    """Generate and save MRMP API JSON files for KMPI #1 and KMPI #2.

    Args:
        kmpi1: Result dict from evaluate_entity_recall().
        kmpi2: Result dict from evaluate_crime_recall().
        quarter: Quarter string, e.g. "2026_Q1".
        output_dir: Directory where files will be saved (quarterly reports folder).
        config: The kmpi_monitoring config module.

    Returns:
        Tuple of (path_to_kmpi1_file, path_to_kmpi2_file).
    """
    run_id = f"run_{quarter}"

    payload1 = _build_payload(
        definition_id=config.MRMP_KMPI1_DEFINITION_ID,
        kmpi_value_id=1,
        kmpi_eval=kmpi1,
        quarter=quarter,
        delivery_id=config.MRMP_DELIVERY_ID_KMPI1,
        sender_name=config.MRMP_SENDER_NAME,
        model_version=config.MRMP_MODEL_VERSION,
        report_link=config.MRMP_REPORT_LINK,
        run_id=run_id,
    )

    payload2 = _build_payload(
        definition_id=config.MRMP_KMPI2_DEFINITION_ID,
        kmpi_value_id=2,
        kmpi_eval=kmpi2,
        quarter=quarter,
        delivery_id=config.MRMP_DELIVERY_ID_KMPI2,
        sender_name=config.MRMP_SENDER_NAME,
        model_version=config.MRMP_MODEL_VERSION,
        report_link=config.MRMP_REPORT_LINK,
        run_id=run_id,
    )

    file1 = output_dir / f"mrmp_{config.MRMP_KMPI1_DEFINITION_ID}_{quarter}.json"
    file2 = output_dir / f"mrmp_{config.MRMP_KMPI2_DEFINITION_ID}_{quarter}.json"

    with open(file1, "w") as f:
        json.dump(payload1, f, indent=2)

    with open(file2, "w") as f:
        json.dump(payload2, f, indent=2)

    return file1, file2
