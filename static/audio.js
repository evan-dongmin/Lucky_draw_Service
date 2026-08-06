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
 * - **동시다발 이벤트는 솎아낸다.** 장애물 충돌·결승선 통과는 한 프레임에
 *   수십 대가 동시에 일으킬 수 있어, 게이트(gate)로 총량을 제한하고 마지막
 *   단의 리미터로 피크를 눌러 스피커가 찌그러지지 않게 한다.
 *
 * 구성
 * - 한숏 효과음: 스타트/추월/충돌(6종)/통과/종/상승음/팡파레/함성 등
 * - 지속음: 엔진음(setRpm으로 실시간 피치)
 * - BGM: 장면별 루프. 대기(idle/standby) · 선택(anticipation) ·
 *   레이스(race1/race2/race3, 라운드마다 다른 곡) · 결과(victory) · 룰렛
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

  // 장면별 배경음악(BGM) 상태 -- 아래 "장면별 배경음악" 섹션 참고
  let sceneGain = null;
  let sceneName = null;
  let sceneTimer = null;
  let sceneGeneration = 0;
  let sceneParams = {};
  let sceneIntensity = 0.4;
  let sceneBarIndex = 0;

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
      // 리미터. BGM(화음+드럼) + 엔진음 + 동시다발 충돌음이 겹치는 순간이
      // 있어 그대로 내보내면 행사장 스피커에서 찌그러진다. 마지막 단에
      // 컴프레서를 물려 피크만 눌러준다(평상시 음색에는 거의 영향 없음).
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -8;
      limiter.knee.value = 6;
      limiter.ratio.value = 12;
      limiter.attack.value = 0.003;
      limiter.release.value = 0.25;
      master.connect(duckGain);
      duckGain.connect(limiter);
      limiter.connect(ctx.destination);
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

  /**
   * 같은 계열 소리가 짧은 시간에 겹쳐 터지는 것을 막는 게이트.
   * 장애물 충돌·결승선 통과처럼 "여러 대가 동시에" 일으키는 이벤트는
   * 그대로 다 울리면 노이즈 덩어리가 되고 마스터가 클리핑된다.
   * 통과하면 true, 막히면 false.
   */
  const gateAt = {};
  function gate(key, ms) {
    const t = Date.now();
    if (gateAt[key] !== undefined && t - gateAt[key] < ms) return false;
    gateAt[key] = t;
    return true;
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

  // -- 장애물 충돌 ----------------------------------------------------------
  //
  // 장애물 종류(정확히는 OBSTACLE_DEFS의 kind)마다 다른 소리를 낸다. 한
  // 프레임에 수십 대가 동시에 부딪힐 수 있으므로 (1) 종류별 게이트 (2) 전체
  // 게이트 (3) 순위 기반 볼륨 스케일(scale) 세 겹으로 총량을 억제한다.
  // scale은 stage.js가 "선두권일수록 1에 가깝게" 넘겨준다.

  const HIT_SOUNDS = {
    /** 라바콘 -- 가볍게 툭 치고 지나가는 플라스틱 소리. */
    wobble(s) {
      tone({ freq: 430, dur: 0.07, type: "square", gain: 0.075 * s, sweepTo: 240 });
      noise({ dur: 0.09, gain: 0.06 * s, freq: 2800, q: 1.2, filter: "highpass" });
    },
    /** 기름/타이어 -- 둔탁한 고무 충격 + 짧게 끌리는 마찰. */
    slow(s) {
      tone({ freq: 140, dur: 0.2, type: "sine", gain: 0.13 * s, sweepTo: 62 });
      noise({ dur: 0.26, gain: 0.07 * s, freq: 500, sweepTo: 180, q: 0.9, filter: "lowpass" });
    },
    /** 물웅덩이/빙판 -- 타이어가 미끄러지는 스키드음. */
    slide(s) {
      noise({ dur: 0.42, gain: 0.1 * s, freq: 2400, sweepTo: 900, q: 6 });
      tone({ freq: 620, dur: 0.32, type: "sawtooth", gain: 0.05 * s, sweepTo: 300 });
    },
    /** 바나나 -- 팽이처럼 도는 코믹한 하강음 + 스키드. */
    spin(s) {
      tone({ freq: 980, dur: 0.5, type: "triangle", gain: 0.11 * s, sweepTo: 170 });
      noise({ dur: 0.4, gain: 0.07 * s, freq: 1800, sweepTo: 700, q: 4 });
    },
    /** 바위 -- 묵직한 정면 충돌. 화면상 가장 크게 밀리는 효과라 소리도 세게. */
    stall(s) {
      tone({ freq: 110, dur: 0.34, type: "sine", gain: 0.2 * s, sweepTo: 42 });
      noise({ dur: 0.3, gain: 0.14 * s, freq: 900, sweepTo: 140, q: 0.7 });
      tone({ freq: 240, dur: 0.1, type: "square", gain: 0.08 * s, sweepTo: 90 });
    },
    /** 폭탄 -- 저역 붐 + 파편 크래클. 전체에서 가장 눈에 띄는 순간. */
    explode(s) {
      tone({ freq: 95, dur: 0.6, type: "sine", gain: 0.26 * s, sweepTo: 32 });
      noise({ dur: 0.55, gain: 0.2 * s, freq: 1600, sweepTo: 120, q: 0.5 });
      noise({ dur: 0.22, gain: 0.1 * s, freq: 5200, sweepTo: 1800, q: 1.5, filter: "highpass" });
    },
  };

  /**
   * 장애물 충돌음.
   * @param {string} kind wobble|slow|slide|spin|stall|explode
   * @param {number} scale 0.15~1.4 볼륨 배율(선두권일수록 크게)
   */
  function hit(kind, scale = 1) {
    if (!isReady()) return;
    const fn = HIT_SOUNDS[kind];
    if (!fn) return;
    const heavy = kind === "explode" || kind === "stall";
    if (!gate(`hit:${kind}`, heavy ? 280 : 150)) return;
    if (!gate("hit:any", heavy ? 0 : 70)) return; // 큰 충돌은 전체 게이트를 무시
    fn(Math.max(0.15, Math.min(1.4, scale)));
  }

  /**
   * 결승(통과)선 통과음. rank 0(선두)은 체커기 + 함성까지 크게 가고,
   * 뒤따라 들어오는 카트는 짧은 통과 블립만 낸다(수백 대가 지나가도
   * 소리가 뭉치지 않도록 게이트로 솎는다).
   */
  function finishCross(rank = 99) {
    if (!isReady()) return;
    if (rank === 0) {
      noise({ dur: 0.5, gain: 0.17, freq: 3400, sweepTo: 600, q: 0.8 });
      tone({ freq: 1046.5, dur: 0.5, type: "triangle", gain: 0.2 });
      tone({ freq: 1567.98, dur: 0.42, type: "sine", gain: 0.11, at: 0.06 });
      crowd(0.8, 1.5);
      return;
    }
    if (!gate("cross", rank < 5 ? 70 : 130)) return;
    const g = rank < 5 ? 0.09 : 0.05;
    tone({ freq: 900 + Math.random() * 160, dur: 0.11, type: "triangle", gain: g });
    noise({ dur: 0.12, gain: g * 0.6, freq: 2600, sweepTo: 900, q: 1.5 });
  }

  /** 마지막 랩 종. 종소리는 배음이 있어야 "종"으로 들린다. */
  function bell() {
    [1318.5, 1975.5, 2637].forEach((f, i) =>
      tone({ freq: f, dur: 1.1 - i * 0.3, type: "sine", gain: 0.14 - i * 0.045, at: i * 0.015 })
    );
  }

  /** 결정적 순간 직전의 상승 스팅어(포토피니시/최종 발표 직전). */
  function riser(seconds = 1.1) {
    tone({ freq: 140, dur: seconds, type: "sawtooth", gain: 0.12, sweepTo: 900 });
    noise({ dur: seconds, gain: 0.09, freq: 300, sweepTo: 6000, q: 0.7 });
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

  // ---------------------------------------------------------------------
  // 장면별 배경음악(BGM)
  //
  // SFX 한숏과 같은 원칙(외부 파일 없이 오실레이터로 합성, 실패해도 진행에
  // 영향 없음)을 지속음악에도 적용한다. 화면/구간마다 어울리는 짧은 루프를
  // 정의해두고, playScene(name)을 부르면 이전 루프를 부드럽게 낮추면서 새
  // 루프를 올린다 -- 장면이 바뀔 때마다 뚝 끊기지 않는다.
  //
  // 각 트랙은 playBar(at, params)를 구현한다: 현재 마디의 음들을 sceneTone
  // 으로 예약하고 마디 길이(초)를 반환한다. 드라이버(loop)가 그 길이만큼
  // 뒤에 setTimeout으로 스스로를 다시 부르는 방식의 "마디 단위 루프"라
  // 정교한 lookahead 스케줄러 없이도 매끄럽게 이어진다.
  // ---------------------------------------------------------------------

  function ensureSceneGain() {
    const c = ensureCtx();
    if (!c) return null;
    if (!sceneGain) {
      sceneGain = ctx.createGain();
      sceneGain.gain.value = 0.0001;
      sceneGain.connect(master);
    }
    return sceneGain;
  }

  // 음이름 -> Hz (A4 = 440, 평균율). 코드를 Hz 대신 "C#4" 같은 음이름으로
  // 적어야 어떤 화음/스케일인지 한눈에 보인다. 파싱 결과는 캐시한다
  // (마디마다 수십 번 불린다).
  const NOTE_SEMITONE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const hzCache = new Map();
  function hz(name) {
    const cached = hzCache.get(name);
    if (cached !== undefined) return cached;
    const m = /^([A-G])([#b]?)(-?\d)$/.exec(name);
    if (!m) return 440;
    const semi = NOTE_SEMITONE[m[1]] + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0);
    const midi = (parseInt(m[3], 10) + 1) * 12 + semi;
    const freq = 440 * Math.pow(2, (midi - 69) / 12);
    hzCache.set(name, freq);
    return freq;
  }

  /** BGM 전용 한 음. sceneGain을 통해서만 나가고, 뮤트/dpr 등은 상위 master가 처리한다. */
  function sceneTone(freq, dur, type, gain, at) {
    if (!isReady() || !sceneGain) return;
    const t = now() + at;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + Math.max(0.05, dur));
    osc.connect(g);
    g.connect(sceneGain);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  // -- BGM 리듬 파트 --------------------------------------------------------
  // 화음만으로는 "달리는 느낌"이 안 나서 킥/스네어/하이햇을 합성해 넣는다.
  // 전부 sceneGain으로만 나가므로 장면이 바뀌면 함께 페이드된다.

  function sceneKick(at, gain = 0.08) {
    if (!isReady() || !sceneGain) return;
    const t = now() + at;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(155, t);
    osc.frequency.exponentialRampToValueAtTime(44, t + 0.12);
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
    osc.connect(g);
    g.connect(sceneGain);
    osc.start(t);
    osc.stop(t + 0.24);
  }

  function sceneNoiseHit(at, gain, freq, q, dur, filter = "bandpass") {
    if (!isReady() || !sceneGain) return;
    const t = now() + at;
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer;
    src.loop = true;
    const bp = ctx.createBiquadFilter();
    bp.type = filter;
    bp.frequency.value = freq;
    bp.Q.value = q;
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp);
    bp.connect(g);
    g.connect(sceneGain);
    src.start(t);
    src.stop(t + dur + 0.03);
  }

  function sceneHat(at, gain = 0.02, dur = 0.045) {
    sceneNoiseHit(at, gain, 8000, 0.9, dur, "highpass");
  }

  function sceneSnare(at, gain = 0.045) {
    sceneNoiseHit(at, gain, 1900, 0.8, 0.13);
    sceneTone(hz("D3"), 0.07, "triangle", gain * 0.5, at);
  }

  const SCENES = {
    // 대기 화면(idle/waiting) -- 행사 시작 전, 아무 일도 안 일어나는 동안
    // 거슬리지 않게 아주 낮은 볼륨으로 깔리는 패드. 4마디 화음 진행
    // (Cmaj - Am - Fmaj - Gsus)이라 오래 틀어놔도 한 화음만 웅웅대지 않는다.
    idle: {
      gain: 0.42,
      playBar(at, params, bar) {
        const dur = 4.4;
        const chords = [
          ["C3", "G3", "E4"],
          ["A2", "E3", "C4"],
          ["F2", "C3", "A3"],
          ["G2", "D3", "B3"],
        ];
        const ch = chords[bar % 4];
        sceneTone(hz(ch[0]), dur * 0.95, "sine", 0.05, at);
        sceneTone(hz(ch[1]), dur * 0.9, "sine", 0.03, at + 0.12);
        sceneTone(hz(ch[2]), dur * 0.7, "triangle", 0.022, at + 0.3);
        return dur;
      },
    },
    // 라운드 시작 전 대기(오프닝/r1_lock/committed) -- 그리드에 정렬해 스타트
    // 라이트를 기다리는 구간. 심장박동 같은 2연타 킥 + 초침 하이햇 + 마디마다
    // 조여드는 저현으로 "곧 출발한다"는 긴장을 만든다. 스타트 라이트 시퀀스
    // 직전까지 깔리므로 멜로디는 일부러 최소한만 둔다.
    standby: {
      gain: 0.5,
      playBar(at, params, bar) {
        const beat = 60 / 96;
        const barLen = beat * 4;
        sceneKick(at, 0.085);
        sceneKick(at + beat * 0.52, 0.045);
        sceneKick(at + beat * 2, 0.085);
        sceneKick(at + beat * 2.52, 0.045);
        const roots = ["D2", "D2", "F2", "E2"]; // 3~4마디에서 화성이 조여든다
        sceneTone(hz(roots[bar % 4]), barLen * 0.95, "sawtooth", 0.032, at);
        sceneTone(hz("A3"), barLen * 0.9, "sine", 0.02, at + 0.2);
        for (let i = 0; i < 4; i++) sceneHat(at + i * beat, 0.018);
        if (bar % 4 === 3) sceneTone(hz("E4"), beat * 1.4, "triangle", 0.028, at + beat * 3);
        return barLen;
      },
    },
    // 선택/발표 대기 화면(score_rX_select_rY) -- 참가자가 모바일에서 대상을
    // 고르는 동안의 "결정을 기다리는" 긴장감. 단조 아르페지오 + 4마디 진행.
    anticipation: {
      gain: 0.5,
      playBar(at, params, bar) {
        const beat = 60 / 100;
        const roots = ["A2", "F2", "C3", "E2"];
        const arps = [
          ["A3", "C4", "E4", "A4", "E4", "C4"],
          ["F3", "A3", "C4", "F4", "C4", "A3"],
          ["C4", "E4", "G4", "C5", "G4", "E4"],
          ["E3", "G#3", "B3", "E4", "B3", "G#3"],
        ];
        sceneTone(hz(roots[bar % 4]), beat * 3.7, "triangle", 0.055, at);
        const arp = arps[bar % 4];
        const step = (beat * 4) / arp.length;
        arp.forEach((n, i) => sceneTone(hz(n), beat * 0.4, "square", 0.035, at + i * step));
        sceneHat(at + beat * 2, 0.016);
        return beat * 4;
      },
    },
    // -- 레이스 3종 ---------------------------------------------------------
    // 라운드마다 곡을 따로 둔다. 같은 곡의 템포만 올리면 세 번을 다 듣는
    // 관객에게는 "같은 노래 세 번"으로 들려서 라운드가 올라간다는 감각이
    // 없다. 조성·리듬·악기 배치를 바꿔 R1(경쾌) -> R2(추격) -> R3(결전)로
    // 무게를 키운다. 세 곡 모두 setSceneIntensity(0..1: 진행률+접전도)로
    // 템포와 리드 음량이 실시간으로 올라간다.

    // R1 -- D장조, 밝고 경쾌한 오프닝 레이스.
    race1: {
      gain: 0.55,
      playBar(at, params, bar) {
        const beat = 60 / (126 + sceneIntensity * 12);
        const bass = ["D2", "D2", "A2", "D2", "F#2", "D2", "A2", "C#3"];
        bass.forEach((n, i) => sceneTone(hz(n), beat * 0.4, "sawtooth", 0.06, at + i * beat * 0.5));
        const lead = ["D4", "F#4", "A4", "F#4"];
        const leadGain = 0.026 + sceneIntensity * 0.038;
        lead.forEach((n, i) => sceneTone(hz(n), beat * 0.45, "square", leadGain, at + i * beat));
        for (let i = 0; i < 8; i++) sceneHat(at + i * beat * 0.5, i % 2 ? 0.012 : 0.02);
        sceneKick(at, 0.075);
        sceneKick(at + beat * 2, 0.07);
        sceneSnare(at + beat, 0.035);
        sceneSnare(at + beat * 3, 0.035);
        if (sceneIntensity > 0.72) sceneTone(hz("D5"), beat * 1.4, "triangle", 0.028, at + beat * 2.5);
        if (bar % 4 === 3) sceneTone(hz("A4"), beat * 0.9, "triangle", 0.03, at + beat * 3.2);
        return beat * 4;
      },
    },
    // R2 -- A단조, 싱코페이션 베이스에 오프비트 스탭. 쫓고 쫓기는 추격전.
    race2: {
      gain: 0.57,
      playBar(at, params, bar) {
        const beat = 60 / (136 + sceneIntensity * 14);
        const bass = ["A1", "A1", "C2", "A1", "G1", "G1", "E2", "G1"];
        bass.forEach((n, i) => sceneTone(hz(n), beat * 0.36, "sawtooth", 0.07, at + i * beat * 0.5));
        // 오프비트 스탭 화음 -- 박자를 뒤로 밀어 급한 느낌을 준다
        const stabGain = 0.024 + sceneIntensity * 0.034;
        [0.75, 1.75, 2.75, 3.5].forEach((b) => {
          sceneTone(hz("A3"), beat * 0.3, "square", stabGain, at + beat * b);
          sceneTone(hz("E4"), beat * 0.3, "square", stabGain * 0.8, at + beat * b);
        });
        const lead = bar % 2 === 0 ? ["E4", "G4", "A4", "C5"] : ["D5", "C5", "A4", "G4"];
        lead.forEach((n, i) =>
          sceneTone(hz(n), beat * 0.4, "triangle", 0.022 + sceneIntensity * 0.03, at + i * beat)
        );
        for (let i = 0; i < 8; i++) sceneHat(at + i * beat * 0.5, i % 2 ? 0.014 : 0.022);
        sceneKick(at, 0.085);
        sceneKick(at + beat * 1.5, 0.06);
        sceneKick(at + beat * 2, 0.08);
        sceneSnare(at + beat, 0.042);
        sceneSnare(at + beat * 3, 0.042);
        if (sceneIntensity > 0.8) sceneSnare(at + beat * 3.75, 0.03);
        return beat * 4;
      },
    },
    // R3(결선) -- E단조, 4분 킥 + 16분 아르페지오 + 옥타브 리드. 가장 무겁고
    // 빠르다. 4마디마다 상승 필인을 넣어 결승선까지 계속 몰아친다.
    race3: {
      gain: 0.6,
      playBar(at, params, bar) {
        const beat = 60 / (146 + sceneIntensity * 16);
        const roots = ["E1", "E1", "C2", "B1"];
        const root = roots[bar % 4];
        for (let i = 0; i < 8; i++) {
          sceneTone(hz(root), beat * 0.3, "sawtooth", 0.075, at + i * beat * 0.5);
        }
        // 16분 아르페지오 -- 속도감의 핵심
        const arp = ["E3", "G3", "B3", "E4", "B3", "G3", "E3", "G3"];
        const arpGain = 0.02 + sceneIntensity * 0.028;
        for (let i = 0; i < 16; i++) {
          sceneTone(hz(arp[i % arp.length]), beat * 0.18, "square", arpGain, at + i * beat * 0.25);
        }
        // 옥타브 리드
        const lead = bar % 2 === 0 ? ["E4", "B4"] : ["D4", "A4"];
        lead.forEach((n, i) =>
          sceneTone(hz(n), beat * 1.6, "triangle", 0.03 + sceneIntensity * 0.035, at + i * beat * 2)
        );
        for (let i = 0; i < 4; i++) sceneKick(at + i * beat, 0.09);
        sceneSnare(at + beat, 0.05);
        sceneSnare(at + beat * 3, 0.05);
        for (let i = 0; i < 8; i++) sceneHat(at + i * beat * 0.5 + beat * 0.25, 0.016);
        if (bar % 4 === 3) {
          // 상승 필인
          for (let i = 0; i < 6; i++) {
            sceneNoiseHit(at + beat * 3 + i * beat * 0.16, 0.03, 1200 + i * 700, 1.2, 0.1);
          }
        }
        return beat * 4;
      },
    },
    // 결과 화면(final_announce/verify/podium) -- 밝은 장조 진행 + 벨 아르페지오
    // 로 승리감을 준다. 시상대에서 오래 떠 있으므로 4마디 진행으로 지루함을 던다.
    victory: {
      gain: 0.6,
      playBar(at, params, bar) {
        const beat = 60 / 108;
        const barLen = beat * 4;
        const chords = [
          ["C3", "C4", "E4", "G4"],
          ["G2", "B3", "D4", "G4"],
          ["A2", "C4", "E4", "A4"],
          ["F2", "A3", "C4", "F4"],
        ];
        const ch = chords[bar % 4];
        sceneTone(hz(ch[0]), barLen * 0.9, "triangle", 0.05, at);
        ch.slice(1).forEach((n, i) => sceneTone(hz(n), barLen * 0.8, "triangle", 0.032, at + 0.04 * i));
        const bells = ["C5", "E5", "G5", "E5"];
        bells.forEach((n, i) => sceneTone(hz(n), beat * 0.5, "sine", 0.038, at + i * beat));
        sceneKick(at, 0.06);
        sceneKick(at + beat * 2, 0.05);
        sceneHat(at + beat, 0.016);
        sceneHat(at + beat * 3, 0.016);
        return barLen;
      },
    },
    // 룰렛 모드 스핀 구간 -- 빙글빙글 도는 느낌의 짧고 단순한 루프.
    roulette: {
      gain: 0.5,
      playBar(at, params, bar) {
        const barLen = 1.8;
        sceneTone(hz("A3"), barLen * 0.9, "sine", 0.05, at);
        sceneTone(hz("E4"), barLen * 0.4, "triangle", 0.035, at + barLen * 0.5);
        sceneHat(at, 0.018);
        sceneHat(at + barLen * 0.5, 0.014);
        if (bar % 2 === 1) sceneTone(hz("C5"), barLen * 0.3, "square", 0.025, at + barLen * 0.75);
        return barLen;
      },
    },
  };

  /** 장면 전환. 이미 같은 장면이면(라운드 등 params만 갱신하고) 다시 시작하지 않는다 -- 매 페이즈 이벤트마다 불려도 끊기지 않게. */
  function playScene(name, opts = {}) {
    if (!SCENES[name]) return;
    if (sceneName === name) {
      sceneParams = { ...sceneParams, ...opts };
      return;
    }
    if (!ensureSceneGain()) return;
    sceneGeneration += 1;
    const myGen = sceneGeneration;
    sceneName = name;
    sceneParams = { ...opts };
    sceneBarIndex = 0;
    sceneGain.gain.cancelScheduledValues(now());
    // 장면마다 어울리는 기본 음량이 다르다(대기 화면은 낮게, 결선/시상대는 높게).
    sceneGain.gain.setTargetAtTime(SCENES[name].gain || 0.55, now(), 0.5);

    if (sceneTimer) clearTimeout(sceneTimer);
    const loop = () => {
      if (myGen !== sceneGeneration) return;
      if (!isReady()) {
        sceneTimer = setTimeout(loop, 300); // 아직 unlock 전이거나 음소거 -- 조용히 재시도
        return;
      }
      const bar = SCENES[sceneName].playBar(0, sceneParams, sceneBarIndex);
      sceneBarIndex += 1;
      sceneTimer = setTimeout(loop, Math.max(200, bar * 1000 - 60));
    };
    loop();
  }

  function stopScene() {
    sceneGeneration += 1;
    if (sceneTimer) {
      clearTimeout(sceneTimer);
      sceneTimer = null;
    }
    sceneName = null;
    if (sceneGain) sceneGain.gain.setTargetAtTime(0.0001, now(), 0.4);
  }

  function setSceneIntensity(value) {
    sceneIntensity = Math.max(0, Math.min(1, value));
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
    hit,
    finishCross,
    bell,
    riser,
    startEngine,
    setRpm,
    stopEngine,
    playScene,
    stopScene,
    setSceneIntensity,
    getSceneName: () => sceneName,
  };
})();
