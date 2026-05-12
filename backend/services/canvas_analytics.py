"""
Canvas analytics computations.
Grade Surgeon (Phase 2) and Momentum Score (Phase 1) live here.
"""

import json
from typing import Optional


def parse_gradebook(gradebook_json) -> dict:
    """Parse gradebook JSON string into a dict."""
    if not gradebook_json:
        return {}
    if isinstance(gradebook_json, str):
        return json.loads(gradebook_json)
    return gradebook_json


# ==============================================================================
# MOMENTUM SCORE
# ==============================================================================

CLASSIFICATION_PACE = {
    "Freshman": 0.25, "Sophomore": 0.50, "Junior": 0.75, "Senior": 0.95,
}

def compute_momentum_score(canvas_dict: Optional[dict], dw_dict: Optional[dict], banner_dict: Optional[dict] = None) -> dict:
    """Compute academic momentum score (0-100) from available data sources.

    Factors:
    - Timeliness (25): % of assignments submitted on time
    - Trajectory (25): current performance vs historical GPA
    - Credit Pace (20): progress toward degree adjusted for classification
    - Workload (20): assignment completion ratio
    - Missing Penalty (-10): deductions for missing work

    Gracefully degrades when data sources are missing.
    """
    factors = {}
    sources = []
    max_possible = 0

    # --- TIMELINESS (max 25) ---
    if canvas_dict and canvas_dict.get("gradebook"):
        gradebook = parse_gradebook(canvas_dict["gradebook"])
        on_time = 0
        total_submitted = 0
        for cid, data in gradebook.items():
            for a in data.get("assignments", []):
                sub = a.get("submission") or {}
                if sub.get("score") is not None and sub.get("workflow_state") != "pending_review":
                    total_submitted += 1
                    if not sub.get("late", False):
                        on_time += 1

        if total_submitted > 0:
            pct = on_time / total_submitted
            score = round(pct * 25, 1)
            factors["timeliness"] = {
                "score": score, "max": 25,
                "detail": f"{round(pct * 100)}% on-time ({on_time}/{total_submitted})",
            }
        else:
            factors["timeliness"] = {"score": 15, "max": 25, "detail": "No submissions yet"}
        max_possible += 25
        sources.append("canvas")
    else:
        factors["timeliness"] = {"score": None, "max": 25, "detail": "Canvas not connected"}

    # --- TRAJECTORY (max 25) ---
    current_avg = None
    if canvas_dict and canvas_dict.get("courses"):
        courses = json.loads(canvas_dict["courses"]) if isinstance(canvas_dict["courses"], str) else canvas_dict["courses"]
        scores = [c.get("current_score") for c in courses if c.get("current_score") is not None]
        if scores:
            current_avg = sum(scores) / len(scores)

    historical_gpa = None
    if dw_dict and dw_dict.get("overall_gpa"):
        historical_gpa = float(dw_dict["overall_gpa"])
    elif banner_dict and banner_dict.get("cumulative_gpa"):
        historical_gpa = float(banner_dict["cumulative_gpa"])

    if current_avg is not None:
        if historical_gpa is not None:
            # Convert GPA to percentage scale for comparison
            hist_pct = (historical_gpa / 4.0) * 100
            diff = current_avg - hist_pct
            if diff > 5:
                score = 25
                detail = f"Trending up (+{diff:.1f}%)"
            elif diff > -5:
                score = 20
                detail = f"Stable ({diff:+.1f}%)"
            else:
                score = max(8, 20 + diff)
                detail = f"Trending down ({diff:.1f}%)"
            factors["trajectory"] = {"score": round(score, 1), "max": 25, "detail": detail}
        else:
            # No historical data, use current avg directly
            score = min(25, (current_avg / 100) * 25)
            factors["trajectory"] = {"score": round(score, 1), "max": 25, "detail": f"Current avg {current_avg:.1f}%"}
        max_possible += 25
        if "canvas" not in sources:
            sources.append("canvas")
    else:
        factors["trajectory"] = {"score": None, "max": 25, "detail": "No grade data"}

    # --- CREDIT PACE (max 20) ---
    if dw_dict and dw_dict.get("total_credits_earned"):
        earned = float(dw_dict["total_credits_earned"])
        required = float(dw_dict.get("credits_required") or 120)  # default 120 for BS
        classification = dw_dict.get("classification", "")

        expected_pct = CLASSIFICATION_PACE.get(classification, 0.5)
        actual_pct = earned / required if required > 0 else 0
        ratio = min(1.2, actual_pct / expected_pct) if expected_pct > 0 else 1.0
        score = min(20, round(ratio * 20, 1))

        factors["credit_pace"] = {
            "score": score, "max": 20,
            "detail": f"{earned:.0f}/{required:.0f} credits ({actual_pct * 100:.0f}%)",
        }
        max_possible += 20
        if "degreeworks" not in sources:
            sources.append("degreeworks")
    else:
        factors["credit_pace"] = {"score": None, "max": 20, "detail": "DegreeWorks not connected"}

    # --- WORKLOAD MANAGEMENT (max 20) ---
    if canvas_dict and canvas_dict.get("gradebook"):
        gradebook = parse_gradebook(canvas_dict["gradebook"])
        total = 0
        completed = 0
        for cid, data in gradebook.items():
            for a in data.get("assignments", []):
                sub = a.get("submission") or {}
                pp = a.get("points_possible") or 0
                if pp > 0:  # skip extra credit
                    total += 1
                    if sub.get("score") is not None or sub.get("submitted_at"):
                        completed += 1

        if total > 0:
            ratio = completed / total
            score = round(ratio * 20, 1)
            factors["workload"] = {
                "score": score, "max": 20,
                "detail": f"{round(ratio * 100)}% completion ({completed}/{total})",
            }
        else:
            factors["workload"] = {"score": 15, "max": 20, "detail": "No assignments yet"}
        max_possible += 20
    else:
        factors["workload"] = {"score": None, "max": 20, "detail": "Canvas not connected"}

    # --- MISSING PENALTY (max -10) ---
    missing_count = 0
    if canvas_dict and canvas_dict.get("missing_assignments"):
        missing = json.loads(canvas_dict["missing_assignments"]) if isinstance(canvas_dict["missing_assignments"], str) else canvas_dict["missing_assignments"]
        missing_count = len(missing)

    penalty = min(10, missing_count * 2)
    factors["missing_penalty"] = {
        "score": -penalty, "max": 0,
        "detail": f"{missing_count} missing" if missing_count > 0 else "None",
    }

    # --- COMPUTE TOTAL ---
    available_scores = [f["score"] for f in factors.values() if f["score"] is not None]
    if not available_scores:
        return {"score": None, "breakdown": factors, "trend": None, "sources": [], "factors_available": 0}

    # Scale available factors to fill 100
    raw_sum = sum(available_scores)
    raw_max = sum(f["max"] for f in factors.values() if f["score"] is not None and f["max"] > 0)
    if raw_max > 0:
        total = max(0, min(100, (raw_sum / raw_max) * 100))
    else:
        total = max(0, min(100, raw_sum))

    # Determine trend
    trajectory = factors.get("trajectory", {})
    trend = None
    if trajectory.get("detail", "").startswith("Trending up"):
        trend = "up"
    elif trajectory.get("detail", "").startswith("Trending down"):
        trend = "down"
    elif trajectory.get("score") is not None:
        trend = "stable"

    return {
        "score": round(total, 1),
        "breakdown": factors,
        "trend": trend,
        "sources": sources,
        "factors_available": len(available_scores),
    }


