"""Requirement-based validation engine (explainable, NOT machine learning).

Every check returns an outcome that names the rule, the code reference, the requirement
applied, the observed value and a plain-language explanation. The overall status is the
worst outcome: VERIFIED < REVIEW_REQUIRED < FLAGGED. Missing/invalid data never passes
silently - it produces REVIEW_REQUIRED.
"""
import json
from pathlib import Path
from statistics import mean

RULES = json.loads((Path(__file__).resolve().parent.parent / "rules" / "is_concrete.json").read_text())

SEV = {"VERIFIED": 0, "REVIEW_REQUIRED": 1, "FLAGGED": 2}


def worst(statuses) -> str:
    statuses = list(statuses)
    return max(statuses, key=lambda s: SEV[s]) if statuses else "VERIFIED"


def fck_of(grade: str) -> float:
    return float(grade.strip().upper().lstrip("M"))


def norm_exposure(e: str | None) -> str:
    return (e or "moderate").strip().lower().replace(" ", "_").replace("-", "_")


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def _o(rule_id, title, reference, requirement, observed, status, explanation, **extra):
    d = {"rule_id": rule_id, "title": title, "reference": reference, "requirement": requirement,
         "observed": observed, "status": status, "explanation": explanation}
    d.update(extra)
    return d


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ───────────────────────── sampling & thresholds ─────────────────────────
def required_samples(quantity_m3: float) -> int:
    cfg = RULES["sampling_frequency"]
    if quantity_m3 <= 0:
        return 0
    for step in cfg["steps"]:
        if quantity_m3 <= step["upto_m3"]:
            return step["samples"]
    extra = quantity_m3 - 50
    import math
    return cfg["beyond"]["base_samples"] + math.ceil(extra / cfg["beyond"]["per_extra_m3"])


def assumed_sd(grade: str) -> float:
    return RULES["assumed_sd_mpa"].get(grade.upper(), 5.0)


def individual_min(fck: float) -> float:
    m = RULES["cube"]["individual_margin_mpa"]
    return fck - (m["le_M15"] if fck <= 15 else m["ge_M20"])


def group_threshold(fck: float, grade: str) -> float:
    m = RULES["cube"]["group_margin_mpa"]
    margin = m["le_M15"] if fck <= 15 else m["ge_M20"]
    sd_term = _round_half(RULES["cube"]["group_sd_factor"] * assumed_sd(grade))
    return fck + max(sd_term, margin)


def sample_mean(values: dict) -> float | None:
    specs = (values or {}).get("specimens_mpa")
    if isinstance(specs, list) and specs and all(_is_num(x) and x > 0 for x in specs):
        return sum(specs) / len(specs)
    return None


# ───────────────────────── batch registration ─────────────────────────
def registration_check(batch) -> tuple[str, list[dict]]:
    exp_key = norm_exposure(batch.exposure)
    table = RULES["exposure"].get(exp_key)
    outs = []
    if not table:
        return "REVIEW_REQUIRED", [_o("exposure_unknown", "Exposure class", "IS 456 Table 5", "One of: " + ", ".join(RULES["exposure"]),
                                      batch.exposure, "REVIEW_REQUIRED", "Exposure class not recognised; limits could not be applied.")]
    lim = table["pcc" if batch.construction_type == "pcc" else "rcc"]
    ref = "IS 456:2000 Table 5"
    if lim["min_grade"]:
        need = fck_of(lim["min_grade"])
        ok = batch.fck >= need
        outs.append(_o("min_grade_for_exposure", "Minimum grade for exposure", ref, f">= {lim['min_grade']} for {exp_key} exposure",
                       batch.grade, "VERIFIED" if ok else "FLAGGED",
                       "Grade meets the minimum for this exposure." if ok else
                       f"{batch.grade} is below the {lim['min_grade']} minimum for {exp_key} exposure; check design intent."))
    if batch.wc_ratio is not None:
        ok = batch.wc_ratio <= lim["max_wc"]
        outs.append(_o("max_wc_ratio", "Maximum free water-cement ratio", ref, f"<= {lim['max_wc']}", batch.wc_ratio,
                       "VERIFIED" if ok else "FLAGGED",
                       "w/c ratio within limit." if ok else "w/c ratio exceeds the limit for this exposure."))
    else:
        outs.append(_o("max_wc_ratio", "Maximum free water-cement ratio", ref, f"<= {lim['max_wc']}", None, "REVIEW_REQUIRED",
                       "w/c ratio not recorded; obtain it from the mix design / delivery ticket."))
    if batch.cement_content_kg_m3 is not None:
        ok = batch.cement_content_kg_m3 >= lim["min_cement_kg_m3"]
        outs.append(_o("min_cement_content", "Minimum cement content", ref, f">= {lim['min_cement_kg_m3']} kg/m3", batch.cement_content_kg_m3,
                       "VERIFIED" if ok else "FLAGGED",
                       "Cement content meets the minimum." if ok else "Cement content is below the minimum for this exposure."))
    else:
        outs.append(_o("min_cement_content", "Minimum cement content", ref, f">= {lim['min_cement_kg_m3']} kg/m3", None, "REVIEW_REQUIRED",
                       "Cement content not recorded; obtain it from the mix design / delivery ticket."))
    return worst(o["status"] for o in outs), outs


