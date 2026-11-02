#!/usr/bin/env python3
"""lab-피드: 우수 학회 CFP와 학과 공지를 Slack 웹훅으로 올린다.

CFP 출처: sec-deadlines(보안), huggingface/ai-deadlines(AI)
등급 출처: Pusnow gist (정보과학회 2024, BK21플러스 IF 2018)
남기는 기준: CORE A* 또는 A, 또는 정보과학회 최우수·우수
"""
import argparse
import csv
import io
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KST = ZoneInfo("Asia/Seoul")
AOE = timezone(timedelta(hours=-12))
WEEKDAY = "월화수목금토일"
TZ_ABBR = {"PST": -8, "PDT": -7, "EST": -5, "EDT": -4, "CET": 1, "CEST": 2, "BST": 1, "JST": 9, "KST": 9}

RANK_CSV = ("https://gist.githubusercontent.com/Pusnow/6eb933355b5cb8d31ef1abcb3c3e1206/raw/"
            "CS%20%EB%B6%84%EC%95%BC%20%EC%9A%B0%EC%88%98%20%ED%95%99%EC%88%A0%EB%8C%80%ED%9A%8C%20%EB%AA%A9%EB%A1%9D.csv")
SEC_YML = "https://raw.githubusercontent.com/sec-deadlines/sec-deadlines.github.io/master/_data/conferences.yml"
AI_DIR = "https://api.github.com/repos/huggingface/ai-deadlines/contents/src/data/conferences"
DEPT_RSS = "https://cse.pusan.ac.kr/bbs/cse/2058/rssList.do?row=30"
DEPT_BASE = "https://cse.pusan.ac.kr"

REMIND_DAYS = 14
SUMMARY_WEEKS = 8
STATE_FILE = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("state.json")))
VARIANT_RE = re.compile(r"\s*\((oral|poster|spotlight)\)$")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "s3lab-feed"})
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def norm(name):
    return re.sub(r"[^a-z0-9&]", "", name.lower())


# ---------- 등급 ----------

def load_ranks():
    """gist CSV → (dblp key 색인, 약자 색인). 값은 {'kiise': 최우수|우수|'', 'bk': {변형: IF}}"""
    by_dblp, by_name = {}, {}
    for row in csv.DictReader(io.StringIO(get(RANK_CSV))):
        abbr = row["약자"]
        variant = (VARIANT_RE.search(abbr) or [None, ""])[1]
        base = VARIANT_RE.sub("", abbr)
        key = row["DBLP Key"].strip()
        entry = by_dblp.get(key) or {"kiise": "", "bk": {}}
        entry["kiise"] = entry["kiise"] or row["한국정보과학회 (2024)"]
        entry["bk"][variant] = row["BK21플러스 IF (2018)"]
        if key:
            by_dblp[key] = entry
        for alias in base.split("/"):
            by_name[norm(alias)] = entry
    return by_dblp, by_name


def bk_label(bk):
    if set(bk) == {""}:
        return f"BK {bk[''] or '없음'}"
    if len(set(bk.values())) == 1:
        return f"BK {next(iter(bk.values())) or '없음'}"
    return "BK " + " · ".join(f"{v} {bk[v] or '없음'}" for v in ("oral", "spotlight", "poster") if v in bk)


# ---------- CFP ----------

def parse_tz(name):
    if not name or name in ("AoE", "Etc/GMT+12"):
        return AOE
    m = re.fullmatch(r"(?:UTC|GMT)([+-]\d{1,2})?", name)
    if m:
        return timezone(timedelta(hours=int(m.group(1) or 0)))
    if name in TZ_ABBR:
        return timezone(timedelta(hours=TZ_ABBR[name]))
    try:
        return ZoneInfo(name)
    except Exception:  # 모르는 표기는 가장 늦은 시간대(AoE)로 본다
        return AOE


def parse_dt(s, tz):
    s = str(s).strip()
    fmt = "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(s, fmt).replace(tzinfo=tz)
    except ValueError:  # '%Y-05-31 23:59'처럼 아직 정해지지 않은 마감
        return None


def sec_confs():
    core_of = {"ASTAR": "A*", "CORE-A": "A", "CORE-B": "B", "CORE-C": "C"}
    for e in yaml.safe_load(get(SEC_YML)):
        tags = e.get("tags", [])
        if "CONF" not in tags:
            continue
        tz = parse_tz(e.get("timezone"))
        deadlines = e["deadline"] if isinstance(e["deadline"], list) else [e["deadline"]]
        dblp = re.search(r"db/(conf/[^/]+)/", e.get("dblp") or "")
        yield {
            "name": e["name"], "year": e["year"], "full": e.get("description", ""),
            "link": e.get("link", ""), "date": e.get("date", ""), "place": e.get("place", ""),
            "core": next((core_of[t] for t in tags if t in core_of), ""),
            "dblp": dblp.group(1) if dblp else "",
            "deadlines": [("마감", dt) for dt in (parse_dt(d, tz) for d in deadlines if d) if dt],
        }


