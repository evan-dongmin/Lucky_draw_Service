/**
 * 무대 화면 비주얼 이펙트 엔진.
 *
 * 레이스 렌더러(stage.js)와 분리된 **독립 오버레이 캔버스 + 자체 RAF 루프**로
 * 동작한다. 레이스가 끝난 뒤(포디움·컨페티)에도 계속 돌아야 하고, 반대로
 * 이펙트가 죽어도 레이스 렌더링은 영향을 받지 않아야 하기 때문이다.
 *
 * 파티클이 하나도 없으면 루프를 스스로 멈춰 유휴 상태에서 CPU를 쓰지 않는다.
 */

const FX = (() => {
  let canvas = null;
  let ctx = null;
  let shakeTarget = null;
  let raf = null;
  let lastTs = 0;

  let particles = [];
  let rings = [];
  let flash = null;
  let shake = null;
  let speedLineLevel = 0;
  let speedLines = [];

  const GRAVITY = 620; // px/s^2

  function attach(canvasEl, shakeEl) {
    canvas = canvasEl;
    ctx = canvas.getContext("2d");
    shakeTarget = shakeEl || null;
  }

  function resize(cssW, cssH, dpr) {
    if (!canvas) return;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function size() {
    if (!canvas) return { w: 0, h: 0 };
    const dpr = canvas.width / (parseFloat(canvas.style.width) || canvas.width);
    return { w: canvas.width / dpr, h: canvas.height / dpr };
  }

  function ensureLoop() {
    if (raf === null) {
      lastTs = performance.now();
      raf = requestAnimationFrame(step);
    }
  }

  function idle() {
    return (
      particles.length === 0 &&
      rings.length === 0 &&
      speedLines.length === 0 &&
      speedLineLevel <= 0.01 &&
      flash === null &&
      shake === null
    );
  }

  // -- 발생기 ---------------------------------------------------------------

  const CONFETTI_COLORS = [
    "#ff5252", "#4f8cff", "#7cf29c", "#ffd166",
    "#c77dff", "#ff9f45", "#4dd0e1", "#f06292", "#ffffff",
  ];

  /** 화면 위에서 쏟아지는 컨페티. 최종 발표·포디움용. */
  function confetti(count = 160, opts = {}) {
    const { w } = size();
    for (let i = 0; i < count; i++) {
      particles.push({
        kind: "confetti",
        x: opts.x !== undefined ? opts.x : Math.random() * w,
        y: opts.y !== undefined ? opts.y : -20 - Math.random() * 220,
        vx: (Math.random() - 0.5) * 190,
        vy: 80 + Math.random() * 260,
        w: 5 + Math.random() * 8,
        h: 8 + Math.random() * 12,
        rot: Math.random() * Math.PI * 2,
        vrot: (Math.random() - 0.5) * 12,
        color: opts.color || CONFETTI_COLORS[(Math.random() * CONFETTI_COLORS.length) | 0],
        life: 3.4 + Math.random() * 2.2,
        age: 0,
        drag: 0.4,
      });
    }
    ensureLoop();
  }

  /** 한 지점에서 터지는 불꽃(추월·통과·피니시). */
  function burst(x, y, color = "#ffd166", count = 22, speed = 320) {
    for (let i = 0; i < count; i++) {
      const a = Math.random() * Math.PI * 2;
      const s = speed * (0.35 + Math.random() * 0.9);
      particles.push({
        kind: "spark",
        x,
        y,
        vx: Math.cos(a) * s,
        vy: Math.sin(a) * s,
        r: 1.6 + Math.random() * 2.8,
        color,
        life: 0.45 + Math.random() * 0.55,
        age: 0,
        drag: 2.2,
      });
    }
    ensureLoop();
  }

  /** 퍼져나가는 충격파 링(부스터·선두 교체). */
  function ring(x, y, color = "#ffd166", maxR = 90) {
    rings.push({ x, y, r: 6, maxR, color, life: 0.55, age: 0 });
    ensureLoop();
  }

  /** 화면 전체 플래시(라이트 아웃·포토피니시). */
  function screenFlash(color = "rgba(255,255,255,0.85)", ms = 220) {
    flash = { color, ms, age: 0 };
    ensureLoop();
  }

  /** 화면 흔들림. 강한 순간(출발·피니시)에 짧게. */
  function screenShake(magnitude = 12, ms = 380) {
    shake = { magnitude, ms, age: 0 };
    ensureLoop();
  }

  /**
   * 속도선. level 0..1로 강도를 조절하며, 레이스 진행률이 올라갈수록
   * 짙어지도록 stage.js가 매 틱 갱신한다(0이면 서서히 사라진다).
   */
  function setSpeedLines(level) {
    speedLineLevel = Math.max(0, Math.min(1, level));
    if (speedLineLevel > 0.01) ensureLoop();
  }

  function clear() {
    particles = [];
    rings = [];
    speedLines = [];
    speedLineLevel = 0;
    flash = null;
    if (shakeTarget) shakeTarget.style.transform = "";
    shake = null;
    if (ctx) {
      const { w, h } = size();
      ctx.clearRect(0, 0, w, h);
    }
  }

  // -- 루프 -----------------------------------------------------------------

  function step(ts) {
    // **음수 방지가 핵심이다.** ensureLoop가 lastTs를 performance.now()로
    // 잡아두는데, requestAnimationFrame이 넘겨주는 ts는 "그 프레임의 시작
    // 시각"이라 방금 읽은 performance.now()보다 **이전일 수 있다**. 그러면
    // dt가 음수가 되고 age가 음수로 내려가, 링 반지름이
    // `6 + (maxR-6) * (age/life)` 로 음수가 되어 ctx.arc가 예외를 던진다 --
    // 그 순간 draw()가 통째로 중단돼 컨페티·불꽃까지 한 프레임 통으로
    // 사라진다(실측: dt -28ms에서 반지름 -0.85로 예외 발생).
    const dt = Math.max(0, Math.min(0.05, (ts - lastTs) / 1000));
    lastTs = ts;
    update(dt);
    draw();
    if (idle()) {
      raf = null;
      if (ctx) {
        const { w, h } = size();
        ctx.clearRect(0, 0, w, h);
      }
      return;
    }
    raf = requestAnimationFrame(step);
  }

  function update(dt) {
    const { w, h } = size();

    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.age += dt;
      if (p.age >= p.life || p.y > h + 60) {
        particles.splice(i, 1);
        continue;
      }
      const dragK = Math.exp(-p.drag * dt);
      p.vx *= dragK;
      if (p.kind === "confetti") {
        p.vy = p.vy * dragK + GRAVITY * 0.42 * dt;
        p.rot += p.vrot * dt;
        p.x += Math.sin((p.age + p.rot) * 3) * 22 * dt; // 나풀거림
      } else {
        p.vy = p.vy * dragK + GRAVITY * dt;
      }
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }

    for (let i = rings.length - 1; i >= 0; i--) {
      const r = rings[i];
      r.age += dt;
      if (r.age >= r.life) {
        rings.splice(i, 1);
        continue;
      }
      r.r = 6 + (r.maxR - 6) * (r.age / r.life);
    }

    // 속도선: 강도에 비례해 생성하고 왼쪽으로 흘려보낸다
    if (speedLineLevel > 0.01) {
      const spawn = speedLineLevel * 90 * dt;
      let n = Math.floor(spawn) + (Math.random() < spawn % 1 ? 1 : 0);
      while (n-- > 0) {
        speedLines.push({
          x: w + 40,
          y: Math.random() * h,
          len: 60 + Math.random() * 240 * speedLineLevel,
          speed: 900 + Math.random() * 1600 * speedLineLevel,
          alpha: 0.05 + Math.random() * 0.18 * speedLineLevel,
        });
      }
    }
    for (let i = speedLines.length - 1; i >= 0; i--) {
      const s = speedLines[i];
      s.x -= s.speed * dt;
      if (s.x + s.len < -20) speedLines.splice(i, 1);
    }

    if (flash) {
      flash.age += dt * 1000;
      if (flash.age >= flash.ms) flash = null;
    }

    if (shake) {
      shake.age += dt * 1000;
      if (shake.age >= shake.ms) {
        shake = null;
        if (shakeTarget) shakeTarget.style.transform = "";
      } else if (shakeTarget) {
        const decay = 1 - shake.age / shake.ms;
        const m = shake.magnitude * decay * decay;
        const dx = (Math.random() - 0.5) * 2 * m;
        const dy = (Math.random() - 0.5) * 2 * m;
        shakeTarget.style.transform = `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px)`;
      }
    }
  }

  function draw() {
    if (!ctx) return;
    const { w, h } = size();
    ctx.clearRect(0, 0, w, h);

    // 속도선(가장 뒤)
    ctx.lineCap = "round";
    for (const s of speedLines) {
      ctx.globalAlpha = s.alpha;
      ctx.strokeStyle = "#dbe7ff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(s.x + s.len, s.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    for (const r of rings) {
      const t = r.age / r.life;
      ctx.globalAlpha = (1 - t) * 0.8;
      ctx.strokeStyle = r.color;
      ctx.lineWidth = 3 * (1 - t) + 1;
      ctx.beginPath();
      // 반지름은 위 dt 보정으로 음수가 될 수 없지만, 한 번 음수가 나오면
      // 예외가 draw() 전체를 중단시켜 다른 연출까지 죽는다. 값싼 보험을 둔다.
      ctx.arc(r.x, r.y, Math.max(0, r.r), 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    for (const p of particles) {
      const t = p.age / p.life;
      ctx.globalAlpha = t > 0.75 ? (1 - t) / 0.25 : 1;
      if (p.kind === "confetti") {
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        // 회전에 따라 폭이 줄어드는 것처럼 보이게 해 종잇조각 느낌을 준다
        const wobble = Math.abs(Math.cos(p.rot * 1.7));
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w * (0.25 + wobble * 0.75), p.h);
        ctx.restore();
      } else {
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, Math.max(0, p.r), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    if (flash) {
      const t = flash.age / flash.ms;
      ctx.globalAlpha = Math.max(0, 1 - t);
      ctx.fillStyle = flash.color;
      ctx.fillRect(0, 0, w, h);
      ctx.globalAlpha = 1;
    }
  }

  return {
    attach,
    resize,
    confetti,
    burst,
    ring,
    screenFlash,
    screenShake,
    setSpeedLines,
    clear,
  };
})();