# ───────────────────────── individual test rules ─────────────────────────
def _slump(values, batch, **_):
    cfg = RULES["slump"]
    v = values.get("slump_mm")
    ref = "IS 456:2000 Cl. 7.1 (workability); IS 1199 (test method)"
    if not _is_num(v) or v < 0 or v > 300:
        return [_o("slump_range", "Slump within placing range", ref, "numeric slump_mm (0-300)", v, "REVIEW_REQUIRED",
                   "Slump missing or not a plausible number.")]
    cond = values.get("placing_condition")
    assumed = cond not in cfg["ranges"]
    if assumed:
        cond = cfg["default_condition"]
    lo, hi = cfg["ranges"][cond]
    tol = cfg["tolerance_mm"]
    note = f" (placing condition not given, assumed '{cond}')" if assumed else ""
    if lo <= v <= hi:
        st, ex = "VERIFIED", f"Slump {v} mm is within {lo}-{hi} mm for '{cond}'{note}."
    elif lo - tol <= v <= hi + tol:
        st, ex = "REVIEW_REQUIRED", f"Slump {v} mm is outside {lo}-{hi} mm for '{cond}' but within the review band{note}. Engineer to decide before placing."
    else:
        st, ex = "FLAGGED", f"Slump {v} mm is well outside {lo}-{hi} mm for '{cond}'{note}. Hold the batch and investigate."
    return [_o("slump_range", "Slump within placing range", ref, f"{lo}-{hi} mm ({cond})", v, st, ex)]


def _temperature(values, batch, **_):
    cfg = RULES["fresh_temperature_c"]
    t = values.get("temperature_c")
    ref = "IS 7861 (Part 1 & 2) / project specification"
    if not _is_num(t) or t < -20 or t > 80:
        return [_o("fresh_temp", "Fresh concrete temperature", ref, f"{cfg['min']}-{cfg['max']} C", t, "REVIEW_REQUIRED",
                   "Temperature missing or implausible.")]
    band = cfg["review_band"]
    if cfg["min"] <= t <= cfg["max"]:
        st, ex = "VERIFIED", f"{t} C is within {cfg['min']}-{cfg['max']} C."
        if t > cfg["max"] - band or t < cfg["min"] + band:
            st, ex = "REVIEW_REQUIRED", f"{t} C is within limits but close to the edge; consider weather precautions."
    elif cfg["min"] - band <= t <= cfg["max"] + band:
        st, ex = "REVIEW_REQUIRED", f"{t} C is slightly outside {cfg['min']}-{cfg['max']} C."
    else:
        st, ex = "FLAGGED", f"{t} C is well outside {cfg['min']}-{cfg['max']} C."
    return [_o("fresh_temp", "Fresh concrete temperature", ref, f"{cfg['min']}-{cfg['max']} C", t, st, ex)]


