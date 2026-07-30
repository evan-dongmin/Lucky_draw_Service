const views = {
  idle: document.getElementById("idle-view"),
  waiting: document.getElementById("waiting-view"),
  committed: document.getElementById("committed-view"),
  drawing: document.getElementById("drawing-view"),
  racing: document.getElementById("racing-view"),
};
const statusEl = document.getElementById("ws-status");
const participantCountEl = document.getElementById("participant-count");
const departmentCountEl = document.getElementById("department-count");
const commitBadgeEl = document.getElementById("commit-badge");
const reelEl = document.getElementById("reel");
const winnerListEl = document.getElementById("winner-list");
const mcCaptionEl = document.getElementById("mc-caption");
const phaseBannerEl = document.getElementById("racing-phase-banner");
const departmentBarsEl = document.getElementById("department-bars");
const racingFinalEl = document.getElementById("racing-final");
const racingReelEl = document.getElementById("racing-reel");
const racingWinnerListEl = document.getElementById("racing-winner-list");
const predictionLeaderboardEl = document.getElementById("prediction-leaderboard");
const predictionLeaderboardListEl = document.getElementById("prediction-leaderboard-list");
const raceCanvas = document.getElementById("race-canvas");
const raceCtx = raceCanvas.getContext("2d");
const overtakeLayer = document.getElementById("overtake-layer");

let lastPredictionWindow = null;

const animatedDrawKeys = new Set();
let lastOpeningShownFor = null;
let lastFinalShownFor = null;
let racingStarted = false;
let countdownTimer = null;
let latestDepartmentRates = {};

// -- R1~R3: 트랙 렌더러 / 카메라 / 추월 연출 상태 --------------------------
const departmentColorCache = new Map();
let currentPidToGroup = {};
let currentDrawKeyForGroups = null;
let previousTickPositions = {};
let previousTickOrder = [];
let previousTickRound = null;
let lastLaneCount = 20;
let cameraMode = "auto"; // "auto" | "wide" | "medium" | "close" (admin에서 override 가능)

function showView(name) {
  for (const key of Object.keys(views)) {
    views[key].classList.toggle("hidden", key !== name);
  }
}

async function showMcLine(tag, params) {
  try {
    const qs = params
      ? "?" +
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null)
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
          .join("&")
      : "";
    const result = await fetchJSON(`/api/mc/line/${tag}${qs}`);
    mcCaptionEl.textContent = result.text ? `"${result.text}"` : "";
  } catch (e) {
    mcCaptionEl.textContent = "";
  }
}

// 레이스 도중 이벤트(선두 교체·추월)마다 매번 호출하면 자막이 정신없이
// 바뀌므로, 최소 간격을 두고 그 사이 이벤트는 걸러낸다.
const MC_LIVE_COOLDOWN_MS = 4500;
let lastMcLiveCallAt = 0;
function tryShowLiveMcLine(tag, params) {
  const now = Date.now();
  if (now - lastMcLiveCallAt < MC_LIVE_COOLDOWN_MS) return;
  lastMcLiveCallAt = now;
  showMcLine(tag, params);
}

async function spinReel(el, pool, finalText, totalMs) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    el.textContent = pool[Math.floor(Math.random() * pool.length)];
    const elapsed = Date.now() - start;
    const interval = 40 + Math.pow(elapsed / totalMs, 2) * 260;
    await delay(interval);
  }
  el.textContent = finalText;
}

async function playRouletteSequence(draw) {
  const pool = draw.snapshot.participants.map((p) => participantLabel(p));
  const nameById = Object.fromEntries(
    draw.snapshot.participants.map((p) => [p.id, participantLabel(p)])
  );
  showView("drawing");
  winnerListEl.innerHTML = "";
  for (const winnerId of draw.winners) {
    await spinReel(reelEl, pool, nameById[winnerId] || winnerId, 1800);
    const li = document.createElement("li");
    li.textContent = nameById[winnerId] || winnerId;
    winnerListEl.appendChild(li);
    await delay(500);
  }
  reelEl.textContent = "🎊 추첨 완료!";
}

