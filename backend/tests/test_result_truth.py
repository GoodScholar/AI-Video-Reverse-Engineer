import copy

from app.result_truth import adopt_text_result, completed_text_result


def test_completed_text_result_is_immutable_until_explicit_adoption():
    """Changing completion to write a domain version must make this test fail."""
    run = {"id": "text-run-1", "status": "running", "outputItems": []}
    before = copy.deepcopy(run)

    completed = completed_text_result(run, result_id="answer-1", text="可采用的脚本")

    assert run == before
    assert completed == {
        "id": "text-run-1", "status": "completed",
        "outputItems": [{"resultId": "answer-1", "status": "completed", "text": "可采用的脚本"}],
    }

    domains = {"scripts": []}
    adopted = adopt_text_result(completed, result_id="answer-1", target="script", domains=domains)
    repeated = adopt_text_result(completed, result_id="answer-1", target="script", domains=domains)

    assert adopted == repeated
    assert adopted["versionId"].startswith("text-") and len(adopted["versionId"]) == 37
    assert {key: adopted[key] for key in ("sourceRunId", "sourceResultId", "text")} == {
        "sourceRunId": "text-run-1", "sourceResultId": "answer-1", "text": "可采用的脚本",
    }
    assert domains == {"scripts": [adopted]}
    assert completed["outputItems"][0] == {"resultId": "answer-1", "status": "completed", "text": "可采用的脚本"}


def test_text_adoption_ids_do_not_collide_when_run_and_result_ids_contain_hyphens():
    """Joining run/result IDs with hyphens would collapse these two distinct outputs."""
    first = {"id": "a-b", "outputItems": [{"resultId": "c", "status": "completed", "text": "第一版"}]}
    second = {"id": "a", "outputItems": [{"resultId": "b-c", "status": "completed", "text": "第二版"}]}
    domains = {"scripts": []}

    first_version = adopt_text_result(first, result_id="c", target="script", domains=domains)
    second_version = adopt_text_result(second, result_id="b-c", target="script", domains=domains)

    assert first_version["versionId"] != second_version["versionId"]
    assert [item["text"] for item in domains["scripts"]] == ["第一版", "第二版"]


def test_adopt_reads_legacy_results_list_and_single_result_without_rewriting_runs():
    domains = {"scripts": [], "remixPlans": []}
    legacy_list = {"id": "old-run", "results": [{"id": "old-result", "text": "旧脚本"}]}
    legacy_single = {"id": "single-run", "result": {"resultId": "single-result", "text": "旧计划"}}

    script = adopt_text_result(legacy_list, result_id="old-result", target="script", domains=domains)
    plan = adopt_text_result(legacy_single, result_id="single-result", target="remix_plan", domains=domains)

    assert script["text"] == "旧脚本" and plan["text"] == "旧计划"
    assert legacy_list == {"id": "old-run", "results": [{"id": "old-result", "text": "旧脚本"}]}
    assert legacy_single == {"id": "single-run", "result": {"resultId": "single-result", "text": "旧计划"}}
