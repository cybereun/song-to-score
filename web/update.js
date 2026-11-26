"use strict";
// 버전 표시 · 설정 창(도움말/업데이트/개발자) · 새 버전 안내와 자동 업데이트
// app.js 의 $, $$, api, show 를 사용한다.

let appInfo = { version: "", installed: false };
let updInfo = null;
let updTimer = null;

// ---------- 창 열고 닫기
function openModal(id) { show(id, true); }
function closeModal(id) { show(id, false); }
$$(".modal").forEach((m) => {
  m.addEventListener("click", (e) => { if (e.target === m || e.target.closest("[data-close]")) closeModal("#" + m.id); });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $$(".modal").forEach((m) => { if (!m.classList.contains("hidden") && !updating()) closeModal("#" + m.id); });
});
$("#open-settings").addEventListener("click", () => { openModal("#settings-modal"); });
$$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));
function selectTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  $$(".pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== name));
  if (name === "update" && !updInfo) checkUpdate(true);
}

// ---------- 버전 표시
api("/api/version").then((v) => {
  appInfo = v;
  $("#app-ver").textContent = "v" + v.version;
  $("#about-ver").textContent = "v" + v.version;
  $("#upd-current").textContent = "v" + v.version;
  $("#about-dev").textContent = v.developer;
  if (v.threads) $("#about-dev").href = v.threads;
  for (const [id, url] of [["#help-home", v.homepage], ["#about-home", v.homepage],
    ["#help-repo", v.repo + "#readme"], ["#about-repo", v.repo], ["#about-issue", v.repo + "/issues"]]) $(id).href = url;
  checkUpdate(false);
}).catch(() => {});

// ---------- 업데이트 확인
async function checkUpdate(force) {
  $("#upd-latest").textContent = "확인 중…";
  setMsg("#upd-msg", "");
  try {
    updInfo = await api("/api/update/check" + (force ? "?force=1" : ""));
  } catch (err) {
    updInfo = null;
    $("#upd-latest").textContent = "-";
    setMsg("#upd-msg", "업데이트 정보를 가져오지 못했어요. 인터넷 연결을 확인해 주세요.", true);
    return;
  }
  if (updInfo.error) {
    $("#upd-latest").textContent = "-";
    setMsg("#upd-msg", updInfo.error, true);
    return;
  }
  $("#upd-latest").textContent = "v" + updInfo.latest;
  const notes = (updInfo.notes || "").replace(/\r/g, "").trim();
  $("#upd-notes").textContent = notes.slice(0, 1500);
  show("#upd-notes", !!notes && updInfo.newer);
  show("#upd-go", updInfo.newer);
  show("#update-dot", updInfo.newer);
  if (updInfo.newer) {
    setMsg("#upd-msg", "새 버전이 있어요. 「지금 업데이트」를 누르면 받아서 설치하고 프로그램을 다시 열어요.");
    if (!force && sessionStorage.getItem("sts-later") !== updInfo.latest) showUpdateModal();
  } else {
    setMsg("#upd-msg", "최신 버전을 쓰고 있어요. 👍");
  }
}
$("#upd-check").addEventListener("click", () => checkUpdate(true));

function showUpdateModal() {
  $("#um-current").textContent = "v" + updInfo.current;
  $("#um-latest").textContent = "v" + updInfo.latest;
  const notes = (updInfo.notes || "").replace(/\r/g, "").trim();
  $("#um-notes").textContent = notes ? notes.slice(0, 1200) : "버그 수정과 개선 사항이 들어 있어요.";
  setMsg("#um-msg", appInfo.installed ? "업데이트하면 약 1~3분 뒤 프로그램이 자동으로 다시 열려요. 작업한 악보와 설정은 그대로 남아요." :
    "개발용 실행에서는 자동 업데이트를 할 수 없어요. 「지금 업데이트」를 누르면 다운로드 페이지가 열려요.");
  openModal("#update-modal");
}
$("#um-later").addEventListener("click", () => {
  if (updInfo) sessionStorage.setItem("sts-later", updInfo.latest);
  closeModal("#update-modal");
});

// ---------- 내려받고 설치
function updating() { return !!updTimer; }
$("#um-go").addEventListener("click", () => runUpdate("um"));
$("#upd-go").addEventListener("click", () => runUpdate("upd"));

async function runUpdate(prefix) {
  if (!updInfo || !updInfo.newer) return;
  if (!appInfo.installed) { window.open(updInfo.page, "_blank"); return; }
  const go = $(`#${prefix}-go`);
  go.disabled = true;
  show(`#${prefix}-progress`, true);
  setMsg(`#${prefix}-msg`, "새 버전을 내려받는 중이에요…");
  try {
    await api("/api/update/start", { method: "POST" });
  } catch (err) {
    setMsg(`#${prefix}-msg`, err.message, true);
    go.disabled = false;
    return;
  }
  clearInterval(updTimer);
  updTimer = setInterval(async () => {
    let st;
    try { st = await api("/api/update/status"); } catch (_) { return; }
    const pct = Math.round((st.progress || 0) * 100);
    $(`#${prefix}-bar`).style.width = pct + "%";
    $(`#${prefix}-pct`).textContent = pct + "%";
    if (st.state === "error") {
      clearInterval(updTimer); updTimer = null;
      setMsg(`#${prefix}-msg`, st.error + " 잠시 뒤 다시 시도하거나 홈페이지에서 받아 주세요.", true);
      go.disabled = false;
    } else if (st.state === "ready") {
      clearInterval(updTimer); updTimer = null;
      setMsg(`#${prefix}-msg`, "설치를 시작해요. 프로그램이 잠시 닫혔다가 1~3분 뒤 새 버전으로 다시 열려요.");
      try { await api("/api/update/install", { method: "POST" }); } catch (_) {}
    }
  }, 500);
}

function setMsg(sel, text, isErr = false) {
  const el = $(sel);
  el.textContent = text;
  el.classList.toggle("err", !!isErr);
}
