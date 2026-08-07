"""부하 테스트: 모바일 250대 동시 접속 + 라운드별 동시 예측 시뮬레이션.

작업계획서 P5-3의 실사용 환경 의존 DoD(실제 250대 폰 동시 접속)는 이 세션에서
물리적으로 검증할 수 없으므로, asyncio 기반 WebSocket/HTTP 클라이언트로
그 부하 패턴을 재현한다. 결과 보고 시 "시뮬레이션 기반"임을 명시할 것.

사용법 (서버가 먼저 떠 있어야 한다: `python run.py` 또는 uvicorn):
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --count 250
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.error
import urllib.request

import websockets


def http_call(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base_url + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


async def http_call_async(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict, float]:
    start = time.perf_counter()
    status, payload = await asyncio.to_thread(http_call, base_url, method, path, body)
    elapsed = time.perf_counter() - start
    return status, payload, elapsed


async def simulate_participant(base_url: str, participant: dict, round1_candidates: list[str]) -> dict:
    latencies: dict[str, float] = {}
    status, joined, elapsed = await http_call_async(
        base_url, "POST", "/api/predict/join", {"participant_id": participant["id"]}
    )
    latencies["join"] = elapsed
    if status != 200:
        return {"ok": False, "stage": "join", "status": status, "latencies": latencies}
    token = joined["token"]

    target = round1_candidates[hash(participant["id"]) % len(round1_candidates)]
    status, _, elapsed = await http_call_async(
        base_url, "POST", "/api/predict/choose", {"token": token, "round": 1, "target": target}
    )
    latencies["choose"] = elapsed
    if status != 200:
        return {"ok": False, "stage": "choose", "status": status, "latencies": latencies}

    return {"ok": True, "latencies": latencies}


async def open_mobile_socket(ws_url: str) -> bool:
    try:
        async with websockets.connect(ws_url, open_timeout=10) as ws:
            await asyncio.sleep(2)  # 잠시 연결을 유지해 "동시 접속" 상태를 재현
            return True
    except Exception:
        return False


def summarize(label: str, values: list[float]) -> None:
    if not values:
        print(f"  {label}: (데이터 없음)")
        return
    values_sorted = sorted(values)
    p50 = statistics.median(values_sorted)
    p95 = values_sorted[min(len(values_sorted) - 1, int(len(values_sorted) * 0.95))]
    print(
        f"  {label}: n={len(values)} p50={p50*1000:.0f}ms p95={p95*1000:.0f}ms "
        f"max={max(values_sorted)*1000:.0f}ms"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="250명 동시 접속 부하 시뮬레이션")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ws-url", default=None, help="기본값: base-url의 ws:// 버전")
    parser.add_argument("--count", type=int, default=250)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ws_base = args.ws_url or base_url.replace("http://", "ws://").replace("https://", "wss://")

    print(f"[1/5] 세션 초기화 및 {args.count}명 샘플 명단으로 레이싱+예측 세션 생성")
    http_call(base_url, "POST", "/api/session/reset")
    status, sample = http_call(base_url, "GET", f"/api/roster/sample?count={args.count}")
    assert status == 200, sample
    status, _ = http_call(
        base_url,
        "POST",
        "/api/session",
        {
            "participants": sample["participants"],
            "draw_count": 5,
            "mode": "racing",
            "total_seconds": 300,
            "predictions_enabled": True,
        },
    )
    assert status == 200

    print("[2/5] 커밋 생성 (R1 선택창 개방)")
    status, draw = http_call(base_url, "POST", "/api/draw/commit")
    assert status == 200, draw
    departments = list(draw["snapshot"]["departments"].keys())

    print(f"[3/5] 모바일 WebSocket {args.count}개 동시 접속")
    ws_start = time.perf_counter()
    ws_results = await asyncio.gather(
        *[open_mobile_socket(f"{ws_base}/ws?role=mobile") for _ in range(args.count)]
    )
    ws_elapsed = time.perf_counter() - ws_start
    ws_success = sum(1 for r in ws_results if r)
    print(f"  성공 {ws_success}/{args.count} (총 {ws_elapsed:.2f}초)")

    print(f"[4/5] {args.count}명 동시 참여(join) + 1라운드 선택")
    api_start = time.perf_counter()
    results = await asyncio.gather(
        *[simulate_participant(base_url, p, departments) for p in sample["participants"]]
    )
    api_elapsed = time.perf_counter() - api_start

    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]

    print(f"[5/5] 결과 요약 (총 소요 {api_elapsed:.2f}초)")
    print(f"  성공: {len(ok_results)}/{len(results)}, 실패: {len(fail_results)}")
    if fail_results:
        by_stage: dict[str, int] = {}
        for r in fail_results:
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
        print(f"  실패 단계별: {by_stage}")

    for stage in ("join", "choose"):
        summarize(stage, [r["latencies"][stage] for r in ok_results if stage in r["latencies"]])

    status, leaderboard = http_call(base_url, "GET", f"/api/predict/leaderboard?top_n={args.count}")
    print(f"\n리더보드 참여 인원 확인: {len(leaderboard['top'])}/{args.count}")

    print("\n(주의) 이 스크립트는 asyncio 기반 시뮬레이션입니다 -- 실제 250대")
    print("물리 기기·행사장 Wi-Fi 환경을 대체하지 않습니다. 실사용 전 실측 권장.")


if __name__ == "__main__":
    asyncio.run(main())
