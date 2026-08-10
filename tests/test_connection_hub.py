"""WebSocket 브로드캐스트의 안정성.

250명 규모 행사에서 **폰 한 대의 네트워크 상태가 행사 진행 전체를 붙잡으면
안 된다**는 것이 이 파일의 관심사다. 끊긴 연결(예외)뿐 아니라, 끊기지도
않고 받아가지도 않는 "막힌" 연결까지 다룬다 -- 행사장 Wi-Fi에서 실제로
자주 생기는 상태이고, 예외가 안 나기 때문에 기존 try/except로는 안 잡힌다.
"""

import asyncio
import time

import pytest

from app import main as main_module
from app.main import ConnectionHub


class FakeWS:
    """send_text만 흉내 내는 최소 WebSocket 대역."""

    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.received: list[str] = []

    async def send_text(self, payload: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionResetError("연결이 끊겼습니다")
        self.received.append(payload)


@pytest.mark.asyncio
async def test_broadcast_reaches_every_connection_and_respects_roles():
    hub = ConnectionHub()
    stage, admin, mobile = FakeWS(), FakeWS(), FakeWS()
    hub.connections[stage] = "stage"
    hub.connections[admin] = "admin"
    hub.connections[mobile] = "mobile"

    await hub.broadcast({"type": "phase"})
    assert len(stage.received) == len(admin.received) == len(mobile.received) == 1

    # race_tick은 위치 데이터가 커서 무대에만 보낸다(기획안 §4.7)
    await hub.broadcast({"type": "race_tick"}, roles={"stage"})
    assert len(stage.received) == 2
    assert len(admin.received) == 1
    assert len(mobile.received) == 1


@pytest.mark.asyncio
async def test_broadcast_skips_sender_and_drops_broken_connections():
    hub = ConnectionHub()
    sender, healthy, broken = FakeWS(), FakeWS(), FakeWS(fail=True)
    for ws in (sender, healthy, broken):
        hub.connections[ws] = "mobile"

    await hub.broadcast({"type": "cheer"}, sender=sender)

    assert sender.received == []  # 보낸 사람에게는 되돌려주지 않는다
    assert len(healthy.received) == 1
    assert broken not in hub.connections  # 끊긴 연결은 정리된다
    assert healthy in hub.connections


@pytest.mark.asyncio
async def test_one_stalled_phone_does_not_delay_everyone_else():
    """**막힌 폰 한 대가 나머지 249대의 수신을 붙잡으면 안 된다.**

    예전에는 for 루프에서 하나씩 await해서 지연이 그대로 합산됐다 --
    1.5초씩 막힌 폰 5대면 브로드캐스트 하나에 7.5초가 걸렸고, 런북이
    이 브로드캐스트를 직접 await하므로 그게 곧 행사 진행 지연이었다.
    """
    hub = ConnectionHub()
    normal = [FakeWS() for _ in range(40)]
    stalled = [FakeWS(delay=0.4) for _ in range(5)]
    for ws in normal + stalled:
        hub.connections[ws] = "mobile"

    started = time.perf_counter()
    await hub.broadcast({"type": "phase"})
    elapsed = time.perf_counter() - started

    assert all(len(ws.received) == 1 for ws in normal)
    # 순차였다면 0.4 x 5 = 2.0초. 동시에 보내면 가장 느린 한 대(0.4초)로 수렴한다.
    assert elapsed < 1.0, f"느린 연결의 지연이 합산되고 있습니다: {elapsed:.2f}초"


@pytest.mark.asyncio
async def test_permanently_stuck_connection_is_timed_out_and_dropped(monkeypatch):
    """끊기지도 받아가지도 않는 연결은 제한 시간 뒤 끊어야 한다.

    예외가 나지 않으므로 try/except로는 영원히 못 잡는다. 이런 연결을
    남겨두면 브로드캐스트마다 매번 같은 시간을 다시 까먹는다."""
    monkeypatch.setattr(main_module, "SEND_TIMEOUT_SECONDS", 0.15)
    hub = ConnectionHub()
    healthy = FakeWS()
    stuck = FakeWS(delay=30.0)  # 사실상 영원히 안 받아가는 연결
    hub.connections[healthy] = "mobile"
    hub.connections[stuck] = "mobile"

    started = time.perf_counter()
    await hub.broadcast({"type": "phase"})
    elapsed = time.perf_counter() - started

    assert len(healthy.received) == 1
    assert elapsed < 1.0, f"막힌 연결이 제한 시간을 넘겨 붙잡고 있습니다: {elapsed:.2f}초"
    assert stuck not in hub.connections
    assert healthy in hub.connections
