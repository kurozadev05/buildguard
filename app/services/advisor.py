"""AI Test Advisor = verified rules layer (+ optional LLM only to phrase explanations).

The checklist itself is generated deterministically from IS-code rules, so it cannot
invent tests or references. The optional LLM never adds items; it only rewrites the
already-verified checklist in plain language.
"""
import re

from .rules_engine import RULES, fck_of, norm_exposure, required_samples


def _placing_condition(element_type: str, reinforcement: str, method: str) -> str:
    if method == "pumped":
        return "pumped_slipform_trench_piling"
    if method == "tremie":
        return "tremie"
    if element_type in ("column", "beam") and reinforcement == "heavy":
        return "heavily_reinforced"
    if reinforcement == "heavy":
        return "heavily_reinforced"
    return "lightly_reinforced"


def build_checklist(*, construction_type="rcc", element_type="slab", grade="M25", exposure="moderate",
                    quantity_m3=10.0, reinforcement="light", placing_method="normal", weather="normal",
                    cement_type="opc") -> dict:
    grade = grade.upper()
    exp_key = norm_exposure(exposure)
    exp = RULES["exposure"][exp_key]
    lim = exp["pcc" if construction_type == "pcc" else "rcc"]
    n_samples = required_samples(quantity_m3)
    placing = _placing_condition(element_type, reinforcement, placing_method)
    lo, hi = RULES["slump"]["ranges"][placing]
    curing_days = RULES["curing_min_days"]["opc"] if cement_type == "opc" and weather != "hot" else RULES["curing_min_days"]["hot_dry_or_mineral_admixture"]
    cover = max(exp["nominal_cover_mm"], 50) if element_type == "footing" else exp["nominal_cover_mm"]
    fck = fck_of(grade)

    items = [
        {"key": "mix_documents", "test_type": None, "mandatory": True, "name": "Delivery ticket & mix details check",
         "name_hi": "डिलीवरी टिकट और मिक्स विवरण की जाँच",
         "purpose": f"Confirm grade, w/c ratio and cement content against exposure limits ({exp_key}: min {lim['min_grade'] or 'no minimum'}, "
                    f"w/c <= {lim['max_wc']}, cement >= {lim['min_cement_kg_m3']} kg/m3).",
         "purpose_hi": "ग्रेड, w/c अनुपात और सीमेंट मात्रा को एक्सपोज़र सीमा के अनुसार जाँचें।",
         "timing": "On delivery, before placing", "frequency": "Every batch / truck",
         "evidence": ["Delivery challan photo", "Mix design sheet"], "reference": "IS 456:2000 Table 5"},
        {"key": "slump", "test_type": "slump", "mandatory": True, "name": "Slump test", "name_hi": "स्लम्प परीक्षण",
         "purpose": f"Check workability before placing. Target {lo}-{hi} mm for '{placing}'.",
         "purpose_hi": f"कंक्रीट डालने से पहले कार्यक्षमता जाँचें। लक्ष्य {lo}-{hi} मिमी।",
         "timing": "At point of placing, each sampling", "frequency": f"With every cube sample ({n_samples} sample(s) for {quantity_m3:g} m3)",
         "evidence": ["Photo of slump cone measurement (geotagged)"], "reference": "IS 456:2000 Cl. 7.1; IS 1199",
         "params": {"placing_condition": placing}},
        {"key": "fresh_temperature", "test_type": "fresh_temperature", "mandatory": weather != "normal",
         "name": "Fresh concrete temperature", "name_hi": "ताज़ा कंक्रीट का तापमान",
         "purpose": "Control placing temperature in hot or cold weather.",
         "purpose_hi": "गर्म या ठंडे मौसम में तापमान नियंत्रित रखें।",
         "timing": "At point of placing", "frequency": "With each sample" if weather != "normal" else "Recommended in extreme weather",
         "evidence": ["Thermometer reading photo"], "reference": "IS 7861 (Part 1 & 2) / project specification"},
        {"key": "cube_casting", "test_type": None, "mandatory": True, "name": f"Cast cube samples: {n_samples} sample(s) x 6 cubes",
         "name_hi": f"क्यूब कास्टिंग: {n_samples} सैंपल x 6 क्यूब",
         "purpose": "Cast 150 mm cubes (3 for 7-day, 3 for 28-day) per sample at the frequency required for the quantity placed.",
         "purpose_hi": "मात्रा के अनुसार आवश्यक सैंपल में 150 मिमी क्यूब बनाएँ (3 सात दिन के, 3 अट्ठाईस दिन के)।",
         "timing": "During placing", "frequency": f"{n_samples} sample(s) for {quantity_m3:g} m3",
         "evidence": ["Photo of labelled cubes with QR sticker"], "reference": "IS 456:2000 Cl. 15.2 & Table 12; IS 516 (Part 1/Sec 1)"},
        {"key": "cube_7d", "test_type": "cube_compressive_strength", "age_days": 7, "mandatory": True,
         "name": "7-day cube strength (early indicator)", "name_hi": "7 दिन क्यूब संपीडन शक्ति (प्रारंभिक संकेत)",
         "purpose": f"Early indicator: expect roughly {RULES['cube']['early_age_min_fraction']['7']*100:.0f}% of {fck:.0f} = "
                    f"{RULES['cube']['early_age_min_fraction']['7']*fck:.1f} N/mm2. Not an acceptance test.",
         "purpose_hi": "प्रारंभिक संकेत; स्वीकृति परीक्षण नहीं।",
         "timing": "7 days after casting", "frequency": "3 cubes per sample",
         "evidence": ["Crushing machine reading photo (geotagged)", "Lab report"], "reference": "IS 516 (Part 1/Sec 1)"},
        {"key": "cube_28d", "test_type": "cube_compressive_strength", "age_days": 28, "mandatory": True,
         "name": "28-day cube strength (acceptance)", "name_hi": "28 दिन क्यूब संपीडन शक्ति (स्वीकृति)",
         "purpose": "Acceptance test: each result >= fck-margin and the mean of 4 consecutive results >= fck+margin.",
         "purpose_hi": "स्वीकृति परीक्षण: हर परिणाम और 4 परिणामों का औसत सीमा के अनुसार होना चाहिए।",
         "timing": "28 days after casting", "frequency": "3 cubes per sample",
         "evidence": ["Crushing machine reading photo (geotagged)", "Lab report / certificate"],
         "reference": "IS 456:2000 Cl. 15.4, 16.1 & Table 11", "params": {"individual_min_mpa": round(fck - (3 if fck <= 15 else 4), 1)}},
        {"key": "curing", "test_type": None, "mandatory": True, "name": f"Curing record (minimum {curing_days} days)",
         "name_hi": f"क्योरिंग रिकॉर्ड (न्यूनतम {curing_days} दिन)",
         "purpose": "Keep exposed surfaces moist; log start/end and method. Poor curing is a top cause of durability problems.",
         "purpose_hi": "सतह को नम रखें; शुरू/समाप्ति और विधि दर्ज करें।",
         "timing": "Immediately after finishing", "frequency": "Daily log",
         "evidence": ["Curing log photo"], "reference": "IS 456:2000 Cl. 13.5"},
        {"key": "cover", "test_type": None, "mandatory": construction_type == "rcc",
         "name": f"Cover check before pour (nominal {cover} mm)", "name_hi": f"पोर से पहले कवर जाँच ({cover} मिमी)",
         "purpose": f"Verify cover blocks give {cover} mm nominal cover for {exp_key} exposure" + (" (footings: at least 50 mm)." if element_type == "footing" else "."),
         "purpose_hi": f"जाँचें कि कवर {cover} मिमी है।",
         "timing": "Before concreting", "frequency": "Each element",
         "evidence": ["Cover block / cover meter photo"], "reference": "IS 456:2000 Cl. 26.4 & Table 16"},
        {"key": "ndt_followup", "test_type": "upv", "mandatory": False, "name": "NDT follow-up (rebound hammer / UPV) - only if results are doubtful",
         "name_hi": "एनडीटी फॉलो-अप (रिबाउंड हैमर / UPV) - केवल संदिग्ध परिणाम पर",
         "purpose": "If a cube result is Review Required or Flagged, screen the affected element with NDT, then core if still doubtful.",
         "purpose_hi": "यदि क्यूब परिणाम संदिग्ध हो तो एनडीटी करें, फिर आवश्यकता होने पर कोर।",
         "timing": "After a doubtful result", "frequency": "Per affected element",
         "evidence": ["NDT readings sheet"], "reference": "IS 13311 (Part 1 & 2); IS 456:2000 Cl. 17.4"},
    ]
    return {
        "context": {"construction_type": construction_type, "element_type": element_type, "grade": grade,
                    "exposure": exp_key, "quantity_m3": quantity_m3, "placing_condition": placing, "weather": weather},
        "sample_plan": {"required_samples": n_samples, "cubes_per_sample": 6},
        "items": items,
        "disclaimer": RULES["disclaimer"],
        "engine": "rules",
    }


_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by can do does for from how i if in is it its me my of on or should so than that the then there this to was what when where which who why will with you your about tell give show explain please need want know regarding into over under have has had been being not but also any some more most each other such only very".split())


def _stem(w: str) -> str:
    """Tiny suffix stripper so cure/curing/cured and cube/cubes/cubed meet. Deliberately crude; semantic search handles the rest."""
    if len(w) > 3:
        for suf in ("ing", "ed", "es", "s", "e"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[:-len(suf)]
    return w


def _tokens(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Keyword retrieval over the verified knowledge notes (RAG-lite; swap for pgvector later)."""
    q = _tokens(query)
    scored = []
    for k in RULES["knowledge"]:
        hay = _tokens(k["title"] + " " + k["text"] + " " + " ".join(k["tags"]))
        tag_hits = len(q & _tokens(" ".join(k["tags"])))
        score = len(q & hay) + 2 * tag_hits
        if score:
            scored.append((score, k))
    scored.sort(key=lambda t: -t[0])
    return [{"id": k["id"], "reference": k["reference"], "title": k["title"], "text": k["text"], "score": s} for s, k in scored[:limit]]