// ---------------------------------------------------------------------------
// 레이싱 모드: 부서 통과율 실시간 랭킹 + 라운드 진행 상태머신
// ---------------------------------------------------------------------------

function renderPredictionLeaderboard(top) {
  if (!top || !top.length) return;
  predictionLeaderboardEl.classList.remove("hidden");
  predictionLeaderboardListEl.innerHTML = top
    .map((entry) => `<li>${entry.participant_id} - ${entry.score}점</li>`)
    .join("");
}

let currentLeaderDept = null;

function renderDepartmentBars(rates) {
  latestDepartmentRates = rates;
  const sorted = Object.entries(rates).sort((a, b) => b[1] - a[1]);
  departmentBarsEl.innerHTML = sorted
    .map(
      ([name, rate]) => `
      <div class="dept-bar-row">
        <div class="dept-bar-label"><span>${name}</span><span>${(rate * 100).toFixed(0)}%</span></div>
        <div class="dept-bar-track"><div class="dept-bar-fill" style="width:${(rate * 100).toFixed(1)}%"></div></div>
      </div>`
    )
    .join("");

  // 선두 부서가 바뀌면(0%끼리의 무의미한 교체는 제외) MC가 반응하도록 한다.
  if (sorted.length && sorted[0][1] > 0) {
    const newLeader = sorted[0][0];
    if (currentLeaderDept !== null && newLeader !== currentLeaderDept) {
      tryShowLiveMcLine("department_rank_shift", { team: newLeader });
    }
    currentLeaderDept = newLeader;
  }
}

function hashToUnit(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return (h % 10000) / 10000; // 0..1
}

// 해시로 색을 만들면 부서 수가 적을 때 비슷한 색조로 몰려 대형 스크린에서
// 구분이 안 된다. 색조·명도가 모두 뚜렷이 갈리는 고정 팔레트를 부서명 정렬
// 순서대로 배정해 항상 최대 대비를 보장한다(같은 명단이면 항상 같은 색).
const TEAM_PALETTE = [
  { base: "#ff5252", glow: "#ff8a80" },
  { base: "#4f8cff", glow: "#82b1ff" },
  { base: "#7cf29c", glow: "#b9f6ca" },
  { base: "#ffd166", glow: "#ffe082" },
  { base: "#c77dff", glow: "#e1bee7" },
  { base: "#ff9f45", glow: "#ffcc80" },
  { base: "#4dd0e1", glow: "#84ffff" },
  { base: "#f06292", glow: "#f8bbd0" },
];

function colorForDepartment(name) {
  const entry = departmentColorCache.get(name);
  return entry ? entry.base : "#8b95a5";
}

function glowForDepartment(name) {
  const entry = departmentColorCache.get(name);
  return entry ? entry.glow : "#b0bac9";
}

function ensureGroupLookup(latest, drawKey) {
  if (currentDrawKeyForGroups === drawKey) return;
  currentDrawKeyForGroups = drawKey;
  const map = {};
  const departments = (latest.snapshot && latest.snapshot.departments) || {};
  for (const [group, ids] of Object.entries(departments)) {
    for (const id of ids) map[id] = group;
  }
  currentPidToGroup = map;

  departmentColorCache.clear();
  const groupNames = Object.keys(departments).sort();
  groupNames.forEach((name, idx) => {
    departmentColorCache.set(name, TEAM_PALETTE[idx % TEAM_PALETTE.length]);
  });
  renderTeamLegend(groupNames);
}

function renderTeamLegend(groupNames) {
  const el = document.getElementById("team-legend");
  if (!el) return;
  el.innerHTML = groupNames
    .map(
      (name) =>
        `<span class="legend-item"><span class="legend-swatch" style="background:${colorForDepartment(
          name
        )}"></span>${name}</span>`
    )
    .join("");
}

function laneFor(pid, laneCount) {
  return Math.floor(hashToUnit(pid + ":lane") * laneCount);
}

function jitterFor(pid) {
  return hashToUnit(pid + ":jitter") - 0.5; // -0.5..0.5
}