def ai_confs():
    labels = {"abstract": "초록 마감", "paper": "논문 마감", "submission": "논문 마감"}
    for f in json.loads(get(AI_DIR)):
        if not f["name"].endswith(".yml"):
            continue
        for e in yaml.safe_load(get(f["download_url"])) or []:
            core = re.search(r"CORE:\s*(A\*|[ABC])(?!\w)", str(e.get("rankings", "")))
            dls = []
            for d in e.get("deadlines") or []:
                if d.get("type") in labels and d.get("date"):
                    tz = parse_tz(d.get("timezone"))
                    dt = parse_dt(d["date"], tz)
                    if dt:
                        dls.append((labels[d["type"]], dt))
            yield {
                "name": e["title"], "year": e["year"], "full": e.get("full_name", ""),
                "link": e.get("link", ""), "date": e.get("date", ""),
                "place": ", ".join(x for x in (e.get("city"), e.get("country")) if x),
                "core": core.group(1) if core else "", "dblp": "", "deadlines": dls,
            }


def qualified(now):
    by_dblp, by_name = load_ranks()
    seen = {}
    for c in [*sec_confs(), *ai_confs()]:
        r = by_dblp.get(c["dblp"]) or by_name.get(norm(c["name"])) or {"kiise": "", "bk": {"": ""}}
        if c["core"] not in ("A*", "A") and r["kiise"] not in ("최우수", "우수"):
            continue
        c["deadlines"] = sorted(d for d in c["deadlines"] if d[1] > now)
        if not c["deadlines"]:
            continue
        grade = [f"CORE {c['core']}" if c["core"] else "", f"정보과학회 {r['kiise']}" if r["kiise"] else "", bk_label(r["bk"])]
        c["grade"] = " · ".join(g for g in grade if g)
        c["id"] = f"{norm(c['name'])}-{c['year']}"
        seen.setdefault(c["id"], c)
    return sorted(seen.values(), key=lambda c: c["deadlines"][0][1])


def when(dt):
    k = dt.astimezone(KST)
    return f"{k:%m/%d}({WEEKDAY[k.weekday()]}) {k:%H:%M}"


def cfp_line(c, all_deadlines=False):
    dls = c["deadlines"] if all_deadlines else c["deadlines"][:1]
    due = ", ".join(f"{label} {when(dt)}" for label, dt in dls)
    return f"• <{c['link']}|*{c['name']} {c['year']}*> ({c['grade']})\n    {due} · {c['date']} {c['place']}".rstrip()


# ---------- 학과 공지 ----------

def dept_posts():
    root = ET.fromstring(get(DEPT_RSS).strip())
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip().rstrip("}")
        link = (item.findtext("link") or "").split("?")[0]
        yield link, title, DEPT_BASE + link


# ---------- 실행 ----------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def post(text, dry):
    if dry:
        print(text, end="\n\n")
        return
    body = json.dumps({"text": text, "unfurl_links": False}).encode()
    req = urllib.request.Request(os.environ["SLACK_WEBHOOK_URL"], body, {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Slack에 올리지 않고 출력만")
    args = ap.parse_args()

    now = datetime.now(KST)
    state = load_state()
    first_run = not state
    state.setdefault("cfp", [])
    state.setdefault("reminded", [])
    state.setdefault("dept", [])

    confs = qualified(now)
    if first_run:
        # 처음에는 하나씩 알리지 않고, 현재 목록만 한 번 요약한다
        state["cfp"] = [c["id"] for c in confs]
        state["dept"] = [link for link, _, _ in dept_posts()]
        post("[CFP] lab-피드를 시작합니다. 지금 접수 중인 우수 학회입니다.\n"
             + "\n".join(cfp_line(c) for c in confs[:30]), args.dry_run)
    else:
        new = [c for c in confs if c["id"] not in state["cfp"]]
        if new:
            post("[CFP] 새로 올라온 CFP\n" + "\n".join(cfp_line(c, True) for c in new), args.dry_run)
            state["cfp"] += [c["id"] for c in new]

        soon = []
        for c in confs:
            for label, dt in c["deadlines"]:
                key = f"{c['id']}-{dt:%Y%m%d%H%M}"
                if dt - now <= timedelta(days=REMIND_DAYS) and key not in state["reminded"]:
                    soon.append(f"• <{c['link']}|*{c['name']} {c['year']}*> {label} {when(dt)} ({c['grade']})")
                    state["reminded"].append(key)
        if soon:
            post(f"[CFP] 마감 {REMIND_DAYS}일 이내\n" + "\n".join(soon), args.dry_run)

        week = now.strftime("%G-W%V")
        if now.weekday() == 0 and state.get("summary") != week:
            horizon = now + timedelta(weeks=SUMMARY_WEEKS)
            upcoming = [c for c in confs if c["deadlines"][0][1] <= horizon]
            if upcoming:
                post(f"[CFP] 앞으로 {SUMMARY_WEEKS}주 안에 마감하는 학회\n" + "\n".join(cfp_line(c) for c in upcoming), args.dry_run)
            state["summary"] = week

        for link, title, url in reversed(list(dept_posts())):
            if link not in state["dept"]:
                post(f"[학과공지] <{url}|{title}>", args.dry_run)
                state["dept"].append(link)

    state["dept"] = state["dept"][-200:]
    if not args.dry_run:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
