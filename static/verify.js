const resultEl = document.getElementById("result");
const tamperResultEl = document.getElementById("tamper-result");
const btnTamper = document.getElementById("btn-tamper");

let lastVerifyData = null;
let lastLocalOutcome = null;

function departmentTableHtml(rates) {
  return Object.entries(rates)
    .sort((a, b) => b[1] - a[1])
    .map(([name, rate]) => `<tr><td>${name}</td><td>${(rate * 100).toFixed(1)}%</td></tr>`)
    .join("");
}

document.getElementById("btn-load").addEventListener("click", async () => {
  const idx = parseInt(document.getElementById("draw-index").value, 10);
  resultEl.innerHTML = "불러오는 중...";
  btnTamper.disabled = true;
  try {
    const data = await fetchJSON(`/api/verify/${idx}`);
    lastVerifyData = data;

    const local = await FairnessJS.recomputeFromReveal(data.seed, data.snapshot);
    lastLocalOutcome = local;

    const commitMatch = local.commit === data.declared_commit;
    const winnersMatch = JSON.stringify(local.winners) === JSON.stringify(data.declared_winners);
    const rankingMatch = JSON.stringify(local.ranking) === JSON.stringify(data.declared_ranking);
    const allMatch = commitMatch && winnersMatch && rankingMatch;

    const winnerNames = local.winners
      .map((id) => {
        const p = data.snapshot.participants.find((x) => x.id === id);
        return p ? participantLabel(p) : id;
      })
      .join(", ");

    resultEl.innerHTML = `
      <p class="${allMatch ? "verify-ok" : "verify-fail"}">
        브라우저 독립 재계산 결과: ${allMatch ? "✅ 일치 (커밋·순위·당첨자 모두 서버 선언값과 동일)" : "❌ 불일치 감지"}
      </p>
      <p><strong>공개된 시드:</strong></p>
      <p class="mono">${data.seed}</p>
      <p><strong>커밋 해시</strong> (서버 선언 / 브라우저 재계산)</p>
      <p class="mono">${data.declared_commit}<br>${local.commit}</p>
      <p><strong>당첨자(${local.winners.length}명):</strong> ${winnerNames}</p>
      <p><strong>1라운드 부서 통과율</strong></p>
      <table><thead><tr><th>부서</th><th>통과율</th></tr></thead><tbody>${departmentTableHtml(local.department_pass_rate["1"])}</tbody></table>
      <p><strong>2라운드 부서 통과율</strong></p>
      <table><thead><tr><th>부서</th><th>통과율</th></tr></thead><tbody>${departmentTableHtml(local.department_pass_rate["2"])}</tbody></table>
    `;
    btnTamper.disabled = false;
  } catch (e) {
    resultEl.innerHTML = `<p class="verify-fail">불러오기 실패: ${e.message}</p>`;
  }
});

btnTamper.addEventListener("click", () => {
  if (!lastVerifyData || !lastLocalOutcome) return;
  // 데모: 서버가 알려준 커밋 해시를 한 글자 조작한 값을 "선언값"이라 가정하고
  // 브라우저 재계산 값과 비교한다 -- 이 페이지가 조작을 실제로 탐지하는지 보여주는 시연.
  const tamperedCommit =
    lastVerifyData.declared_commit.slice(0, -1) +
    (lastVerifyData.declared_commit.slice(-1) === "0" ? "1" : "0");

  const matches = tamperedCommit === lastLocalOutcome.commit;
  tamperResultEl.innerHTML = `
    <p class="${matches ? "verify-ok" : "verify-fail"}">
      ${matches ? "일치 (이상함 -- 실제로는 발생하면 안 됨)" : "❌ 불일치 감지됨: 조작된 커밋 해시는 브라우저 재계산과 다릅니다."}
    </p>
    <p class="mono">조작된 선언값: ${tamperedCommit}<br>브라우저 재계산: ${lastLocalOutcome.commit}</p>
    <p>이런 식으로, 서버·화면·명단 중 어느 하나라도 사후에 바뀌면 이 페이지에서 즉시 검출됩니다.</p>
  `;
});
