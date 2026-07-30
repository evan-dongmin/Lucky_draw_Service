"""static/fairness.js(브라우저 WebCrypto 이식본)가 app/fairness.py와 바이트 단위로
동일한 커밋/순위/통과자/부서집계를 내는지 Node.js로 교차 검증한다.

verify.html의 신뢰 모델은 "서버를 믿지 않고 브라우저에서 독립 재계산"이므로,
두 구현이 어긋나면 검증 페이지 자체가 거짓 불일치/거짓 일치를 낼 수 있다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.fairness import _compute_outcome, build_snapshot, compute_commit
from app.roster import generate_sample_participants

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="Node.js가 없어 JS 이식본 교차 검증을 건너뜁니다"
)

FAIRNESS_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "fairness.js"


def _run_node_outcome(seed: str, snapshot: dict) -> dict:
    script = f"""
const fairness = require({json.dumps(str(FAIRNESS_JS_PATH))});
const snapshot = {json.dumps(snapshot, ensure_ascii=False)};
const seed = {json.dumps(seed)};

(async () => {{
  const result = await fairness.recomputeFromReveal(seed, snapshot);
  console.log(JSON.stringify(result));
}})();
"""
    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, encoding="utf-8", timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node 실행 실패: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("participant_count", [12, 60, 250])
@pytest.mark.parametrize("draw_count", [1, 3, 7])
def test_js_matches_python_recompute(participant_count, draw_count):
    if draw_count > participant_count:
        pytest.skip("당첨 인원이 참가자 수보다 많음")

    participants = generate_sample_participants(participant_count, seed=99)
    seed = f"parity-seed-{participant_count}-{draw_count}"
    snapshot = build_snapshot("s1", participants, draw_count, [], "2026-07-30T00:00:00Z")
    commit = compute_commit(seed, snapshot)

    eligible_ids = [p["id"] for p in snapshot["participants"]]
    py_outcome = _compute_outcome(eligible_ids, seed, draw_count, snapshot["departments"])

    node_result = _run_node_outcome(seed, snapshot)

    assert node_result["commit"] == commit
    assert node_result["winners"] == py_outcome["winners"]
    assert node_result["ranking"] == py_outcome["ranking"]
    assert node_result["round_pass_ids"]["1"] == py_outcome["round_pass_ids"][1]
    assert node_result["round_pass_ids"]["2"] == py_outcome["round_pass_ids"][2]
    assert node_result["finalist_count"] == py_outcome["finalist_count"]

    for round_key in ("1", "2"):
        py_rates = py_outcome["department_pass_rate"][int(round_key)]
        js_rates = node_result["department_pass_rate"][round_key]
        assert set(py_rates.keys()) == set(js_rates.keys())
        for name, rate in py_rates.items():
            assert abs(js_rates[name] - rate) < 1e-9


def test_canonical_json_matches_for_nested_key_order():
    script = f"""
const fairness = require({json.dumps(str(FAIRNESS_JS_PATH))});
console.log(fairness.canonicalStringify({{ b: 1, a: {{ z: 1, y: [1, 2, "가나"] }} }}));
"""
    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    js_output = proc.stdout.strip()

    from app.fairness import canonical_json

    py_output = canonical_json({"b": 1, "a": {"z": 1, "y": [1, 2, "가나"]}})
    assert js_output == py_output