def _cube(values, batch, age_days=None, prior_28d_means=None, **_):
    cube = RULES["cube"]
    fck = batch.fck
    outs = []
    specs = values.get("specimens_mpa")
    if not isinstance(specs, list) or not specs or not all(_is_num(x) and 0 < x <= 150 for x in specs):
        return [_o("cube_specimens", "Specimen results", "IS 456:2000 Cl. 15.4", "3 valid specimen strengths (N/mm2)", specs,
                   "REVIEW_REQUIRED", "Specimen strengths missing or implausible; re-enter or retest.")]
    m = sum(specs) / len(specs)
    if len(specs) != cube["specimens_per_sample"]:
        outs.append(_o("cube_count", "Three specimens per sample", "IS 456:2000 Cl. 15.4", "3 specimens", len(specs), "REVIEW_REQUIRED",
                       f"{len(specs)} specimen(s) given; a sample result is the average of three."))
    dev = max(abs(x - m) / m for x in specs)
    if dev > cube["max_specimen_deviation"]:
        outs.append(_o("cube_variation", "Specimen variation within 15 percent", "IS 456:2000 Cl. 15.4", "each within +/-15% of average",
                       f"max deviation {dev*100:.1f}%", "REVIEW_REQUIRED",
                       "One specimen deviates more than 15% from the average: the sample result is invalid. Check casting/curing/crushing and retest."))
        return outs
    outs.append(_o("cube_variation", "Specimen variation within 15 percent", "IS 456:2000 Cl. 15.4", "each within +/-15% of average",
                   f"max deviation {dev*100:.1f}%", "VERIFIED", "Specimens are consistent."))
    ref = "IS 456:2000 Cl. 16.1 & Table 11"
    if age_days == 28:
        ind = individual_min(fck)
        thr = group_threshold(fck, batch.grade)
        seq = list(prior_28d_means or []) + [m]
        n = len(seq)
        if m < ind:
            outs.append(_o("cube_individual_28d", "Individual result >= fck - margin", ref, f">= {ind:.1f} N/mm2", round(m, 2), "FLAGGED",
                           f"Mean {m:.1f} N/mm2 is below the individual limit {ind:.1f} N/mm2 for {batch.grade}."))
        elif n % cube["group_size"] == 0:
            grp = mean(seq[-cube["group_size"]:])
            ok = grp >= thr
            outs.append(_o("cube_individual_28d", "Individual result >= fck - margin", ref, f">= {ind:.1f} N/mm2", round(m, 2), "VERIFIED",
                           "Individual criterion met."))
            outs.append(_o("cube_group_28d", "Group of 4 mean >= fck + margin", ref, f">= {thr:.1f} N/mm2", round(grp, 2),
                           "VERIFIED" if ok else "FLAGGED",
                           f"Mean of last 4 results {grp:.1f} N/mm2 " + ("meets" if ok else "does NOT meet") + f" the group criterion {thr:.1f} N/mm2.",
                           group_results=[round(x, 2) for x in seq[-cube['group_size']:]]))
        else:
            outs.append(_o("cube_individual_28d", "Individual result >= fck - margin", ref, f">= {ind:.1f} N/mm2", round(m, 2), "VERIFIED",
                           "Individual criterion met."))
            if m >= thr:
                outs.append(_o("cube_group_28d", "Group of 4 mean >= fck + margin", ref, f">= {thr:.1f} N/mm2", round(m, 2), "VERIFIED",
                               f"This result alone already exceeds the group threshold {thr:.1f}; formal group check after {cube['group_size'] - n % cube['group_size']} more result(s).",
                               group_pending=True))
            else:
                outs.append(_o("cube_group_28d", "Group of 4 mean >= fck + margin", ref, f">= {thr:.1f} N/mm2", round(m, 2), "REVIEW_REQUIRED",
                               f"Result {m:.1f} is acceptable individually but below the group threshold {thr:.1f}; await the group of 4 ({n % cube['group_size']} of 4 so far) and engineer review.",
                               group_pending=True))
    else:
        frac = cube["early_age_min_fraction"].get(str(age_days))
        if frac is not None:
            need = frac * fck
            ok = m >= need
            outs.append(_o("cube_early_age", f"{age_days}-day indicative strength", "IS 516 / IS 456 (indicative, not an acceptance criterion)",
                           f">= {frac*100:.0f}% of fck = {need:.1f} N/mm2", round(m, 2), "VERIFIED" if ok else "REVIEW_REQUIRED",
                           f"{age_days}-day mean {m:.1f} N/mm2 " + ("is on track" if ok else "is below the usual expectation") +
                           "; acceptance is decided at 28 days."))
        else:
            ok = m >= fck
            outs.append(_o("cube_other_age", f"{age_days}-day strength" if age_days else "Strength (age missing)", ref, f">= fck {fck:.0f} N/mm2", round(m, 2),
                           "VERIFIED" if ok else "REVIEW_REQUIRED",
                           "Meets specified strength." if ok else "Below specified strength; engineer review (age missing or non-standard)."))
    return outs


