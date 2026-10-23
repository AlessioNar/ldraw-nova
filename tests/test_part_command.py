"""`ldraw-agent part` takes many codes in one call: a process start costs seconds, so the agent must not loop."""
from argparse import Namespace

from ldraw_tools.commands.library import run_part


def args(*codes, limit=5, brief=False, full=False):
    return Namespace(code=list(codes), limit=limit, brief=brief, full=full)


def test_one_code_keeps_the_original_full_output(parts):
    report, status = run_part(args("3001"), None, parts)
    assert status == 0 and report["code"] == "3001.dat"
    assert {"path", "metadata", "bounds", "size", "stud_positions", "connectors", "diagnostics", "note"} <= set(report)


def test_several_codes_return_one_brief_entry_each(parts):
    report, status = run_part(args("3001", "3003.dat", "3022"), None, parts)
    assert status == 0 and report["count"] == 3 and report["errors"] == {} and "incomplete" in report
    assert [p["code"] for p in report["parts"]] == ["3001.dat", "3003.dat", "3022.dat"]
    first = report["parts"][0]
    assert set(first) == {"code", "description", "complete", "bounds", "size", "stud_count", "connector_count"}
    assert isinstance(first["stud_count"], int) and first["description"] and first["size"]
    assert "stud_positions" not in first and "connectors" not in first               # brief: no long lists


def test_full_detail_on_request_for_a_multi_part_call(parts):
    report, _ = run_part(args("3001", "3003", full=True), None, parts)
    assert all("stud_positions" in p and "connectors" in p for p in report["parts"])


def test_one_unknown_code_does_not_hide_the_others(parts):
    report, status = run_part(args("3001", "no-such-part-0", "3003"), None, parts)
    assert status == 1                                                               # something failed
    assert [p["code"] for p in report["parts"]] == ["3001.dat", "3003.dat"]
    assert list(report["errors"]) == ["no-such-part-0"] and report["errors"]["no-such-part-0"]


def test_brief_works_for_a_single_code(parts):
    report, status = run_part(args("3001", brief=True), None, parts)
    assert status == 0 and report["count"] == 1 and "connectors" not in report["parts"][0]