def _get_letter_grade(score: float) -> str:
    """Convert percentage score to letter grade."""
    if score >= 93: return "A"
    if score >= 90: return "A-"
    if score >= 87: return "B+"
    if score >= 83: return "B"
    if score >= 80: return "B-"
    if score >= 77: return "C+"
    if score >= 73: return "C"
    if score >= 70: return "C-"
    if score >= 67: return "D+"
    if score >= 60: return "D"
    return "F"


GRADE_TARGETS = {
    "for_A": 90.0,
    "for_B": 80.0,
    "for_C": 70.0,
}


def analyze_course_grade(course_gradebook: dict, course_name: str = "") -> dict:
    """Full grade analysis for a single course.

    Args:
        course_gradebook: {grading_type, assignment_groups, assignments}
        course_name: display name for the course

    Returns:
        Complete grade analysis with what-I-need calculator, performance DNA, strategies.
    """
    grading_type = course_gradebook.get("grading_type", "total_points")
    groups = course_gradebook.get("assignment_groups", [])
    assignments = course_gradebook.get("assignments", [])

    # Build group lookup
    group_map = {}
    for g in groups:
        group_map[g["id"]] = {
            "id": g["id"],
            "name": g["name"],
            "weight": g.get("weight", 0),
            "assignments": [],
            "graded_earned": 0.0,
            "graded_possible": 0.0,
            "remaining_possible": 0.0,
            "graded_count": 0,
            "total_count": 0,
        }

    # Categorize assignments into groups
    for a in assignments:
        gid = a.get("assignment_group_id")
        if gid not in group_map:
            continue

        sub = a.get("submission") or {}
        score = sub.get("score")
        submitted_at = sub.get("submitted_at")
        workflow = sub.get("workflow_state", "")
        points = a.get("points_possible") or 0
        is_extra_credit = (points == 0 and score is not None and score > 0)

        # Determine status
        # pending_review means score exists but professor hasn't finalized it
        if score is not None and workflow not in ("pending_review",):
            status = "graded"
        elif score is not None and workflow == "pending_review":
            status = "pending_review"
        elif submitted_at:
            status = "submitted"
        elif sub.get("missing"):
            status = "missing"
        else:
            status = "upcoming"

        entry = {
            "name": a.get("name", ""),
            "score": score,
            "points_possible": points,
            "status": status,
            "due_at": a.get("due_at"),
            "late": sub.get("late", False),
            "missing": sub.get("missing", False),
            "extra_credit": is_extra_credit,
        }
        group_map[gid]["assignments"].append(entry)
        group_map[gid]["total_count"] += 1

        if status == "graded":
            # Extra credit: earned points count, but 0 possible stays 0
            # This allows grades above 100% (matching Canvas behavior)
            group_map[gid]["graded_earned"] += score
            group_map[gid]["graded_possible"] += points  # 0 for extra credit
            group_map[gid]["graded_count"] += 1
        elif status in ("upcoming", "missing") and points > 0:
            group_map[gid]["remaining_possible"] += points

    # Calculate current grade
    if grading_type == "weighted":
        current_score = _calc_weighted_grade(group_map)
        what_i_need = _calc_weighted_targets(group_map, current_score)
    else:
        current_score = _calc_total_points_grade(group_map)
        what_i_need = _calc_total_points_targets(group_map)

    # Performance DNA: avg score per group type
    performance_dna = {}
    for g in group_map.values():
        if g["graded_possible"] > 0:
            avg = (g["graded_earned"] / g["graded_possible"]) * 100
            performance_dna[g["name"]] = round(avg, 1)

    # Strategy recommendations
    strategies = _build_strategies(group_map, performance_dna, grading_type)

    # Build response
    group_list = []
    for g in group_map.values():
        current_avg = None
        if g["graded_possible"] > 0:
            current_avg = round((g["graded_earned"] / g["graded_possible"]) * 100, 1)
        group_list.append({
            "id": g["id"],
            "name": g["name"],
            "weight": g["weight"],
            "current_avg": current_avg,
            "graded_count": g["graded_count"],
            "total_count": g["total_count"],
            "assignments": g["assignments"],
        })

    return {
        "course_name": course_name,
        "grading_type": grading_type,
        "current_grade": {
            "score": round(current_score, 1) if current_score is not None else None,
            "letter": _get_letter_grade(current_score) if current_score is not None else None,
        },
        "assignment_groups": group_list,
        "what_i_need": what_i_need,
        "performance_dna": performance_dna,
        "strategies": strategies,
        "stats": {
            "total_assignments": sum(g["total_count"] for g in group_map.values()),
            "graded": sum(g["graded_count"] for g in group_map.values()),
            "missing": sum(1 for g in group_map.values() for a in g["assignments"] if a["status"] == "missing"),
            "upcoming": sum(1 for g in group_map.values() for a in g["assignments"] if a["status"] == "upcoming"),
            "pending_review": sum(1 for g in group_map.values() for a in g["assignments"] if a["status"] == "pending_review"),
            "extra_credit": sum(1 for g in group_map.values() for a in g["assignments"] if a.get("extra_credit")),
        },
    }


