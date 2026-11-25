"use strict";
// 악보(SVG) 표시 · 재생에 맞춘 음표 표시 · 음표 눌러 가사 고치기
// app.js 의 $, $$, api, show, showError, collect, jobId, original, lyricEdits 를 사용한다.

let renderSeq = 0;
let timeline = []; // [{n, t0, t1, lyric}] 시간순
let T0 = [];
let noteEls = new Map(); // 음표번호 → [SVG 요소]
let lyricEls = new Map();
let activeIdx = -1;
let editorIdx = -1;

// ---------- 렌더
async function render(opts = {}) {
  if (!jobId || !original) return;
  const seq = ++renderSeq;
  const quiet = !!opts.quiet;
  showError("");
  if (quiet) $("#pl-status").textContent = "고친 가사 반영 중…";
  else { show("#busy"); show("#empty", false); }
  $("#render").disabled = true;
  try {
    const res = await api(`/api/jobs/${jobId}/render`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()),
    });
    if (seq !== renderSeq) return;
    const svgs = await Promise.all(res.pages.map((u) => fetch(u).then((r) => r.text())));
    if (seq !== renderSeq) return;
    const sheet = $("#sheet");
    const keep = sheet.scrollTop;
    sheet.innerHTML = svgs.map((s) => `<div class="page">${s.replace(/<\?xml[^>]*>/, "")}</div>`).join("");
    show("#sheet"); show("#player"); show("#sheet-hint"); show("#empty", false);
    sheet.scrollTop = keep;
    timeline = res.timeline.slice().sort((a, b) => a.t0 - b.t0);
    T0 = timeline.map((x) => x.t0);
    indexSheet();
    activeIdx = -1;
    tick(false);
    if (editorIdx >= 0) reopenEditor();

    show("#downloads");
    $("#dl-pdf").href = `/api/jobs/${jobId}/file/pdf?download=1&t=${Date.now()}`;
    $("#dl-xml").href = res.files.musicxml ? res.files.musicxml + "&download=1" : "#";
    $("#dl-xml").classList.toggle("hidden", !res.files.musicxml);
    $("#dl-midi").href = res.files.midi ? res.files.midi + "&download=1" : "#";
    $("#dl-midi").classList.toggle("hidden", !res.files.midi);
    const i = res.info;
    $("#summary").querySelectorAll(".r").forEach((x) => x.remove());
    $("#summary").insertAdjacentHTML("beforeend",
      `<span class="r">악보<b>${i.bars}마디 · ${i.key} · ♩=${i.bpm}</b></span>`);
    const nEdits = Object.keys(lyricEdits).length;
    $("#pl-status").textContent = nEdits ? `직접 고친 가사 ${nEdits}곳` : "";
  } catch (err) {
    if (seq === renderSeq) showError("악보 만들기 실패:\n" + err.message);
  } finally {
    if (seq === renderSeq) { show("#busy", false); $("#render").disabled = false; }
  }
}

function indexSheet() {
  noteEls = new Map();
  lyricEls = new Map();
  $$("#sheet .mn, #sheet .ly").forEach((el) => {
    const m = /^([nl])(\d+)(?:_\d+)?$/.exec(el.id || "");
    if (!m) return;
    const map = m[1] === "n" ? noteEls : lyricEls;
    const k = Number(m[2]);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(el);
  });
}

function elsOf(n) {
  return (noteEls.get(n) || []).concat(lyricEls.get(n) || []);
}

// ---------- 재생기
const audio = $("#pl-audio");
const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function setAudioSource() {
  if (!jobId) return;
  const t = audio.currentTime || 0;
  const wasPlaying = !audio.paused;
  audio.src = `/api/jobs/${jobId}/file/${$("#pl-src").value}`;
  audio.addEventListener("loadedmetadata", () => {
    audio.currentTime = t;
    audio.playbackRate = Number($("#pl-rate").value);
    if (wasPlaying) audio.play();
  }, { once: true });
}
function stopAudio() { audio.pause(); }
function togglePlay() {
  if (!audio.src) setAudioSource();
  if (audio.paused) audio.play(); else audio.pause();
}

$("#pl-src").addEventListener("change", setAudioSource);
$("#pl-rate").addEventListener("change", () => { audio.playbackRate = Number($("#pl-rate").value); });
$("#pl-play").addEventListener("click", togglePlay);
audio.addEventListener("play", () => { $("#pl-play").textContent = "⏸"; requestAnimationFrame(loop); });
audio.addEventListener("pause", () => { $("#pl-play").textContent = "▶"; });
audio.addEventListener("ended", () => { $("#pl-play").textContent = "▶"; setActive(-1); });
audio.addEventListener("seeked", () => tick(true));
$("#pl-seek").addEventListener("input", () => {
  if (audio.duration) audio.currentTime = (Number($("#pl-seek").value) / 1000) * audio.duration;
});
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  if ($("#player").classList.contains("hidden")) return;
  e.preventDefault();
  togglePlay();
});