function computeCamera(leaderX, round, W, H) {
  let mode = cameraMode;
  if (mode === "auto") {
    mode = round === 1 ? "wide" : round === 2 ? "medium" : "close";
  }
  if (mode === "wide") return { scale: 1, offsetX: 0, offsetY: 0 };
  const scale = mode === "close" ? 2.2 : 1.4;
  return {
    scale,
    offsetX: W / 2 - leaderX * scale,
    offsetY: H / 2 - (H / 2) * scale,
  };
}

function spawnOvertakeBadge(pid) {
  const lane = laneFor(pid, lastLaneCount);
  const x = previousTickPositions[pid] !== undefined ? previousTickPositions[pid] : 0.5;
  const el = document.createElement("div");
  el.className = "overtake-badge";
  el.textContent = "🚀";
  el.style.left = `${Math.min(96, Math.max(4, x * 100))}%`;
  el.style.top = `${Math.min(94, Math.max(6, ((lane + 0.5) / lastLaneCount) * 100))}%`;
  overtakeLayer.appendChild(el);
  setTimeout(() => el.remove(), 700);
}

function detectOvertakes(sortedIds, round) {
  if (previousTickRound === round && previousTickOrder.length) {
    const prevRank = new Map(previousTickOrder.map((pid, idx) => [pid, idx]));
    let badges = 0;
    let mcFired = false;
    for (let i = 0; i < sortedIds.length && badges < 4; i++) {
      const pid = sortedIds[i];
      const prevIdx = prevRank.get(pid);
      if (prevIdx === undefined) continue;
      if (prevIdx - i >= 3) {
        spawnOvertakeBadge(pid);
        badges += 1;
        if (!mcFired) {
          // 쿨다운이 걸려 있으면 tryShowLiveMcLine이 조용히 무시한다
          tryShowLiveMcLine("race_progress", { team: currentPidToGroup[pid] });
          mcFired = true;
        }
      }
    }
  }
  previousTickOrder = sortedIds;
  previousTickRound = round;
}

// ---------------------------------------------------------------------------
// V1: 틱 버퍼 + 60fps 보간 렌더 루프
//
// 서버는 0.3초 간격으로만 위치를 보내므로(대역폭 절약), 틱마다 그리면 화면이
// 초당 3.3회만 갱신되어 레이스가 아니라 "움직이는 차트"처럼 보인다. 마지막
// 두 틱을 버퍼에 두고 그 사이를 requestAnimationFrame으로 보간하면, 서버
// 변경이나 트래픽 증가 없이 부드러운 60fps 주행이 된다(한 틱만큼 뒤처져
// 보여주는 표준 네트코드 방식 -- 관람용 화면에서는 체감되지 않는다).
// ---------------------------------------------------------------------------

const TRACK_PAD_X = 40;
let prevTickState = null;
let currTickState = null;
let tickArrivalAt = 0;
let tickIntervalEstimate = 300;
let rafHandle = null;

function pushTick(tick) {
  const now = performance.now();
  if (currTickState) {
    const delta = now - tickArrivalAt;
    if (delta > 40 && delta < 2000) {
      // 실제 도착 간격으로 보간 구간 길이를 적응시킨다(지연·배속에 대응)
      tickIntervalEstimate = tickIntervalEstimate * 0.7 + delta * 0.3;
    }
  }
  // 라운드가 바뀌면 참가자 집합과 시작 위치가 모두 달라진다. 이전 라운드의
  // 마지막 위치에서 보간하면 카트가 뒤로 미끄러지는 것처럼 보이므로,
  // 라운드 경계에서는 보간 없이 새 위치에서 바로 시작한다.
  const roundChanged = currTickState && currTickState.round !== tick.round;
  prevTickState = roundChanged ? null : currTickState;
  currTickState = tick;
  tickArrivalAt = now;
  if (rafHandle === null) rafHandle = requestAnimationFrame(renderLoop);
}

function stopRenderLoop() {
  if (rafHandle !== null) {
    cancelAnimationFrame(rafHandle);
    rafHandle = null;
  }
  prevTickState = null;
  currTickState = null;
}