def _calc_weighted_grade(group_map: dict) -> Optional[float]:
    """Calculate current weighted grade. Only counts groups with graded work."""
    weighted_sum = 0.0
    weight_used = 0.0
    for g in group_map.values():
        if g["graded_possible"] > 0 and g["weight"] > 0:
            group_pct = (g["graded_earned"] / g["graded_possible"]) * 100
            weighted_sum += group_pct * g["weight"]
            weight_used += g["weight"]
    if weight_used == 0:
        return None
    # Scale to account for ungraded weight
    return weighted_sum / weight_used


def _calc_total_points_grade(group_map: dict) -> Optional[float]:
    """Calculate grade as total earned / total possible."""
    total_earned = sum(g["graded_earned"] for g in group_map.values())
    total_possible = sum(g["graded_possible"] for g in group_map.values())
    if total_possible == 0:
        return None
    return (total_earned / total_possible) * 100


def _calc_weighted_targets(group_map: dict, current: Optional[float]) -> dict:
    """Calculate required average on remaining work for each target grade (weighted)."""
    if current is None:
        return {k: {"required_avg": None, "achievable": False} for k in GRADE_TARGETS}

    # Calculate weight of graded vs remaining
    graded_weight = 0.0
    remaining_weight = 0.0
    weighted_earned = 0.0

    for g in group_map.values():
        if g["weight"] <= 0:
            continue
        if g["graded_possible"] > 0:
            group_pct = (g["graded_earned"] / g["graded_possible"]) * 100
            weighted_earned += group_pct * g["weight"]
            graded_weight += g["weight"]
        if g["remaining_possible"] > 0:
            remaining_weight += g["weight"]

    # Some weight may overlap (group has both graded and remaining)
    # Use total weight minus pure-graded weight for remaining
    total_weight = sum(g["weight"] for g in group_map.values() if g["weight"] > 0)

    result = {}
    for key, target in GRADE_TARGETS.items():
        if remaining_weight <= 0:
            result[key] = {
                "required_avg": None,
                "achievable": current >= target,
            }
        else:
            # target = (weighted_earned + required_avg * remaining_weight) / total_weight
            needed = ((target * total_weight) - weighted_earned) / remaining_weight
            result[key] = {
                "required_avg": round(needed, 1),
                "achievable": needed <= 100,
            }
    return result


