"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const STEP_ORDER = ["separate", "lyrics", "melody", "rhythm", "accomp", "finish"];

let jobId = null;
let original = null; // 분석 결과
let pollTimer = null;
let lyricEdits = {}; // 음표별로 직접 고친 가사 {음표번호: 글자}

// ---------- 공통
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((body && (body.error || body.detail)) || r.statusText);
  return body;
}
function show(id, on = true) { $(id).classList.toggle("hidden", !on); }
function showError(msg) { const e = $("#error"); e.textContent = msg; show("#error", !!msg); }

// ---------- 상태 표시
api("/api/health").then((h) => {
  $("#health").innerHTML =
    `<span class="${h.cuda ? "ok" : "bad"}">${h.cuda ? "GPU " + h.gpu : "GPU 없음 (CPU로 느리게 동작)"}</span>` +
    `<span class="${h.lilypond ? "ok" : "bad"}">${h.lilypond ? "악보 엔진 준비됨" : "LilyPond 없음 — 설치.bat 실행"}</span>`;
}).catch(() => {});

// 키 목록
for (const mode of ["major", "minor"]) {
  for (const k of KEYS) {
    const o = document.createElement("option");
    o.value = `${k} ${mode}`;
    o.textContent = mode === "major" ? `${k} 장조` : `${k}m (단조)`;
    $("#key").append(o);
  }
}