def _core(values, batch, **_):
    cfg = RULES["core"]
    cores = values.get("cores_equiv_cube_mpa")
    ref = "IS 456:2000 Cl. 17.4"
    if not isinstance(cores, list) or not cores or not all(_is_num(x) and x > 0 for x in cores):
        return [_o("core_strength", "Core acceptance", ref, "equivalent cube strengths of cores", cores, "REVIEW_REQUIRED",
                   "Core results missing or invalid.")]
    m = sum(cores) / len(cores)
    mean_need = cfg["mean_min_fraction"] * batch.fck
    ind_need = cfg["individual_min_fraction"] * batch.fck
    outs = []
    if len(cores) < cfg["min_cores"]:
        outs.append(_o("core_count", "Minimum number of cores", ref, f">= {cfg['min_cores']} cores", len(cores), "REVIEW_REQUIRED",
                       "Fewer cores than the usual minimum; engineer to confirm adequacy."))
    ok = m >= mean_need and min(cores) >= ind_need
    outs.append(_o("core_strength", "Core acceptance", ref, f"mean >= {mean_need:.1f} (85% fck) and each >= {ind_need:.1f} (75% fck) N/mm2",
                   f"mean {m:.1f}, min {min(cores):.1f}", "VERIFIED" if ok else "FLAGGED",
                   "Cores satisfy the acceptance criterion." if ok else "Cores do not satisfy the acceptance criterion; structural assessment is needed."))
    return outs


def _upv(values, batch, **_):
    cfg = RULES["upv_km_s"]
    v = values.get("velocity_km_s")
    ref = "IS 13311 (Part 1)"
    if not _is_num(v) or v <= 0 or v > 8:
        return [_o("upv_quality", "Concrete quality by pulse velocity", ref, ">= 3.5 km/s good", v, "REVIEW_REQUIRED", "Velocity missing or implausible.")]
    if v >= cfg["good_min"]:
        st, ex = "VERIFIED", f"{v} km/s indicates good/excellent concrete."
    elif v >= cfg["medium_min"]:
        st, ex = "REVIEW_REQUIRED", f"{v} km/s indicates medium (doubtful) quality; correlate with other tests."
    else:
        st, ex = "FLAGGED", f"{v} km/s indicates poor/doubtful concrete; follow up with cores."
    return [_o("upv_quality", "Concrete quality by pulse velocity", ref, f">= {cfg['good_min']} km/s good; {cfg['medium_min']}-{cfg['good_min']} medium",
               v, st, ex)]


def _rebound(values, batch, **_):
    cfg = RULES["rebound"]
    ref = "IS 13311 (Part 2)"
    rn = values.get("rebound_number_avg")
    est = values.get("estimated_strength_mpa")
    if not _is_num(rn) or rn <= 0:
        return [_o("rebound", "Rebound hammer", ref, "average rebound number", rn, "REVIEW_REQUIRED", "Rebound number missing.")]
    if not _is_num(est):
        return [_o("rebound", "Rebound hammer", ref, "estimated strength from calibration curve", None, "REVIEW_REQUIRED",
                   "Rebound number recorded but no calibrated strength estimate; screening value only.")]
    if est >= cfg["verified_fraction"] * batch.fck:
        st = "VERIFIED"
    elif est >= cfg["review_fraction"] * batch.fck:
        st = "REVIEW_REQUIRED"
    else:
        st = "FLAGGED"
    return [_o("rebound", "Rebound hammer (indicative)", ref, f">= {batch.fck:.0f} N/mm2 (review down to {cfg['review_fraction']*100:.0f}%)", est, st,
               f"Estimated strength {est} N/mm2 vs specified {batch.fck:.0f}. Indicative only; confirm with cores if in doubt.")]


_DISPATCH = {
    "slump": _slump,
    "fresh_temperature": _temperature,
    "cube_compressive_strength": _cube,
    "core_strength": _core,
    "upv": _upv,
    "rebound_hammer": _rebound,
}


def evaluate(test_type: str, values: dict, age_days: int | None, batch, prior_28d_means=None) -> tuple[str, list[dict]]:
    fn = _DISPATCH.get(test_type)
    if fn is None:
        outs = [_o("unknown_test", "Unknown test type", "-", ", ".join(_DISPATCH), test_type, "REVIEW_REQUIRED", "No rule configured for this test.")]
    else:
        outs = fn(values or {}, batch, age_days=age_days, prior_28d_means=prior_28d_means)
    return worst(o["status"] for o in outs), outs