def _calc_total_points_targets(group_map: dict) -> dict:
    """Calculate required scores for each target grade (total points)."""
    total_earned = sum(g["graded_earned"] for g in group_map.values())
    total_possible_graded = sum(g["graded_possible"] for g in group_map.values())
    total_remaining = sum(g["remaining_possible"] for g in group_map.values())
    total_all = total_possible_graded + total_remaining

    result = {}
    for key, target in GRADE_TARGETS.items():
        if total_remaining <= 0:
            result[key] = {
                "required_avg": None,
                "achievable": total_all > 0 and (total_earned / total_all * 100) >= target,
            }
        else:
            needed_points = (target / 100) * total_all - total_earned
            required_pct = (needed_points / total_remaining) * 100
            result[key] = {
                "required_avg": round(required_pct, 1),
                "achievable": required_pct <= 100,
            }
    return result


def _build_strategies(group_map: dict, dna: dict, grading_type: str) -> list:
    """Build actionable strategy recommendations."""
    strategies = []

    if not dna:
        return strategies

    # Find strongest and weakest areas
    sorted_dna = sorted(dna.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_dna) >= 2:
        strongest_name, strongest_pct = sorted_dna[0]
        weakest_name, weakest_pct = sorted_dna[-1]

        if strongest_pct - weakest_pct > 5:
            strategies.append({
                "action": f"Focus on {weakest_name} assignments",
                "reason": f"Your {weakest_name} average ({weakest_pct}%) is your weakest area vs {strongest_name} ({strongest_pct}%)",
                "impact": "Improving your weakest category has the highest grade impact",
            })

    # Find high-weight groups with remaining work
    for g in group_map.values():
        remaining_count = sum(1 for a in g["assignments"] if a["status"] in ("upcoming", "missing"))
        if remaining_count > 0 and g["weight"] >= 10:
            group_avg = dna.get(g["name"])
            if group_avg and group_avg >= 85:
                strategies.append({
                    "action": f"Crush the remaining {remaining_count} {g['name']} assignments ({g['weight']}% weight)",
                    "reason": f"You average {group_avg}% in {g['name']} and it's worth {g['weight']}% of your grade",
                    "impact": f"High weight + your strength = maximum grade boost",
                })

    # Flag missing work
    for g in group_map.values():
        missing = [a for a in g["assignments"] if a["status"] == "missing"]
        if missing:
            points = sum(a["points_possible"] or 0 for a in missing)
            strategies.append({
                "action": f"Recover {len(missing)} missing {g['name']} assignment(s)",
                "reason": f"Missing {points} points in {g['name']}",
                "impact": "Recovering missing work is the easiest grade boost",
            })

    return strategies[:5]  # Cap at 5 recommendations


def get_all_courses_summary(gradebook: dict, courses: list) -> list:
    """Lightweight summary of all courses for the dashboard."""
    course_name_map = {}
    for c in courses:
        course_name_map[str(c.get("id", ""))] = c.get("name", "Unknown")

    summaries = []
    for cid, gb_data in gradebook.items():
        analysis = analyze_course_grade(gb_data, course_name_map.get(str(cid), "Unknown"))
        summaries.append({
            "course_id": cid,
            "course_name": analysis["course_name"],
            "grading_type": analysis["grading_type"],
            "current_grade": analysis["current_grade"],
            "what_i_need": analysis["what_i_need"],
            "stats": analysis["stats"],
        })
    return summaries