function loop() {
  tick(false);
  if (!audio.paused) requestAnimationFrame(loop);
}

function tick(forceScroll) {
  const t = audio.currentTime || 0;
  const d = audio.duration || 0;
  $("#pl-time").textContent = `${fmt(t)} / ${fmt(d)}`;
  if (d && document.activeElement !== $("#pl-seek")) $("#pl-seek").value = Math.round((t / d) * 1000);
  // 지금 시각에 해당하는 음표 (이진 탐색)
  let lo = 0, hi = T0.length - 1, i = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (T0[mid] <= t + 0.03) { i = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (i >= 0) {
    const cur = timeline[i];
    const next = timeline[i + 1];
    const hold = Math.min(next ? next.t0 : Infinity, cur.t1 + 0.35);
    if (t > hold) i = -1;
  }
  setActive(i, forceScroll);
}

function setActive(i, forceScroll) {
  if (i === activeIdx && !forceScroll) return;
  if (activeIdx >= 0 && timeline[activeIdx]) {
    elsOf(timeline[activeIdx].n).forEach((el) => el.classList.toggle("playing", false));
  }
  activeIdx = i;
  if (i < 0 || !timeline[i]) return;
  const els = elsOf(timeline[i].n);
  els.forEach((el) => el.classList.toggle("playing", true));
  if ($("#pl-follow").checked && els[0]) followScroll(els[0], forceScroll);
}

function followScroll(el, force) {
  const sheet = $("#sheet");
  const r = el.getBoundingClientRect();
  const s = sheet.getBoundingClientRect();
  const top = r.top - s.top;
  if (force || top < s.height * 0.12 || top > s.height * 0.7) {
    sheet.scrollTo({ top: sheet.scrollTop + top - s.height * 0.3, behavior: force ? "auto" : "smooth" });
  }
}

// ---------- 음표 / 가사 눌러서 고치기
$("#sheet").addEventListener("click", (e) => {
  const g = e.target.closest(".mn, .ly");
  if (!g) return;
  const m = /^[nl](\d+)/.exec(g.id || "");
  if (!m) return;
  const idx = timeline.findIndex((x) => x.n === Number(m[1]));
  if (idx < 0) return;
  if (!audio.src) setAudioSource();
  audio.currentTime = timeline[idx].t0;
  openEditor(idx);
});

function phraseEnd(idx) {
  // 1초 이상 쉬는 곳 전까지를 한 구절로 본다
  let j = idx;
  while (j + 1 < timeline.length && timeline[j + 1].t0 - timeline[j].t1 < 1.0) j++;
  return j;
}

function openEditor(idx) {
  if (idx < 0 || idx >= timeline.length) return;
  editorIdx = idx;
  const it = timeline[idx];
  $$("#sheet .selected").forEach((el) => el.classList.toggle("selected", false));
  const els = elsOf(it.n);
  els.forEach((el) => el.classList.toggle("selected", true));
  $("#ed-text").value = it.lyric || "";
  $("#ed-time").textContent = `${fmt(it.t0)} 부근`;
  const ed = $("#editor");
  show("#editor");
  const anchor = (lyricEls.get(it.n) || [])[0] || els[0];
  const viewer = $(".viewer").getBoundingClientRect();
  if (anchor) {
    const r = anchor.getBoundingClientRect();
    let x = r.left - viewer.left - 140;
    let y = r.bottom - viewer.top + 14;
    x = Math.max(8, Math.min(x, viewer.width - 310));
    if (y > viewer.height - 230) y = r.top - viewer.top - 230;
    ed.style.left = x + "px";
    ed.style.top = Math.max(60, y) + "px";
  }
  $("#ed-text").focus();
  $("#ed-text").select();
}
function reopenEditor() {
  const n = timeline[editorIdx] ? timeline[editorIdx].n : null;
  const idx = n == null ? -1 : timeline.findIndex((x) => x.n === n);
  if (idx >= 0) openEditor(idx); else closeEditor();
}
function closeEditor() {
  editorIdx = -1;
  show("#editor", false);
  $$("#sheet .selected").forEach((el) => el.classList.toggle("selected", false));
}

let editTimer = null;
function commitEdits(changes) {
  // 화면의 글자는 바로 바꾸고(악보를 다시 그리면 20초쯤 걸리므로), 설정만 저장해 둔다.
  // PDF·MIDI·MusicXML 은 저장할 때 고친 가사로 새로 만든다.
  for (const [n, text] of changes) {
    lyricEdits[n] = text;
    const it = timeline.find((x) => x.n === n);
    if (it) it.lyric = text;
    patchLyric(n, text);
  }
  $("#pl-status").textContent = `직접 고친 가사 ${Object.keys(lyricEdits).length}곳 · 저장 중…`;
  clearTimeout(editTimer);
  editTimer = setTimeout(async () => {
    try {
      await api(`/api/jobs/${jobId}/settings`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()),
      });
      $("#pl-status").textContent = `직접 고친 가사 ${Object.keys(lyricEdits).length}곳 · 저장됨`;
    } catch (err) {
      showError("가사 저장 실패: " + err.message);
    }
  }, 400);
}

