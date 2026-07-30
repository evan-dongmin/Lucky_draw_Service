/**
 * app/fairness.py의 커밋-리빌 알고리즘을 브라우저(WebCrypto)에서 독립적으로
 * 재계산하기 위한 이식본. verify.html이 서버 응답을 신뢰하지 않고 이 파일로
 * commit/순위/통과자/부서집계를 처음부터 다시 계산해 비교한다.
 *
 * canonicalStringify는 Python의 json.dumps(ensure_ascii=False, sort_keys=True,
 * separators=(",", ":"))와 바이트 단위로 동일한 결과를 내야 한다
 * (tests/test_js_fairness_parity.py가 Node로 교차 검증한다).
 */
(function (global) {
  "use strict";

  function utf8Bytes(str) {
    return new TextEncoder().encode(str);
  }

  function bytesToHex(bytes) {
    return Array.from(bytes)
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  function bytesToBigIntBE(bytes) {
    let result = 0n;
    for (const b of bytes) {
      result = (result << 8n) | BigInt(b);
    }
    return result;
  }

  function canonicalStringify(value) {
    if (value === null || value === undefined) return "null";
    const t = typeof value;
    if (t === "number" || t === "boolean") return JSON.stringify(value);
    if (t === "string") return JSON.stringify(value);
    if (Array.isArray(value)) {
      return "[" + value.map(canonicalStringify).join(",") + "]";
    }
    if (t === "object") {
      const keys = Object.keys(value).sort();
      return (
        "{" +
        keys.map((k) => JSON.stringify(k) + ":" + canonicalStringify(value[k])).join(",") +
        "}"
      );
    }
    throw new Error("canonicalStringify: 지원하지 않는 타입 " + t);
  }

  async function sha256Hex(str) {
    const digest = await crypto.subtle.digest("SHA-256", utf8Bytes(str));
    return bytesToHex(new Uint8Array(digest));
  }

  async function computeCommit(seed, snapshot) {
    const payload = seed + "|" + canonicalStringify(snapshot);
    return sha256Hex(payload);
  }

  async function hmacScore(seed, participantId) {
    const key = await crypto.subtle.importKey(
      "raw",
      utf8Bytes(seed),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );
    const sig = await crypto.subtle.sign("HMAC", key, utf8Bytes(participantId));
    return bytesToBigIntBE(new Uint8Array(sig));
  }

  const R1_PASS_COUNT = 100;

  function resolveFinalistCount(drawCount) {
    if (drawCount < 1) throw new Error("당첨 인원수는 1 이상이어야 합니다");
    const clamped = Math.max(5, Math.min(10, drawCount * 2));
    return Math.max(drawCount, clamped);
  }

  function departmentPassRates(departments, passSet, denomSets) {
    const rates = {};
    for (const name of Object.keys(departments)) {
      const ids = departments[name];
      const denomSet = denomSets[name] || new Set();
      const denom = denomSet.size;
      if (denom === 0) {
        rates[name] = 0.0;
        continue;
      }
      let numerator = 0;
      for (const id of ids) {
        if (passSet.has(id)) numerator += 1;
      }
      rates[name] = numerator / denom;
    }
    return rates;
  }

  async function computeOutcome(eligibleIds, seed, drawCount, departments) {
    if (drawCount > eligibleIds.length) {
      throw new Error("당첨 인원수가 참가 가능 인원보다 많습니다");
    }

    const scores = new Map();
    for (const id of eligibleIds) {
      scores.set(id, await hmacScore(seed, id));
    }

    const ranking = [...eligibleIds].sort((a, b) => {
      const sa = scores.get(a);
      const sb = scores.get(b);
      if (sa === sb) return a < b ? -1 : a > b ? 1 : 0;
      return sa > sb ? -1 : 1;
    });

    const finalistCount = Math.min(resolveFinalistCount(drawCount), ranking.length);
    const r1Count = Math.min(Math.max(R1_PASS_COUNT, finalistCount), ranking.length);

    const r1Pass = ranking.slice(0, r1Count);
    const r2Pass = ranking.slice(0, finalistCount);
    const winners = ranking.slice(0, drawCount);

    const r1PassSet = new Set(r1Pass);
    const r2PassSet = new Set(r2Pass);

    const allGroupSets = {};
    for (const name of Object.keys(departments)) {
      allGroupSets[name] = new Set(departments[name]);
    }
    const r2DenomSets = {};
    for (const name of Object.keys(departments)) {
      const inter = new Set([...allGroupSets[name]].filter((id) => r1PassSet.has(id)));
      r2DenomSets[name] = inter;
    }

    const departmentPassRate = {
      1: departmentPassRates(departments, r1PassSet, allGroupSets),
      2: departmentPassRates(departments, r2PassSet, r2DenomSets),
    };

    return {
      ranking,
      winners,
      round_pass_ids: { 1: r1Pass, 2: r2Pass, 3: winners },
      department_pass_rate: departmentPassRate,
      finalist_count: finalistCount,
    };
  }

  async function recomputeFromReveal(seed, snapshot) {
    const commit = await computeCommit(seed, snapshot);
    const excluded = new Set(snapshot.excluded_ids || []);
    const eligibleIds = snapshot.participants
      .filter((p) => !excluded.has(p.id))
      .map((p) => p.id);
    const outcome = await computeOutcome(eligibleIds, seed, snapshot.draw_count, snapshot.departments);
    return { commit, ...outcome };
  }

  const FairnessJS = {
    canonicalStringify,
    sha256Hex,
    computeCommit,
    hmacScore,
    resolveFinalistCount,
    computeOutcome,
    recomputeFromReveal,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = FairnessJS;
  } else {
    global.FairnessJS = FairnessJS;
  }
})(typeof window !== "undefined" ? window : globalThis);
