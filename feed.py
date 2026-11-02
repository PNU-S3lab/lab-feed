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
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
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
HISTORY = {  # CFP가 올라온 날 = 이 저장소에 해당 연도 항목이 처음 등록된 날
    "sec": ("https://github.com/sec-deadlines/sec-deadlines.github.io", "_data/conferences.yml", "name"),
    "ai": ("https://github.com/huggingface/ai-deadlines", "src/data/conferences", "title"),
}
DEPT_RSS = "https://cse.pusan.ac.kr/bbs/cse/2058/rssList.do?row=30"
DEPT_BASE = "https://cse.pusan.ac.kr"

REMIND_DAYS = 14
STATE_FILE = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("state.json")))
ICS_FILE = Path(os.environ.get("ICS_FILE", Path(__file__).with_name("cfp.ics")))
CHANNEL_ID = os.environ.get("CHANNEL_ID", "C0CE11WLCN6")  # #lab-피드
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


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
NUM = {"one": 1, "two": 2, "three": 3}


def abstract_deadlines(comment, papers):
    """comment에 적힌 초록·논문 등록 마감을 논문 마감 목록에 맞춰 계산한다"""
    text = str(comment or "")
    if not re.search(r"abstract|regist", text, re.I) or re.search(r"once accepted", text, re.I):
        return []
    rel = re.search(r"(\d+|one|two|three) (week|day)s? before", text, re.I)
    if rel:
        n = int(NUM.get(rel[1].lower(), rel[1]))
        step = timedelta(weeks=n) if rel[2].lower() == "week" else timedelta(days=n)
        return [dt - step for dt in papers]
    m = re.search(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", text)
    if not m or m[1].lower() not in MONTHS:
        return []
    out = []
    for dt in papers:  # 연도가 없으면 논문 마감 직전 60일 안에 드는 날짜로 본다
        for y in ([int(m[3])] if m[3] else [dt.year, dt.year - 1]):
            try:
                ab = dt.replace(year=y, month=MONTHS.index(m[1].lower()) + 1, day=int(m[2]))
            except ValueError:
                continue
            if timedelta(0) < dt - ab <= timedelta(days=60):
                out.append(ab)
                break
    return out


def sec_confs():
    core_of = {"ASTAR": "A*", "CORE-A": "A", "CORE-B": "B", "CORE-C": "C"}
    for e in yaml.safe_load(get(SEC_YML)):
        tags = e.get("tags", [])
        if "CONF" not in tags:
            continue
        tz = parse_tz(e.get("timezone"))
        deadlines = e["deadline"] if isinstance(e["deadline"], list) else [e["deadline"]]
        dblp = re.search(r"db/(conf/[^/]+)/", e.get("dblp") or "")
        papers = [dt for dt in (parse_dt(d, tz) for d in deadlines if d) if dt]
        abstracts = abstract_deadlines(e.get("comment"), papers)
        yield {
            "src": "sec", "name": e["name"], "year": e["year"], "full": e.get("description", ""),
            "link": e.get("link", ""), "date": e.get("date", ""), "place": e.get("place", ""),
            "core": next((core_of[t] for t in tags if t in core_of), ""),
            "dblp": dblp.group(1) if dblp else "",
            "deadlines": [("초록 마감", dt) for dt in abstracts]
            + [("논문 마감" if abstracts else "마감", dt) for dt in papers],
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
                "src": "ai", "name": e["title"], "year": e["year"], "full": e.get("full_name", ""),
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
        c["deadlines"] = sorted((d for d in c["deadlines"] if d[1] > now), key=lambda d: d[1])
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


def first_listed(src):
    """저장소 기록을 거슬러 올라가며 {(이름, 연도): 처음 등록된 날}을 만든다"""
    url, path, key = HISTORY[src]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "-q", "--shallow-since=18 months ago", url, tmp], check=True)
        git = ["git", "-C", tmp]
        log = subprocess.run(git + ["log", "--format=@%H %cs", "--name-only", "--", path],
                             capture_output=True, text=True, check=True).stdout
        first = {}
        for line in log.split("\n"):  # 최신 → 과거 순이라 마지막에 남는 값이 가장 이른 날
            if line.startswith("@"):
                commit, day = line[1:].split()
                continue
            if not line.endswith(".yml"):
                continue
            try:
                entries = yaml.safe_load(subprocess.run(git + ["show", f"{commit}:{line}"], capture_output=True, text=True).stdout)
            except yaml.YAMLError:
                continue
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get(key):
                    first[(e[key], e.get("year"))] = date.fromisoformat(day)
    return first


def attach_opened(state, confs, today):
    opened = state.setdefault("opened", {})
    missing = {c["src"] for c in confs if c["id"] not in opened}
    found = {}
    for src in missing:
        try:
            found.update({(src, *k): v for k, v in first_listed(src).items()})
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"{src} 기록 조회 실패: {e}")
    for c in confs:
        if c["id"] not in opened:  # 기록에서 못 찾으면 이 봇이 처음 본 날로 둔다
            opened[c["id"]] = str(found.get((c["src"], c["name"], c["year"]), today))
        c["opened"] = date.fromisoformat(opened[c["id"]])


