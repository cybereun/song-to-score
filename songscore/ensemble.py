"""여러 번 받아쓴 결과를 글자 단위로 맞춰 보고 다수결로 확정한다 (교차 검증).

입력: passes = [[{"s","e","w","p"}, ...], ...]  (한 번 받아쓸 때의 단어 목록)
출력: [{"t","end","text","agree","sure","word_start"}, ...]  (한글은 글자, 영어는 단어 단위)
"""
from __future__ import annotations

import re
from statistics import median

UNIT = re.compile(r"[가-힣]|[A-Za-z0-9'’]+|[぀-ヿ一-鿿]")
NEAR = 2.5  # 이보다 시간이 멀면 같은 글자로 보지 않는다 (초)


def units(words: list[dict]) -> list[dict]:
    out = []
    for w in words:
        parts = UNIT.findall(w["w"])
        if not parts:
            continue
        total = sum(len(x) for x in parts)
        dur = max(w["e"] - w["s"], 0.05)
        acc = 0
        for k, x in enumerate(parts):
            a = w["s"] + dur * acc / total
            acc += len(x)
            b = w["s"] + dur * acc / total
            out.append({"t": a, "end": b, "text": x, "key": x.lower().replace("’", "'"),
                        "p": float(w.get("p", 0.5)), "word_start": k == 0})
    return out


def _col_time(col: dict) -> float:
    return median(u["t"] for u in col.values())


def _merge(columns: list[dict], hyp: list[dict], idx: int) -> list[dict]:
    """columns(지금까지의 합의 틀)에 새 받아쓰기 결과를 시간·글자 일치 기준으로 끼워 맞춘다."""
    n, m = len(columns), len(hyp)
    ct = [_col_time(c) for c in columns]
    keys = [{u["key"] for u in c.values()} for c in columns]
    GAP = 1.0
    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    bk = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0] = [j * GAP for j in range(m + 1)]
    for j in range(1, m + 1):
        bk[0][j] = 2
    for i in range(1, n + 1):
        dp[i][0] = i * GAP
        bk[i][0] = 1
        ti = ct[i - 1]
        for j in range(1, m + 1):
            dt = abs(ti - hyp[j - 1]["t"])
            if dt <= NEAR:
                sub = (0.0 if hyp[j - 1]["key"] in keys[i - 1] else 1.2) + 0.35 * dt
                best, how = dp[i - 1][j - 1] + sub, 0
            else:
                best, how = INF, 0
            if dp[i - 1][j] + GAP < best:
                best, how = dp[i - 1][j] + GAP, 1
            if dp[i][j - 1] + GAP < best:
                best, how = dp[i][j - 1] + GAP, 2
            dp[i][j], bk[i][j] = best, how
    out = []
    i, j = n, m
    while i or j:
        how = bk[i][j]
        if how == 0:
            col = dict(columns[i - 1])
            col[idx] = hyp[j - 1]
            out.append(col)
            i, j = i - 1, j - 1
        elif how == 1:
            out.append(columns[i - 1])
            i -= 1
        else:
            out.append({idx: hyp[j - 1]})
            j -= 1
    out.reverse()
    return out


def combine(passes: list[list[dict]], weights: list[float] | None = None) -> list[dict]:
    hyps = [units(p) for p in passes]
    n_pass = len(hyps)
    if not any(hyps):
        return []
    weights = weights or [1.0] * n_pass
    # 가장 많이 알아들은(확률 합이 큰) 결과를 기준으로 시작
    order = sorted(range(n_pass), key=lambda k: -sum(u["p"] for u in hyps[k]))
    columns = [{order[0]: u} for u in hyps[order[0]]]
    for k in order[1:]:
        columns = _merge(columns, hyps[k], k) if columns else [{k: u} for u in hyps[k]]
    out = []
    for col in columns:
        scores: dict[str, float] = {}
        voters: dict[str, list] = {}
        for k, u in col.items():
            scores[u["key"]] = scores.get(u["key"], 0.0) + weights[k] * (0.45 + 0.55 * u["p"])
            voters.setdefault(u["key"], []).append((k, u))
        best = max(scores, key=scores.get)
        # '여기엔 글자가 없다'는 표: 이 자리에서 아무것도 못 들은 결과의 수
        silent = sum(weights[k] for k in range(n_pass) if k not in col)
        if silent * 0.55 > sum(scores.values()):
            continue
        v = voters[best]
        agree = sum(weights[k] for k, _ in v) / sum(weights)
        top = max(v, key=lambda kv: kv[1]["p"])[1]
        prob = sum(u["p"] for _, u in v) / len(v)
        out.append({
            "t": round(median(u["t"] for _, u in v), 3),
            "end": round(median(u["end"] for _, u in v), 3),
            "text": top["text"], "key": best, "agree": round(agree, 2), "prob": round(prob, 3),
            # sure: 화면에 '확인 필요' 표시를 하지 않을 만큼 확실함 (5번 중 4번 이상 같고 신뢰도도 높음)
            "sure": bool(agree >= 0.8 and prob >= 0.6),
            "word_start": sum(u["word_start"] for _, u in v) * 2 >= len(v),
        })
    out.sort(key=lambda u: u["t"])
    return dedupe(out)


def dedupe(units_: list[dict]) -> list[dict]:
    """받아쓰기마다 시간이 어긋나 같은 글자가 두 번 들어간 것(소수 의견 쪽)을 뺀다."""
    drop = set()
    for i, u in enumerate(units_):
        if u["agree"] > 0.4:
            continue
        for j in range(max(0, i - 3), min(len(units_), i + 4)):
            o = units_[j]
            if (j != i and j not in drop and o["text"] == u["text"] and abs(o["t"] - u["t"]) < 0.7
                    and o["agree"] > u["agree"]):
                drop.add(i)
                break
    return [u for i, u in enumerate(units_) if i not in drop]