function renderLoop() {
  rafHandle = requestAnimationFrame(renderLoop);
  if (!currTickState) return;

  const alpha = prevTickState
    ? Math.min(1, (performance.now() - tickArrivalAt) / tickIntervalEstimate)
    : 1;

  const positions = {};
  const from = prevTickState ? prevTickState.positions : currTickState.positions;
  const to = currTickState.positions;
  for (const pid of Object.keys(to)) {
    const a = from[pid] !== undefined ? from[pid] : to[pid];
    positions[pid] = a + (to[pid] - a) * alpha;
  }

  drawFrame(positions, currTickState);
}

// ---------------------------------------------------------------------------
// V2/V4/V5: 트랙 맵 · 카트 · 긴장 연출
// ---------------------------------------------------------------------------

function drawTrackSurface(ctx, W, H, trackTop, trackBottom, passX, scrollPhase) {
  // 노면
  ctx.fillStyle = "#171d26";
  ctx.fillRect(0, trackTop, W, trackBottom - trackTop);

  // 통과선 기준 위험(왼쪽)/안전(오른쪽) 구역 -- 누가 잘릴 위기인지 한눈에
  const danger = ctx.createLinearGradient(0, 0, passX, 0);
  danger.addColorStop(0, "rgba(179,38,30,0.28)");
  danger.addColorStop(1, "rgba(179,38,30,0.05)");
  ctx.fillStyle = danger;
  ctx.fillRect(0, trackTop, passX, trackBottom - trackTop);

  const safe = ctx.createLinearGradient(passX, 0, W, 0);
  safe.addColorStop(0, "rgba(26,127,55,0.06)");
  safe.addColorStop(1, "rgba(26,127,55,0.20)");
  ctx.fillStyle = safe;
  ctx.fillRect(passX, trackTop, W - passX, trackBottom - trackTop);

  // 상하 커브(빨강/흰색 줄무늬) -- 스크롤시켜 속도감을 준다
  const curbH = 8;
  const stripe = 26;
  for (let x = -stripe; x < W + stripe; x += stripe) {
    const sx = x + ((scrollPhase * stripe * 2) % (stripe * 2));
    const idx = Math.floor((sx + stripe * 4) / stripe) % 2;
    ctx.fillStyle = idx === 0 ? "#c0392b" : "#ecf0f1";
    ctx.fillRect(sx, trackTop - curbH, stripe, curbH);
    ctx.fillRect(sx, trackBottom, stripe, curbH);
  }

  // 중앙 차선 파선 -- 스크롤로 전진감
  ctx.strokeStyle = "rgba(255,255,255,0.16)";
  ctx.lineWidth = 2;
  ctx.setLineDash([22, 20]);
  ctx.lineDashOffset = -(scrollPhase * 84) % 42;
  const rows = 4;
  for (let i = 1; i < rows; i++) {
    const y = trackTop + ((trackBottom - trackTop) * i) / rows;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.lineDashOffset = 0;

  // 출발선
  ctx.fillStyle = "rgba(255,255,255,0.75)";
  ctx.fillRect(TRACK_PAD_X - 6, trackTop, 3, trackBottom - trackTop);
}

function drawFinishLine(ctx, x, trackTop, trackBottom) {
  const cell = 11;
  const cols = 2;
  for (let row = 0; (trackTop + row * cell) < trackBottom; row++) {
    for (let col = 0; col < cols; col++) {
      ctx.fillStyle = (row + col) % 2 === 0 ? "#ffffff" : "#11161d";
      ctx.fillRect(
        x + col * cell,
        trackTop + row * cell,
        cell,
        Math.min(cell, trackBottom - (trackTop + row * cell))
      );
    }
  }
  ctx.strokeStyle = "#ffd166";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x - 1, trackTop);
  ctx.lineTo(x - 1, trackBottom);
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// 카트 스프라이트: 팀 색상마다 오프스크린 캔버스에 "한 번만" 그려두고 매
// 프레임에는 drawImage로 복사만 한다(2D 게임의 표준 스프라이트 기법).
// 250대를 매 프레임 세부 묘사로 다시 그리면 프레임이 무너지지만, 복사는
// 250회여도 부담이 없다. 실제 크기는 레인 높이에 맞춰 스케일된다.
// ---------------------------------------------------------------------------

const kartSpriteCache = new Map();
const SPRITE_W = 96; // 2배 슈퍼샘플 -- 축소해 그리면 가장자리가 매끄럽다
const SPRITE_H = 60;

function roundRectPath(g, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}

function buildKartSprite(color, glow) {
  const c = document.createElement("canvas");
  c.width = SPRITE_W;
  c.height = SPRITE_H;
  const g = c.getContext("2d");
  const W = SPRITE_W;
  const H = SPRITE_H;
  const cy = H / 2;

  // 노면 그림자
  g.fillStyle = "rgba(0,0,0,0.30)";
  g.beginPath();
  g.ellipse(W * 0.52, cy + H * 0.30, W * 0.33, H * 0.12, 0, 0, Math.PI * 2);
  g.fill();

  // 바퀴 (뒤가 크고 앞이 작다 -- 카트 특유의 실루엣)
  g.fillStyle = "#0d1117";
  roundRectPath(g, W * 0.17, cy - H * 0.44, W * 0.21, H * 0.22, 4); g.fill();
  roundRectPath(g, W * 0.17, cy + H * 0.22, W * 0.21, H * 0.22, 4); g.fill();
  roundRectPath(g, W * 0.66, cy - H * 0.40, W * 0.17, H * 0.19, 3); g.fill();
  roundRectPath(g, W * 0.66, cy + H * 0.21, W * 0.17, H * 0.19, 3); g.fill();

  // 리어 윙(스포일러)
  g.fillStyle = glow;
  roundRectPath(g, W * 0.10, cy - H * 0.26, W * 0.07, H * 0.52, 3);
  g.fill();

  // 사이드 포드
  g.fillStyle = color;
  roundRectPath(g, W * 0.28, cy - H * 0.32, W * 0.34, H * 0.64, 5);
  g.fill();

  // 메인 섀시 (뒤에서 앞으로 좁아진다)
  g.beginPath();
  g.moveTo(W * 0.20, cy - H * 0.19);
  g.lineTo(W * 0.72, cy - H * 0.15);
  g.lineTo(W * 0.90, cy - H * 0.07);
  g.lineTo(W * 0.90, cy + H * 0.07);
  g.lineTo(W * 0.72, cy + H * 0.15);
  g.lineTo(W * 0.20, cy + H * 0.19);
  g.closePath();
  g.fillStyle = color;
  g.fill();

  // 상단 하이라이트 (입체감)
  g.fillStyle = "rgba(255,255,255,0.22)";
  roundRectPath(g, W * 0.30, cy - H * 0.28, W * 0.30, H * 0.10, 4);
  g.fill();

  // 콕핏
  g.fillStyle = "rgba(0,0,0,0.55)";
  g.beginPath();
  g.ellipse(W * 0.47, cy, W * 0.09, H * 0.17, 0, 0, Math.PI * 2);
  g.fill();

  // 드라이버 헬멧
  g.fillStyle = glow;
  g.beginPath();
  g.arc(W * 0.47, cy, H * 0.11, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "rgba(0,0,0,0.45)"; // 바이저
  g.beginPath();
  g.ellipse(W * 0.50, cy, H * 0.045, H * 0.07, 0, 0, Math.PI * 2);
  g.fill();

  // 프론트 노즈콘
  g.fillStyle = glow;
  g.beginPath();
  g.moveTo(W * 0.88, cy - H * 0.06);
  g.lineTo(W * 0.98, cy);
  g.lineTo(W * 0.88, cy + H * 0.06);
  g.closePath();
  g.fill();

  return c;
}

function kartSpriteFor(color, glow) {
  const key = color + "|" + glow;
  let sprite = kartSpriteCache.get(key);
  if (!sprite) {
    sprite = buildKartSprite(color, glow);
    kartSpriteCache.set(key, sprite);
  }
  return sprite;
}

function drawKart(ctx, x, y, h, color, glow, isLeader, atRisk, pulse) {
  const w = h * (SPRITE_W / SPRITE_H);

  // 배기 연기 / 속도 자국 -- 카트가 충분히 클 때만(작으면 뭉개져 지저분하다)
  if (h >= 9) {
    for (let t = 1; t <= 3; t++) {
      ctx.globalAlpha = 0.14 / t;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x - w * (0.5 + t * 0.26), y, h * (0.16 + t * 0.05), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  if (isLeader) {
    ctx.shadowColor = glow;
    ctx.shadowBlur = 16;
  } else if (atRisk) {
    ctx.shadowColor = "#ff5252";
    ctx.shadowBlur = 5 + pulse * 9;
  }

  ctx.drawImage(kartSpriteFor(color, glow), x - w / 2, y - h / 2, w, h);
  ctx.shadowBlur = 0;

  // 선두 왕관 -- 카트가 클 때만(결선에서 확실히 보인다)
  if (isLeader && h >= 14) {
    ctx.font = `${Math.round(h * 0.8)}px serif`;
    ctx.textAlign = "center";
    ctx.fillText("👑", x, y - h * 0.75);
    ctx.textAlign = "left";
  }
}

function drawFrame(positions, tick) {
  const ids = Object.keys(positions);
  if (!ids.length) return;
  const W = raceCanvas.width;
  const H = raceCanvas.height;
  const now = performance.now();
  const pulse = (Math.sin(now / 140) + 1) / 2;

  raceCtx.clearRect(0, 0, W, H);
  raceCtx.fillStyle = "#0b0e14";
  raceCtx.fillRect(0, 0, W, H);

  const sorted = [...ids].sort((a, b) => positions[b] - positions[a]);
  const leaderPos = positions[sorted[0]];
  const leaderX = TRACK_PAD_X + leaderPos * (W - TRACK_PAD_X * 2);
  const camera = computeCamera(leaderX, tick.round, W, H);

  const trackTop = 46;
  const trackBottom = H - 30;
  const passX = TRACK_PAD_X + tick.pass_line * (W - TRACK_PAD_X * 2);

  raceCtx.save();
  raceCtx.translate(camera.offsetX, camera.offsetY);
  raceCtx.scale(camera.scale, camera.scale);

  drawTrackSurface(raceCtx, W, H, trackTop, trackBottom, passX, leaderPos);
  drawFinishLine(raceCtx, passX, trackTop, trackBottom);

  const laneCount = Math.max(6, Math.min(36, ids.length));
  lastLaneCount = laneCount;
  const laneHeight = (trackBottom - trackTop) / laneCount;
  // 카트 크기는 레인 높이에서 유도한다 -- 결선(카트 5~10대)에서는 레인이
  // 넓어져 스프라이트 디테일(바퀴·헬멧·스포일러)이 크게 보이고, R1(250대)
  // 에서는 자동으로 작아져 서로 겹치지 않는다.
  const kartH = Math.max(5, Math.min(46, laneHeight * 0.82));

  for (let i = sorted.length - 1; i >= 0; i--) {
    const pid = sorted[i];
    const p = positions[pid];
    const x = TRACK_PAD_X + p * (W - TRACK_PAD_X * 2);
    const lane = laneFor(pid, laneCount);
    const y =
      trackTop + lane * laneHeight + jitterFor(pid) * laneHeight * 0.5 + laneHeight / 2;
    const group = currentPidToGroup[pid];
    const isLeader = i === 0;
    // 통과선 바로 뒤에서 아슬아슬하게 밀린 카트 -- 여기가 가장 긴장되는 지점
    const atRisk = !isLeader && p < tick.pass_line && tick.pass_line - p < 0.06;

    drawKart(
      raceCtx,
      x,
      y,
      kartH,
      group ? colorForDepartment(group) : "#8b95a5",
      group ? glowForDepartment(group) : "#b0bac9",
      isLeader,
      atRisk,
      pulse
    );
  }
  raceCtx.globalAlpha = 1;
  raceCtx.restore();

  drawHud(raceCtx, W, positions, tick, sorted);
}

function drawHud(ctx, W, positions, tick, sorted) {
  // 상단: 진행률 게이지 + 실시간 통과 인원
  const passing = sorted.filter((pid) => positions[pid] >= tick.pass_line).length;
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  ctx.fillRect(TRACK_PAD_X, 18, W - TRACK_PAD_X * 2, 6);
  ctx.fillStyle = "#4f8cff";
  ctx.fillRect(TRACK_PAD_X, 18, (W - TRACK_PAD_X * 2) * tick.progress_ratio, 6);

  ctx.font = "bold 16px 'Malgun Gothic', sans-serif";
  ctx.fillStyle = "#7cf29c";
  ctx.textAlign = "left";
  ctx.fillText(`통과권 ${passing}대`, TRACK_PAD_X, 40);

  ctx.textAlign = "right";
  ctx.fillStyle = "#8b95a5";
  ctx.fillText(`${Math.round(tick.progress_ratio * 100)}%`, W - TRACK_PAD_X, 40);
  ctx.textAlign = "left";
}

function renderTrack(tick) {
  pushTick(tick);
  const sorted = Object.keys(tick.positions).sort(
    (a, b) => tick.positions[b] - tick.positions[a]
  );
  detectOvertakes(sorted, tick.round);
  previousTickPositions = tick.positions;
}

function startCountdown(durationSeconds, startedAtIso, phaseLabel) {
  if (countdownTimer) clearInterval(countdownTimer);
  const startedAt = new Date(startedAtIso).getTime();
  const update = () => {
    const remain = Math.max(0, durationSeconds - (Date.now() - startedAt) / 1000);
    phaseBannerEl.innerHTML = `${phaseLabel} <span class="countdown">${remain.toFixed(1)}초</span>`;
    if (remain <= 0) clearInterval(countdownTimer);
  };
  update();
  countdownTimer = setInterval(update, 100);
}

const PHASE_LABELS = {
  opening: "오프닝",
  r1_lock: "1라운드 준비",
  race_r1: "1라운드 레이스",
  score_r1_select_r2: "1라운드 결과 발표",
  race_r2: "2라운드 레이스",
  score_r2_select_r3: "2라운드 결과 발표",
  race_r3: "결선",
  final_announce: "최종 발표",
  verify: "공정성 검증",
};

function handleRacingEvent(data) {
  if (data.type === "phase") {
    racingStarted = true;
    showView("racing");
    racingFinalEl.classList.add("hidden");
    startCountdown(data.duration_seconds, data.started_at, PHASE_LABELS[data.phase] || data.phase);
    if (["race_r1", "race_r2", "race_r3"].includes(data.phase)) {
      // 라운드가 바뀌면 이전 라운드의 선두 부서 정보를 들고 넘어가지 않도록 리셋
      currentLeaderDept = null;
    }
    if (data.phase === "race_r1") showMcLine("opening");
    if (data.phase === "race_r3") showMcLine("race_progress");
  } else if (data.type === "race_tick") {
    renderTrack(data);
    if (data.department_live_rate) {
      renderDepartmentBars(data.department_live_rate);
    }
  } else if (data.type === "round_revealed") {
    showMcLine("round_pass_announce", { pass_count: data.pass_ids ? data.pass_ids.length : undefined });
  } else if (data.type === "racing_complete") {
    if (countdownTimer) clearInterval(countdownTimer);
    phaseBannerEl.textContent = "진행 완료";
    // 마지막 프레임(최종 위치)을 화면에 남긴 채 루프만 정지한다
    if (rafHandle !== null) {
      cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }
  }
}

// ---------------------------------------------------------------------------
// 세션 상태 조회(폴링/이벤트 트리거) -- 룰렛 모드 렌더링
// ---------------------------------------------------------------------------

function render(session) {
  if (!session) {
    showView("idle");
    statusEl.textContent = "명단 등록을 기다리는 중입니다";
    return;
  }
  const latest = session.draws[session.draws.length - 1];
  if (!latest) {
    const departmentCount = new Set(session.participants.map((p) => p.team || "미지정")).size;
    participantCountEl.textContent = session.participants.length;
    departmentCountEl.textContent = departmentCount;
    showView("waiting");
    return;
  }

  if (session.mode === "racing") {
    ensureGroupLookup(latest, latest.commit);
    if (latest.revealed) {
      showView("racing");
      racingFinalEl.classList.remove("hidden");
      racingReelEl.textContent = "🎊 최종 당첨자 발표!";
      racingWinnerListEl.innerHTML = latest.winners
        .map((id) => {
          const p = latest.snapshot.participants.find((x) => x.id === id);
          return `<li>${p ? participantLabel(p) : id}</li>`;
        })
        .join("");
      if (lastFinalShownFor !== "racing-final") {
        lastFinalShownFor = "racing-final";
        showMcLine("final_announce").then(() => delay(2500)).then(() => showMcLine("verification"));
      }
      return;
    }
    if (racingStarted) {
      showView("racing");
      return; // race_tick/phase 이벤트가 실시간 갱신을 담당
    }
    commitBadgeEl.textContent = latest.commit;
    showView("committed");
    if (lastOpeningShownFor !== latest.commit) {
      lastOpeningShownFor = latest.commit;
      showMcLine("opening");
    }
    return;
  }

  // 룰렛 모드
  if (!latest.revealed) {
    commitBadgeEl.textContent = latest.commit;
    showView("committed");
    if (lastOpeningShownFor !== latest.commit) {
      lastOpeningShownFor = latest.commit;
      showMcLine("opening");
    }
    return;
  }
  const drawKey = `${session.draws.length - 1}:${latest.revealed_at}`;
  if (!animatedDrawKeys.has(drawKey)) {
    animatedDrawKeys.add(drawKey);
    playRouletteSequence(latest).then(() => {
      if (lastFinalShownFor !== drawKey) {
        lastFinalShownFor = drawKey;
        showMcLine("final_announce").then(() => delay(2500)).then(() => showMcLine("verification"));
      }
    });
  } else {
    showView("drawing");
    winnerListEl.innerHTML = latest.winners
      .map((id) => {
        const p = latest.snapshot.participants.find((x) => x.id === id);
        return `<li>${p ? participantLabel(p) : id}</li>`;
      })
      .join("");
    reelEl.textContent = "🎊 추첨 완료!";
  }
}

document.getElementById("btn-demo-start").addEventListener("click", async (event) => {
  event.target.disabled = true;
  event.target.textContent = "데모 준비 중...";
  try {
    await fetchJSON("/api/demo/start", { method: "POST", body: JSON.stringify({}) });
  } catch (e) {
    alert(e.message);
    event.target.disabled = false;
    event.target.textContent = "🚀 데모로 체험하기";
  }
});

async function refresh() {
  try {
    const session = await fetchJSON("/api/session");
    render(session);
  } catch (e) {
    if (e.status === 404) {
      render(null);
    }
  }
}

const ws = connectWS((data) => {
  if (data.type === "reset") {
    racingStarted = false;
    lastOpeningShownFor = null;
    lastFinalShownFor = null;
    currentDrawKeyForGroups = null;
    currentPidToGroup = {};
    previousTickPositions = {};
    previousTickOrder = [];
    previousTickRound = null;
    currentLeaderDept = null;
    lastMcLiveCallAt = 0;
    stopRenderLoop();
    raceCtx.clearRect(0, 0, raceCanvas.width, raceCanvas.height);
    overtakeLayer.innerHTML = "";
    const legendEl = document.getElementById("team-legend");
    if (legendEl) legendEl.innerHTML = "";
    if (countdownTimer) clearInterval(countdownTimer);
  }
  if (data.type === "camera_mode") {
    cameraMode = data.mode;
  }
  if (["phase", "race_tick", "round_revealed", "racing_complete"].includes(data.type)) {
    handleRacingEvent(data);
  }
  if (data.type === "prediction_leaderboard") {
    renderPredictionLeaderboard(data.top);
  }
  if (data.type === "prediction_window") {
    lastPredictionWindow = data;
  }
  refresh();
}, "stage");
ws.addEventListener("open", () => {
  refresh();
});

refresh();