// ---------- 최근 작업
async function loadRecent() {
  try {
    const jobs = (await api("/api/jobs")).filter((j) => j.state === "done" || j.state === "error");
    if (!jobs.length) { $("#recent").innerHTML = ""; return; }
    $("#recent").innerHTML = "<h3>최근 작업 다시 열기</h3>";
    for (const j of jobs.slice(0, 10)) {
      const row = document.createElement("div");
      row.className = "recent-item" + (j.state === "error" ? " failed" : "");
      const d = new Date(j.created * 1000);
      const when = `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      row.innerHTML = `<button class="open"><span></span><em></em></button>` +
        `<button class="del" title="이 작업 지우기" aria-label="지우기">🗑</button>`;
      row.querySelector("span").textContent = j.name;
      row.querySelector("em").textContent = j.state === "error" ? "분석 실패" : when;
      const open = row.querySelector(".open");
      if (j.state === "done") open.onclick = () => openJob(j.id, j.name);
      else { open.disabled = true; open.title = "분석에 실패한 작업이에요. 지우고 파일을 다시 넣어 주세요."; }
      const del = row.querySelector(".del");
      del.onclick = async () => {
        del.disabled = true;
        try {
          await api(`/api/jobs/${j.id}`, { method: "DELETE" });
          row.remove();
          if (!$("#recent .recent-item")) $("#recent").innerHTML = "";
        } catch (err) { del.disabled = false; showError("지우지 못했어요: " + err.message); }
      };
      $("#recent").append(row);
    }
  } catch (_) {}
}
loadRecent();

// ---------- 업로드
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
$("#file").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) upload(f); e.target.value = ""; });

async function upload(file) {
  showError("");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("language", $("#language").value);
  $("#fname").textContent = file.name;
  show("#upload", false); show("#progress"); show("#settings", false); show("#downloads", false);
  setProgress({ progress: 0, message: "파일 올리는 중…" });
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    jobId = job.id;
    $("#title").value = job.name;
    poll();
  } catch (err) {
    showError("업로드 실패: " + err.message);
    show("#upload"); show("#progress", false);
  }
}

function setProgress(st) {
  $("#bar-fill").style.width = Math.round((st.progress || 0) * 100) + "%";
  $("#pmsg").textContent = `${Math.round((st.progress || 0) * 100)}% · ${st.message || ""}`;
  const cur = STEP_ORDER.indexOf(st.step);
  $$("#steps li").forEach((li) => {
    const i = STEP_ORDER.indexOf(li.dataset.step);
    li.classList.toggle("done", st.state === "done" || (cur >= 0 && i < cur));
    li.classList.toggle("active", st.state !== "done" && i === cur);
  });
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = await api(`/api/jobs/${jobId}/status`);
    setProgress(st);
    if (st.state === "done") return openJob(jobId);
    if (st.state === "error") {
      showError("분석 중 오류가 났어요:\n" + st.message);
      show("#upload");
      return;
    }
  } catch (_) {}
  pollTimer = setTimeout(poll, 1000);
}

// ---------- 분석 결과 열기
async function openJob(id, name) {
  jobId = id;
  original = await api(`/api/jobs/${id}/analysis`);
  show("#upload", false); show("#progress", false); show("#settings"); show("#downloads", false);
  const k = original.key;
  $("#summary").innerHTML = `
    <span>길이<b>${Math.floor(original.duration / 60)}:${String(Math.round(original.duration % 60)).padStart(2, "0")}</b></span>
    <span>템포<b>${Math.round(original.tempo)} BPM</b></span>
    <span>조<b>${KEYS[k.tonic]} ${k.mode === "major" ? "장조" : "단조"}</b></span>
    <span>멜로디 음<b>${original.melody_notes}개</b></span>
    <span>가사<b>${original.lyrics.length}줄</b></span>`;
  const s = original.settings;
  $("#lyrics").value = original.lyrics.join("\n");
  if (s) applySettings(s);
  else $("#title").value = name || original.name;
  $("#a-src").src = `/api/jobs/${id}/file/source`;
  $("#a-voc").src = `/api/jobs/${id}/file/vocals`;
  $("#a-acc").src = `/api/jobs/${id}/file/accomp`;
  $("#dl-voc").href = `/api/jobs/${id}/file/vocals?download=1`;
  $("#dl-acc").href = `/api/jobs/${id}/file/accomp?download=1`;
  lyricEdits = (s && s.lyric_edits) || {};
  setAudioSource();
  render();
}

function applySettings(s) {
  $("#title").value = s.title || "";
  $("#artist").value = s.artist || "";
  const m = $(`input[name=mode][value="${s.mode}"]`); if (m) m.checked = true;
  for (const id of ["time_sig", "grid", "bars_per_line", "key"]) if (s[id] != null) $("#" + id).value = String(s[id]);
  $("#chord_stability").value = s.chord_stability ?? 0.12;
  $("#downbeat_shift").textContent = s.downbeat_shift || 0;
  $("#transpose").textContent = s.transpose || 0;
  $$("#tempo_mult button").forEach((b) => b.classList.toggle("on", Number(b.dataset.v) === Number(s.tempo_mult || 1)));
  $("#show_lyrics").checked = s.show_lyrics !== false;
  $("#lyrics_page").checked = s.lyrics_page !== false;
  if (s.lyrics_text != null) $("#lyrics").value = s.lyrics_text;
  lyricEdits = s.lyric_edits || {};
}

function collect() {
  const lyr = $("#lyrics").value;
  const orig = original.lyrics.join("\n");
  return {
    mode: $("input[name=mode]:checked").value,
    title: $("#title").value.trim(),
    artist: $("#artist").value.trim(),
    time_sig: $("#time_sig").value,
    grid: Number($("#grid").value),
    bars_per_line: Number($("#bars_per_line").value),
    tempo_mult: Number($("#tempo_mult .on").dataset.v),
    downbeat_shift: Number($("#downbeat_shift").textContent),
    transpose: Number($("#transpose").textContent),
    key: $("#key").value,
    chord_stability: Number($("#chord_stability").value),
    show_lyrics: $("#show_lyrics").checked,
    lyrics_page: $("#lyrics_page").checked,
    lyrics_text: lyr.trim() === orig.trim() ? null : lyr,
    lyric_edits: lyricEdits,
  };
}

// ---------- 컨트롤
$$("#tempo_mult button").forEach((b) => b.addEventListener("click", () => {
  $$("#tempo_mult button").forEach((x) => x.classList.toggle("on", x === b));
  render();
}));
$$(".stepper button").forEach((b) => b.addEventListener("click", () => {
  const out = $("#" + b.dataset.for);
  let v = Number(out.textContent) + Number(b.dataset.d);
  if (b.dataset.for === "transpose") v = Math.max(-11, Math.min(11, v));
  if (b.dataset.for === "downbeat_shift") v = Math.max(-3, Math.min(3, v));
  out.textContent = v > 0 && b.dataset.for === "transpose" ? "+" + v : v;
  render();
}));
$$("input[name=mode], #time_sig, #grid, #bars_per_line, #key, #chord_stability, #show_lyrics, #lyrics_page")
  .forEach((el) => el.addEventListener("change", () => render()));
$("#render").addEventListener("click", () => render(true));
$("#lyrics-reset").addEventListener("click", () => {
  $("#lyrics").value = original.lyrics.join("\n");
  lyricEdits = {};
  render();
});
$("#new").addEventListener("click", () => {
  show("#upload"); show("#settings", false); show("#downloads", false); show("#progress", false);
  stopAudio(); show("#sheet", false); show("#player", false); show("#sheet-hint", false); closeEditor();
  show("#empty"); loadRecent();
});
$("#print").addEventListener("click", async () => {
  if (desktop()) {
    const r = await window.pywebview.api.open_file(jobId, "pdf");
    if (r && r.ok) toast("PDF를 기본 프로그램으로 열었어요. 그 창에서 인쇄하세요.");
    else showError("PDF를 열지 못했어요: " + ((r && r.error) || ""));
    return;
  }
  const w = window.open(`/api/jobs/${jobId}/file/pdf?t=${Date.now()}`, "_blank");
  if (w) w.addEventListener("load", () => setTimeout(() => w.print(), 500));
});

// ---------- 프로그램 창(데스크톱) 모드: 저장은 '다른 이름으로 저장' 창으로
function desktop() { return !!(window.pywebview && window.pywebview.api); }
const SAVE_KIND = { "dl-pdf": "pdf", "dl-xml": "musicxml", "dl-midi": "midi", "dl-voc": "vocals", "dl-acc": "accomp" };
for (const [id, kind] of Object.entries(SAVE_KIND)) {
  $("#" + id).addEventListener("click", async (e) => {
    if (!desktop()) return; // 브라우저에서는 일반 내려받기
    e.preventDefault();
    if (["pdf", "midi", "musicxml"].includes(kind)) toast("최신 악보로 파일을 만드는 중이에요… (긴 곡은 20초쯤)");
    const r = await window.pywebview.api.save_file(jobId, kind);
    if (r && r.ok) toast("저장했어요: " + r.path, r.path);
    else if (r && !r.cancelled) showError("저장 실패: " + (r.error || ""));
  });
}
function toast(msg, path) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  if (path) {
    const b = document.createElement("button");
    b.textContent = "폴더 열기";
    b.onclick = () => window.pywebview.api.show_in_folder(path);
    t.append(b);
  }
  document.body.append(t);
  setTimeout(() => t.remove(), 6000);
}
window.addEventListener("pywebviewready", () => document.body.classList.add("desktop"));