function translateOf(g) {
  const inner = g.firstElementChild;
  const m = /translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)/.exec((inner && inner.getAttribute("transform")) || "");
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function patchLyric(n, text) {
  let g = (lyricEls.get(n) || [])[0];
  if (!g) {
    // 가사가 없던 음표: 같은 줄의 가장 가까운 가사를 본떠 새 글자를 만든다
    const note = (noteEls.get(n) || [])[0];
    if (!note || !text) return;
    const np = translateOf(note);
    const svg = note.ownerSVGElement;
    if (!np || !svg) return;
    let best = null, bestD = Infinity, bestY = 0;
    svg.querySelectorAll(".ly").forEach((ly) => {
      const p = translateOf(ly);
      if (!p || p[1] <= np[1] || p[1] - np[1] > 14) return;
      const dist = Math.abs(p[0] - np[0]) + (p[1] - np[1]) * 0.1;
      if (dist < bestD) { bestD = dist; best = ly; bestY = p[1]; }
    });
    if (!best) return;
    g = best.cloneNode(true);
    g.id = "l" + n;
    g.firstElementChild.setAttribute("transform", `translate(${(np[0] - 0.4).toFixed(4)}, ${bestY})`);
    best.parentNode.appendChild(g);
    lyricEls.set(n, [g]);
  }
  const ts = g.querySelector("tspan") || g.querySelector("text");
  if (ts) ts.textContent = text;
  g.style.display = text ? "" : "none";
}

$("#ed-save").addEventListener("click", () => {
  if (editorIdx < 0) return;
  commitEdits([[timeline[editorIdx].n, $("#ed-text").value.trim()]]);
  openEditor(editorIdx + 1);
});
$("#ed-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#ed-save").click(); }
  if (e.key === "Escape") closeEditor();
});
$("#ed-del").addEventListener("click", () => {
  if (editorIdx < 0) return;
  commitEdits([[timeline[editorIdx].n, ""]]);
  $("#ed-text").value = "";
});
$("#ed-insert").addEventListener("click", () => {
  if (editorIdx < 0) return;
  const text = $("#ed-text").value.trim();
  if (!text) { $("#ed-text").focus(); return; }
  const end = phraseEnd(editorIdx);
  const count = end - editorIdx + 1;
  const vals = timeline.slice(editorIdx, end + 1).map((x) => x.lyric || "");
  const shifted = [text, ...vals];
  // 구절 끝을 넘치는 글자는 마지막 음표에 붙여 둔다
  shifted[count - 1] = shifted.slice(count - 1).filter(Boolean).join("");
  commitEdits(timeline.slice(editorIdx, end + 1).map((x, k) => [x.n, shifted[k]]));
  openEditor(editorIdx + 1);
});
$("#ed-pull").addEventListener("click", () => {
  if (editorIdx < 0) return;
  const end = phraseEnd(editorIdx);
  const vals = timeline.slice(editorIdx + 1, end + 1).map((x) => x.lyric || "");
  vals.push("");
  commitEdits(timeline.slice(editorIdx, end + 1).map((x, k) => [x.n, vals[k]]));
  $("#ed-text").value = vals[0];
});
$("#ed-prev").addEventListener("click", () => openEditor(editorIdx - 1));
$("#ed-next").addEventListener("click", () => openEditor(editorIdx + 1));
$("#ed-close").addEventListener("click", closeEditor);
$("#ed-play").addEventListener("click", () => {
  if (editorIdx < 0) return;
  if (!audio.src) setAudioSource();
  audio.currentTime = Math.max(0, timeline[editorIdx].t0 - 0.5);
  audio.play();
});
