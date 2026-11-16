"""
test_eval_series.py — The director eval's series mode and blind export
========================================================================
Series mode runs the same case through today's full prompt (judge off) and
the judge + writer-only pipeline (judge on). The blind export hands an
independent judge every pair in both orders under neutral ids, with the key
kept outside its folder; scoring counts a case only when both orders agree.
Offline: fake judge, fake writer.
"""

import asyncio
import json
from unittest.mock import patch

import evals.run_director_eval as director_eval
from tests.helpers import FakeStreamLLM
from tests.test_director import SCRIPT_TEXT
from tests.test_judge_pipeline import FakeJudge

CASE = {"id": "c1", "why": "a test", "user_text": "welcome back we ship monday", "target_style": "calm CEO", "expect": "script"}


def run_side(judge_on, judge_replies=("WRITE",)):
    writer, judge = FakeStreamLLM(SCRIPT_TEXT, step=15), FakeJudge(*judge_replies)
    with patch("app.services.llm.get_llm", return_value=writer), patch("app.services.llm.get_judge_llm", return_value=judge):
        out = asyncio.run(director_eval.run_new(CASE, judge=judge_on))
    return out, writer, judge


def test_the_old_side_is_todays_full_prompt_with_no_judge():
    out, writer, judge = run_side(False)
    assert judge.calls == 0 and "## ERRORS" in writer.prompts[0].to_string()
    assert out["display_script"] and out.get("judge") is None and not out.get("note")


def test_the_new_side_is_the_judge_then_the_writer_only_prompt():
    out, writer, judge = run_side(True, ("WRITE_NOTE",))
    assert judge.calls == 1 and "## ERRORS" not in writer.prompts[0].to_string()
    assert out["display_script"] and out["judge"]["label"] == "WRITE_NOTE" and out["note"]


def test_a_refusal_by_the_judge_is_an_error_on_the_new_side():
    out, writer, _ = run_side(True, ("REFUSE",))
    assert out["error"] and not out.get("display_script")      # the writer may have started (parallel); nothing is shown


def test_in_series_mode_every_case_is_a_pair():
    case = dict(CASE, new_only=True)
    assert director_eval._sides_for(case, ["new", "old"]) == ["new"]
    with patch.object(director_eval, "_SERIES_MODE", True):
        assert director_eval._sides_for(case, ["new", "old"]) == ["new", "old"]


def results_for(*case_ids):
    return {cid: {"new": {"error": None, "display_script": f"new script {cid}", "tts_script": f"new script {cid}"},
                  "old": {"error": None, "display_script": f"old script {cid}", "tts_script": f"old script {cid}"}} for cid in case_ids}


def test_the_blind_export_hides_which_prompt_wrote_what_and_shows_each_pair_both_ways(tmp_path):
    cases = [dict(CASE, id="c1"), dict(CASE, id="c2"), dict(CASE, id="c3")]
    results = results_for("c1", "c2")             # c3 has no results yet: left out
    path = director_eval.blind_export(tmp_path, cases, results, seed=7)
    pairs = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == tmp_path / "blind" and not (tmp_path / "blind" / "blind_key.json").exists()
    assert len(pairs) == 4 and len({p["id"] for p in pairs}) == 4
    for p in pairs:
        assert set(p) == {"id", "draft", "style", "case_intent", "A", "B"}
        assert set(p["A"]) <= {"script", "refused_with"} and set(p["B"]) <= {"script", "refused_with"}    # no voice script
    key = json.loads((tmp_path / "blind_key.json").read_text(encoding="utf-8"))
    for cid in ("c1", "c2"):
        orders = sorted((v["A"], v["B"]) for v in key.values() if v["case"] == cid)
        assert orders == [("new", "old"), ("old", "new")]
    assert director_eval.blind_export(tmp_path, cases, results, seed=7).read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_scoring_counts_a_case_only_when_both_orders_agree(tmp_path):
    cases = [dict(CASE, id=c) for c in ("c1", "c2", "c3")]
    director_eval.blind_export(tmp_path, cases, results_for("c1", "c2", "c3"), seed=3)
    key = json.loads((tmp_path / "blind_key.json").read_text(encoding="utf-8"))

    def pick(item_id, side):              # the letter that holds `side` in this item
        return "A" if key[item_id]["A"] == side else "B"
    verdicts = []
    for item_id, entry in key.items():
        if entry["case"] == "c1":
            verdicts.append({"id": item_id, "winner": pick(item_id, "new"), "reason": "keeps the numbers"})
        elif entry["case"] == "c2":
            verdicts.append({"id": item_id, "winner": "A", "reason": "first one reads better"})     # position bias
        else:
            verdicts.append({"id": item_id, "winner": "same", "reason": "equal"})
    lines = director_eval.blind_score(tmp_path, verdicts)
    tally = json.loads((tmp_path / "blind_scores.json").read_text(encoding="utf-8"))["tally"]
    assert tally["new"] == ["c1"] and tally["old"] == [] and tally["same"] == ["c3"] and tally["split"] == ["c2"]
    assert any("disagreed" in line and "c2" in line for line in lines)


def test_the_new_cases_are_well_formed():
    cases = json.loads(director_eval.CASES.read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) == 33
    assert {"boxing-pep", "immigration-policy", "villain-monologue", "hindi-farewell", "launch-numbers",
            "apology-delay", "farewell-colleague", "death-penalty-debate"} <= set(ids)


def test_in_an_aa_check_the_new_side_is_todays_full_prompt_too():
    with patch.object(director_eval, "_SERIES_MODE", True):
        out, writer, judge = run_side(False)            # what the A/A mode asks for on the new side
    assert judge.calls == 0 and "## ERRORS" in writer.prompts[0].to_string() and out["display_script"]