# ---------- 캘린더 ----------

def ics_text(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line):
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " "
        cur += ch
    return "\r\n".join(out + [cur])


def write_ics(confs, now):
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//S3Lab//lab-feed//KO", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:S3Lab 학회 마감", "X-WR-TIMEZONE:Asia/Seoul",
             "X-WR-CALDESC:CORE A*·A 또는 정보과학회 최우수·우수 학회 마감일 (lab-feed)"]
    for c in confs:
        info = f"{c['full']}\n{c['grade']}\n{c['date']} {c['place']}\n{c['link']}"
        due = "\n".join(f"{label} {when(dt)}" for label, dt in c["deadlines"])
        last = c["deadlines"][-1][1].astimezone(KST).date()
        # CFP가 올라온 날부터 마지막 마감일까지 종일 막대로 보여 준다
        lines += [
            "BEGIN:VEVENT",
            f"UID:{c['id']}-cfp@lab-feed.s3lab",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{min(c['opened'], last):%Y%m%d}",
            f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{ics_text(f'{c['name']} {c['year']} CFP')}",
            f"DESCRIPTION:{ics_text(f'CFP 등록 {c['opened']:%m/%d}\n{due}\n{info}')}",
            f"URL:{c['link']}",
            "END:VEVENT",
        ]
        for label, dt in c["deadlines"]:
            start = dt.astimezone(timezone.utc)
            lines += [
                "BEGIN:VEVENT",
                f"UID:{c['id']}-{start:%Y%m%dT%H%M}@lab-feed.s3lab",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
                f"DTEND:{start + timedelta(minutes=30):%Y%m%dT%H%M%SZ}",
                f"SUMMARY:{ics_text(f'{c['name']} {c['year']} {label}')}",
                f"DESCRIPTION:{ics_text(info)}",
                f"URL:{c['link']}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    ICS_FILE.write_text("\r\n".join(ics_fold(l) for l in lines) + "\r\n")


# ---------- 채널 캔버스 ----------

def slack_api(method, payload):
    req = urllib.request.Request(
        f"https://slack.com/api/{method}", json.dumps(payload).encode(),
        {"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if not resp.get("ok"):
        raise RuntimeError(f"{method}: {resp.get('error')}")
    return resp


def canvas_markdown(confs, now):
    rows = sorted(((dt, label, c) for c in confs for label, dt in c["deadlines"]), key=lambda r: (r[0], r[2]["name"]))
    lines = [
        "# 학회 마감 일정",
        f"매일 09:00 자동 갱신 · 마지막 갱신 {now:%Y-%m-%d %H:%M} · 시간은 한국 시간",
        "",
        "CORE A*·A 또는 정보과학회 최우수·우수 학회만 모았습니다. "
        "BK 점수는 BK21플러스 2018 목록 기준입니다. "
        "[구독용 캘린더 파일](https://raw.githubusercontent.com/PNU-S3lab/lab-feed/main/cfp.ics)",
    ]
    month = None
    for dt, label, c in rows:
        k = dt.astimezone(KST)
        if (k.year, k.month) != month:
            month = (k.year, k.month)
            lines += ["", f"## {k.year}년 {k.month}월", "", "| 마감 | D-day | 학회 | 등급 |", "| --- | --- | --- | --- |"]
        dday = (k.date() - now.date()).days
        lines.append(f"| {when(dt)} {label} | D-{dday} | [{c['name']} {c['year']}]({c['link']}) | {c['grade']} |")
    if not rows:
        lines += ["", "지금 접수 중인 학회가 없습니다."]
    return "\n".join(lines)


def update_canvas(state, confs, now):
    content = {"type": "markdown", "markdown": canvas_markdown(confs, now)}
    if state.get("canvas_id"):
        slack_api("canvases.edit", {"canvas_id": state["canvas_id"],
                                    "changes": [{"operation": "replace", "document_content": content}]})
    else:
        state["canvas_id"] = slack_api("conversations.canvases.create",
                                       {"channel_id": CHANNEL_ID, "document_content": content})["canvas_id"]


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
    ap.add_argument("--canvas-only", action="store_true", help="알림 없이 캔버스와 캘린더만 갱신")
    args = ap.parse_args()

    now = datetime.now(KST)
    state = load_state()
    first_run = not state
    state.setdefault("cfp", [])
    state.setdefault("reminded", [])
    state.setdefault("dept", [])

    confs = qualified(now)
    attach_opened(state, confs, now.date())
    if args.dry_run:
        print(canvas_markdown(confs, now), end="\n\n")
    if args.canvas_only:
        update_canvas(state, confs, now)
        write_ics(confs, now)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        return
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

        for link, title, url in reversed(list(dept_posts())):
            if link not in state["dept"]:
                post(f"[학과공지] <{url}|{title}>", args.dry_run)
                state["dept"].append(link)

    state["dept"] = state["dept"][-200:]
    if not args.dry_run:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        write_ics(confs, now)
        if os.environ.get("SLACK_BOT_TOKEN"):
            # 캔버스가 실패해도 알림 기록은 이미 저장되어 같은 알림이 다시 가지 않는다
            update_canvas(state, confs, now)
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
