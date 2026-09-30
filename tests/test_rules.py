from types import SimpleNamespace as NS

from app.services import rules_engine as r


def batch(grade="M25", **kw):
    d = dict(grade=grade, fck=r.fck_of(grade), exposure="moderate", construction_type="rcc", wc_ratio=0.48, cement_content_kg_m3=320)
    d.update(kw)
    return NS(**d)


def test_thresholds_match_is456_table11():
    assert r.group_threshold(25, "M25") == 29.0        # max(25+3.5, 25+4)
    assert r.group_threshold(30, "M30") == 34.0        # max(30+4.0, 30+4)
    assert r.group_threshold(15, "M15") == 18.0        # +3 for <= M15
    assert r.individual_min(25) == 21.0
    assert r.individual_min(15) == 12.0


def test_sampling_frequency_table12():
    assert [r.required_samples(q) for q in (3, 10, 25, 45, 50, 51, 100, 101)] == [1, 2, 3, 4, 4, 5, 5, 6]


def test_cube_28d_individual_fail_is_flagged():
    st, outs = r.evaluate("cube_compressive_strength", {"specimens_mpa": [19.8, 20.4, 19.5]}, 28, batch())
    assert st == "FLAGGED"


def test_cube_28d_good_single_sample_verified_group_pending():
    st, outs = r.evaluate("cube_compressive_strength", {"specimens_mpa": [31, 30.2, 31.8]}, 28, batch())
    assert st == "VERIFIED"
    assert any(o.get("group_pending") for o in outs)


def test_cube_28d_between_limits_needs_review():
    st, _ = r.evaluate("cube_compressive_strength", {"specimens_mpa": [25.5, 26.0, 25.8]}, 28, batch())
    assert st == "REVIEW_REQUIRED"


def test_cube_group_of_four_fails():
    prior = [26.0, 26.5, 27.0]      # each >= 21 individually, mean of 4 below 29
    st, outs = r.evaluate("cube_compressive_strength", {"specimens_mpa": [26.0, 26.2, 26.4]}, 28, batch(), prior)
    assert st == "FLAGGED"
    assert any(o["rule_id"] == "cube_group_28d" and o["status"] == "FLAGGED" for o in outs)


def test_specimen_variation_over_15pct_invalid():
    st, outs = r.evaluate("cube_compressive_strength", {"specimens_mpa": [20.0, 30.0, 31.0]}, 28, batch())
    assert st == "REVIEW_REQUIRED"
    assert outs[0]["rule_id"] == "cube_variation"


def test_missing_data_never_passes():
    assert r.evaluate("slump", {}, None, batch())[0] == "REVIEW_REQUIRED"
    assert r.evaluate("cube_compressive_strength", {}, 28, batch())[0] == "REVIEW_REQUIRED"
    assert r.evaluate("nonsense", {}, None, batch())[0] == "REVIEW_REQUIRED"


def test_slump_bands():
    assert r.evaluate("slump", {"slump_mm": 90}, None, batch())[0] == "VERIFIED"
    assert r.evaluate("slump", {"slump_mm": 115}, None, batch())[0] == "REVIEW_REQUIRED"
    assert r.evaluate("slump", {"slump_mm": 160}, None, batch())[0] == "FLAGGED"


def test_core_acceptance():
    assert r.evaluate("core_strength", {"cores_equiv_cube_mpa": [22, 23, 24]}, None, batch())[0] == "VERIFIED"   # mean 23 >= 21.25, min 22 >= 18.75
    assert r.evaluate("core_strength", {"cores_equiv_cube_mpa": [15, 23, 24]}, None, batch())[0] == "FLAGGED"


def test_upv_grades():
    assert r.evaluate("upv", {"velocity_km_s": 4.0}, None, batch())[0] == "VERIFIED"
    assert r.evaluate("upv", {"velocity_km_s": 3.2}, None, batch())[0] == "REVIEW_REQUIRED"
    assert r.evaluate("upv", {"velocity_km_s": 2.5}, None, batch())[0] == "FLAGGED"


def test_registration_exposure_limits():
    st, _ = r.registration_check(batch("M20", exposure="moderate"))
    assert st == "FLAGGED"                                  # M20 below M25 minimum for moderate
    assert r.registration_check(batch("M25"))[0] == "VERIFIED"
    assert r.registration_check(batch("M25", wc_ratio=0.6))[0] == "FLAGGED"
    assert r.registration_check(batch("M25", wc_ratio=None))[0] == "REVIEW_REQUIRED"
