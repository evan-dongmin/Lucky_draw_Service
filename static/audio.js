/**
 * 무대 화면 효과음 엔진 (WebAudio 합성 전용).
 *
 * 설계 원칙
 * - **외부 오디오 파일을 쓰지 않는다.** 행사장 네트워크가 불안정하거나 오프라인
 *   PC에서 실행돼도 소리가 나야 하고, 저장소에 라이선스 있는 음원을 넣지
 *   않기 위해서다. 모든 소리는 오실레이터·노이즈 버퍼로 즉석 합성한다.
 * - **브라우저 자동재생 정책**: AudioContext는 사용자 제스처 이후에만 소리를
 *   낼 수 있다. 첫 클릭/키입력에서 unlock()이 호출되도록 stage.js가 연결한다.
 * - **실패해도 진행을 막지 않는다.** WebAudio 미지원·차단 시 모든 함수는
 *   조용히 no-op이 된다(추첨 진행 자체는 소리와 무관해야 한다).
 */

const SFX = (() => {
  let ctx = null;
  let master = null;
  let duckGain = null; // MC 음성이 나올 때 배경음을 낮추는 단계
  let enabled = true;
  let unlocked = false;
  let noiseBuffer = null;

  // 엔진음 노드(레이스 구간에만 살아 있음)
  let engine = null;

  function supported() {
    return typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
  }

  function ensureCtx() {
    if (!enabled || !supported()) return null;
    if (!ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      ctx = new Ctor();
      master = ctx.createGain();
      master.gain.value = 0.9;
      duckGain = ctx.createGain();
      duckGain.gain.value = 1;
      master.connect(duckGain);
      duckGain.connect(ctx.destination);
      noiseBuffer = makeNoiseBuffer();
    }
    return ctx;
  }

  function makeNoiseBuffer() {
    const len = ctx.sampleRate * 2;
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    return buf;
  }

  function now() {
    return ctx.currentTime;
  }

  /** 사용자 제스처 안에서 호출해야 실제로 소리가 난다. */
  function unlock() {
    const c = ensureCtx();
    if (!c) return false;
    if (c.state === "suspended") c.resume();
    unlocked = true;
    return true;
  }

  function setEnabled(value) {
    enabled = value;
    if (!enabled) {
      stopEngine();
      if (master) master.gain.value = 0;
    } else if (master) {
      master.gain.value = 0.9;
    }
  }

  function isEnabled() {
    return enabled;
  }

  function isReady() {
    return enabled && unlocked && ctx !== null;
  }

  /** MC 음성 구간 동안 배경음을 줄인다(더킹) -- 멘트가 묻히지 않도록. */
  function duck(amount = 0.28, seconds = 0.15) {
    if (!isReady()) return;
    duckGain.gain.cancelScheduledValues(now());
    duckGain.gain.setTargetAtTime(amount, now(), seconds);
  }

  function unduck(seconds = 0.35) {
    if (!isReady()) return;
    duckGain.gain.cancelScheduledValues(now());
    duckGain.gain.setTargetAtTime(1, now(), seconds);
  }

  // -- 기본 빌딩 블록 -------------------------------------------------------

  function tone({ freq = 440, dur = 0.2, type = "sine", gain = 0.2, at = 0, sweepTo = null, attack = 0.005 }) {
    if (!isReady()) return;
    const t = now() + at;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    if (sweepTo !== null) osc.frequency.exponentialRampToValueAtTime(Math.max(1, sweepTo), t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g);
    g.connect(master);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  function noise({ dur = 0.3, gain = 0.2, at = 0, filter = "bandpass", freq = 1200, q = 1, sweepTo = null }) {
    if (!isReady()) return;
    const t = now() + at;
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer;
    src.loop = true;
    const bp = ctx.createBiquadFilter();
    bp.type = filter;
    bp.frequency.setValueAtTime(freq, t);
    bp.Q.value = q;
    if (sweepTo !== null) bp.frequency.exponentialRampToValueAtTime(Math.max(20, sweepTo), t + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + Math.min(0.03, dur * 0.2));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp);
    bp.connect(g);
    g.connect(master);
    src.start(t);
    src.stop(t + dur + 0.05);
  }

  // -- 상황별 효과음 --------------------------------------------------------

  /** F1 스타트 라이트: 빨간 등이 하나씩 켜질 때마다. */
  function startLight(index) {
    tone({ freq: 520, dur: 0.16, type: "square", gain: 0.16 });
    tone({ freq: 260, dur: 0.2, type: "sine", gain: 0.1, at: 0.005 });
  }

  /** 라이트 소등 = 출발. 화면에서 가장 강한 순간이므로 소리도 가장 크게. */
  function lightsOut() {
    tone({ freq: 880, dur: 0.5, type: "square", gain: 0.26 });
    tone({ freq: 440, dur: 0.6, type: "sawtooth", gain: 0.2, at: 0.01 });
    noise({ dur: 0.7, gain: 0.22, freq: 400, sweepTo: 4000, q: 0.7 });
  }

  /** 추월/부스터. 지나가는 소리(도플러 흉내: 밴드패스 스윕). */
  function whoosh() {
    noise({ dur: 0.36, gain: 0.16, freq: 2600, sweepTo: 420, q: 3 });
  }

  /** 통과선 아래로 밀려 탈락 위기 -- 낮고 둔탁한 경고. */
  function warn() {
    tone({ freq: 180, dur: 0.22, type: "sawtooth", gain: 0.12, sweepTo: 90 });
  }

  /** 라운드 통과 확정 발표. */
  function pass() {
    const notes = [523.25, 659.25, 783.99];
    notes.forEach((f, i) => tone({ freq: f, dur: 0.28, type: "triangle", gain: 0.18, at: i * 0.09 }));
  }

  /** 탈락 확정. */
  function eliminate() {
    tone({ freq: 300, dur: 0.5, type: "sawtooth", gain: 0.14, sweepTo: 110 });
    noise({ dur: 0.4, gain: 0.1, freq: 900, sweepTo: 200, q: 1 });
  }

  /** UI 틱(카운트다운 마지막 5초 등). */
  function tick(high = false) {
    tone({ freq: high ? 1320 : 880, dur: 0.07, type: "square", gain: 0.12 });
  }

  /** 발표 직전 드럼롤. seconds 동안 점점 빨라지고 커진다. */
  function drumroll(seconds = 2.0) {
    if (!isReady()) return;
    let t = 0;
    let interval = 0.09;
    while (t < seconds) {
      const progress = t / seconds;
      noise({
        dur: 0.06,
        gain: 0.06 + progress * 0.12,
        freq: 180,
        q: 0.8,
        filter: "lowpass",
        at: t,
      });
      interval = 0.09 - progress * 0.055;
      t += Math.max(0.03, interval);
    }
  }

  /** 최종 발표 팡파레. */
  function fanfare() {
    const seq = [
      [523.25, 0.0, 0.18],
      [659.25, 0.16, 0.18],
      [783.99, 0.32, 0.18],
      [1046.5, 0.48, 0.55],
    ];
    for (const [f, at, dur] of seq) {
      tone({ freq: f, dur, type: "triangle", gain: 0.22, at });
      tone({ freq: f / 2, dur, type: "sine", gain: 0.12, at });
    }
    noise({ dur: 1.2, gain: 0.1, freq: 3000, sweepTo: 800, q: 0.6, at: 0.48 });
  }

  /** 폭죽/컨페티 터지는 소리. */
  function pop() {
    noise({ dur: 0.18, gain: 0.16, freq: 1800, sweepTo: 300, q: 2 });
    tone({ freq: 1200, dur: 0.1, type: "square", gain: 0.08, sweepTo: 400 });
  }

  /**
   * 관중 함성. 필터드 노이즈를 천천히 열었다 닫아 "와아—" 소리를 만든다.
   * intensity 0..1로 크기와 길이를 조절한다.
   */
  function crowd(intensity = 0.6, dur = 1.6) {
    if (!isReady()) return;
    const t = now();
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer;
    src.loop = true;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.setValueAtTime(600, t);
    bp.frequency.linearRampToValueAtTime(1500, t + dur * 0.35);
    bp.frequency.linearRampToValueAtTime(500, t + dur);
    bp.Q.value = 0.6;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.16 * intensity, t + dur * 0.3);
    g.gain.linearRampToValueAtTime(0.0001, t + dur);
    src.connect(bp);
    bp.connect(g);
    g.connect(master);
    src.start(t);
    src.stop(t + dur + 0.1);
  }

  /** 긴장 고조용 심장박동(결선 막판). */
  function heartbeat() {
    tone({ freq: 62, dur: 0.16, type: "sine", gain: 0.22 });
    tone({ freq: 55, dur: 0.2, type: "sine", gain: 0.16, at: 0.19 });
  }

  // -- 엔진음(레이스 구간 지속음) -------------------------------------------

  /**
   * 카트 엔진음. 두 개의 디튠된 톱니파 + 노이즈를 로우패스에 통과시켜
   * "웅—" 하는 집단 주행음을 만든다. setRpm()으로 선두 속도에 맞춰 피치를
   * 올리면 레이스 후반의 가속감이 소리로도 전달된다.
   */
  function startEngine() {
    if (!isReady() || engine) return;
    const t = now();
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.09, t + 0.8);

    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 900;
    lp.Q.value = 0.8;

    const o1 = ctx.createOscillator();
    o1.type = "sawtooth";
    o1.frequency.value = 70;
    const o2 = ctx.createOscillator();
    o2.type = "sawtooth";
    o2.frequency.value = 70 * 1.011; // 살짝 어긋나 여러 대가 함께 달리는 느낌
    const o3 = ctx.createOscillator();
    o3.type = "square";
    o3.frequency.value = 35;

    const ng = ctx.createGain();
    ng.gain.value = 0.35;
    const n = ctx.createBufferSource();
    n.buffer = noiseBuffer;
    n.loop = true;

    o1.connect(lp);
    o2.connect(lp);
    o3.connect(lp);
    n.connect(ng);
    ng.connect(lp);
    lp.connect(g);
    g.connect(master);

    o1.start(t);
    o2.start(t);
    o3.start(t);
    n.start(t);
    engine = { g, lp, o1, o2, o3, n, base: 70 };
  }

  /** rpm 0..1 -- 레이스 진행률이나 선두 속도를 그대로 넘기면 된다. */
  function setRpm(rpm) {
    if (!engine || !isReady()) return;
    const r = Math.max(0, Math.min(1, rpm));
    const f = engine.base * (1 + r * 1.35);
    const t = now();
    engine.o1.frequency.setTargetAtTime(f, t, 0.25);
    engine.o2.frequency.setTargetAtTime(f * 1.011, t, 0.25);
    engine.o3.frequency.setTargetAtTime(f / 2, t, 0.25);
    engine.lp.frequency.setTargetAtTime(700 + r * 1500, t, 0.3);
  }

  function stopEngine() {
    if (!engine || !ctx) return;
    const t = ctx.currentTime;
    try {
      engine.g.gain.cancelScheduledValues(t);
      engine.g.gain.setTargetAtTime(0.0001, t, 0.25);
      const e = engine;
      setTimeout(() => {
        try {
          e.o1.stop();
          e.o2.stop();
          e.o3.stop();
          e.n.stop();
        } catch (err) {
          /* 이미 정지됨 */
        }
      }, 900);
    } catch (err) {
      /* noop */
    }
    engine = null;
  }

  return {
    unlock,
    setEnabled,
    isEnabled,
    isReady,
    duck,
    unduck,
    startLight,
    lightsOut,
    whoosh,
    warn,
    pass,
    eliminate,
    tick,
    drumroll,
    fanfare,
    pop,
    crowd,
    heartbeat,
    startEngine,
    setRpm,
    stopEngine,
  };
})();
