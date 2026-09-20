# -*- coding: utf-8 -*-
"""Every human-facing phrase the office uses, in one table per language.

The ledger stores keys, not sentences: an event records that someone did
`read` at the `records` station with target `main.py`, and the wording is
chosen when a snapshot is rendered.  Switching language therefore re-reads
history in the new language instead of leaving old events stranded in the
one they were recorded in.

`zh-Hant` is the default.  Set `AGENT_OFFICE_LANG=en` (or request
`/api/state?lang=en`) for English.
"""

from __future__ import annotations

import hashlib
import os
import re

DEFAULT_LOCALE = "zh-Hant"
FALLBACK_LOCALE = "en"

LOCALES = {
    "zh-Hant": {
        "display_name": "繁體中文",
        "lead_name": "Claude",
        "office_name": "辦公室",
        "stations": {
            "desk": ("大辦公區", "把工作寫出來的地方"),
            "records": ("檔案室", "卷宗、書架、舊文件"),
            "drafting": ("製圖桌", "圖面在這裡改"),
            "server_room": ("機房", "機器、腳本、風扇聲"),
            "mailroom": ("收發室", "所有從外面進來的東西"),
            "war_room": ("作戰室", "規劃、簡報、工作板"),
            "reception": ("櫃檯", "等一個簽名"),
            "break_room": ("茶水間", "兩件工作之間"),
            "door": ("大門", "進出的地方"),
        },
        "verbs": {
            "read": "從檔案櫃抽出{target}",
            "notebook_read": "翻閱{target}",
            "glob": "在書架上找{target}",
            "grep": "在卷宗裡翻找{target}",
            "edit": "在{target}上批改",
            "write": "起草{target}",
            "notebook_edit": "修訂{target}",
            "bash": "執行{target}",
            "bash_output": "查看{target}的進度",
            "kill_shell": "關掉{target}",
            "web_fetch": "拆開{target}寄來的郵件",
            "web_search": "翻同業快報找{target}",
            "task": "向新人交代{target}",
            "workflow": "跑{target}這套流程",
            "skill": "查{target}的作業手冊",
            "todo": "更新工作板",
            "task_create": "把{target}貼上工作板",
            "task_update": "把{target}往前挪一格",
            "plan": "報告計畫",
            "ask": "向老闆確認{target}",
            "send_file": "把{target}送過去",
            "artifact": "把{target}掛上牆",
            "mcp": "和{target}通話中",
            "generic": "處理{target}",
        },
        "roles": {
            "lead": "主管",
            "Explore": "研究員",
            "Plan": "規劃師",
            "general-purpose": "通才",
            "claude": "同仁",
            "code-review": "審查員",
            "statusline-setup": "佈置員",
            "fork": "分身",
            "unknown": "同仁",
        },
        "doing": {
            "settling_in": "正在安頓",
            "opening_office": "開門準備",
            "reading_order": "正在看新工單",
            "back_at_desk": "回到座位",
            "sorting_mess": "收拾殘局",
            "being_seated": "正在帶位",
            "heading_home": "準備下班",
            "handing_report": "交出報告",
            "handing_back": "把工作交回去",
            "waiting_signature": "在櫃檯等簽名",
            "waiting_next": "等下一張工單",
            "desk_cleared": "桌面清空了",
            "boxing_papers": "把舊文件裝箱",
            "line_dead": "線路中斷：{detail}",
        },
        "log": {
            "session_open": "開門上工（{kind}）",
            "session_close": "關燈下班",
            "ticket": "從櫃檯接下一張新工單",
            "filed": "把{target}歸檔",
            "snag": "出狀況：{detail}",
            "arrive": "以{title}的身分報到",
            "depart": "交出報告，下班",
            "wait": "在櫃檯等一個簽名",
            "break": "走進茶水間",
            "handover": "把完成的工作交回櫃檯",
            "line_dead": "線路斷了（{detail}）",
            "compact_start": "把舊文件裝箱送進檔案室",
            "compact_done": "回到清空的桌面",
            "plain": "{text}",
        },
        "kind": {
            "startup": "開工",
            "resume": "續班",
            "clear": "清空重來",
            "compact": "整併",
            "fork": "分流",
        },
        "word": {
            "paperwork": "文件",
            "job": "這件事",
            "notification": "通知",
            "no_text": "（沒有內容）",
        },
        "names": (
            "宇安", "品睿", "家瑜", "語桐", "詠晴", "柏翰", "昱安", "承翰",
            "若彤", "思源", "亭佑", "宥辰", "知恩", "之晴", "立安", "恩琦",
            "亦翔", "千樺", "睿翔", "佳恩", "尚勳", "又寧", "沛瑜", "禹辰",
            "允中", "書懷", "皓宇", "芷安", "秉睿", "詩涵", "冠廷", "祐寧",
            "宜蓁", "昀希", "澔平", "采靈",
        ),
    },
    "en": {
        "display_name": "English",
        "lead_name": "Claude",
        "office_name": "Office",
        "stations": {
            "desk": ("Bullpen", "where the work gets written up"),
            "records": ("Records Room", "files, shelves, old paperwork"),
            "drafting": ("Drafting Table", "where edits are drawn up"),
            "server_room": ("Server Room", "machines, scripts, fans"),
            "mailroom": ("Mail Room", "anything from outside the building"),
            "war_room": ("War Room", "planning, briefing, the job board"),
            "reception": ("Reception", "waiting on a signature"),
            "break_room": ("Break Room", "between assignments"),
            "door": ("Front Door", "arrivals and departures"),
        },
        "verbs": {
            "read": "pulling {target} from the cabinet",
            "notebook_read": "leafing through {target}",
            "glob": "scanning the shelves for {target}",
            "grep": "digging through the files for {target}",
            "edit": "marking up {target}",
            "write": "drafting {target}",
            "notebook_edit": "revising {target}",
            "bash": "running {target}",
            "bash_output": "checking on {target}",
            "kill_shell": "shutting down {target}",
            "web_fetch": "opening mail from {target}",
            "web_search": "combing the trade papers for {target}",
            "task": "briefing a new hire on {target}",
            "workflow": "running the {target} playbook",
            "skill": "looking up the {target} manual",
            "todo": "updating the job board",
            "task_create": "pinning up {target}",
            "task_update": "moving {target} across the board",
            "plan": "presenting the plan",
            "ask": "asking the boss about {target}",
            "send_file": "couriering {target} over",
            "artifact": "mounting {target} on the wall",
            "mcp": "on the line with {target}",
            "generic": "working on {target}",
        },
        "roles": {
            "lead": "Desk Lead",
            "Explore": "Researcher",
            "Plan": "Architect",
            "general-purpose": "Generalist",
            "claude": "Associate",
            "code-review": "Reviewer",
            "statusline-setup": "Fitter",
            "fork": "Understudy",
            "unknown": "Associate",
        },
        "doing": {
            "settling_in": "settling in",
            "opening_office": "opening up the office",
            "reading_order": "reading the new work order",
            "back_at_desk": "back at the desk",
            "sorting_mess": "sorting out a mess",
            "being_seated": "being shown to a desk",
            "heading_home": "heading home",
            "handing_report": "handing in the report",
            "handing_back": "handing the work back",
            "waiting_signature": "waiting on a signature",
            "waiting_next": "waiting for the next order",
            "desk_cleared": "desk cleared",
            "boxing_papers": "boxing up old paperwork",
            "line_dead": "line went dead: {detail}",
        },
        "log": {
            "session_open": "unlocks the office ({kind})",
            "session_close": "lights out",
            "ticket": "takes a new work order off the counter",
            "filed": "files {target}",
            "snag": "hits a snag: {detail}",
            "arrive": "arrives as a {title}",
            "depart": "hands in the report and clocks out",
            "wait": "waits at reception for a signature",
            "break": "steps into the break room",
            "handover": "turns the finished work back over the counter",
            "line_dead": "the line goes dead ({detail})",
            "compact_start": "boxes up old paperwork for the archive",
            "compact_done": "comes back to a cleared desk",
            "plain": "{text}",
        },
        "kind": {
            "startup": "startup",
            "resume": "resume",
            "clear": "clear",
            "compact": "compact",
            "fork": "fork",
        },
        "word": {
            "paperwork": "the paperwork",
            "job": "the job",
            "notification": "notification",
            "no_text": "(no text)",
        },
        "names": (
            "Ada", "Bruno", "Cass", "Dara", "Emil", "Fern", "Gita", "Hal",
            "Ines", "Jules", "Kato", "Lena", "Mira", "Nils", "Ozzy", "Pia",
            "Quinn", "Rue", "Sana", "Theo", "Uma", "Vic", "Wren", "Xan",
            "Yara", "Zeb", "Arlo", "Bex", "Cleo", "Dov", "Esme", "Finn",
            "Gwen", "Hugo", "Iris", "Joss",
        ),
    },
}

ALIASES = {
    "zh": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh_tw": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh_hant": "zh-Hant",
    "zh-hk": "zh-Hant",
    "tw": "zh-Hant",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
}


def normalize_locale(value=None):
    """Resolve whatever we were handed to a locale we actually carry."""
    if not value:
        value = os.environ.get("AGENT_OFFICE_LANG") or DEFAULT_LOCALE
    value = str(value).strip()
    if value in LOCALES:
        return value
    lowered = value.lower()
    if lowered in ALIASES:
        return ALIASES[lowered]
    for key in LOCALES:
        if key.lower() == lowered:
            return key
    return DEFAULT_LOCALE


_FIELD = re.compile(r"\{(\w+)\}")


def _is_wide(ch):
    """True for Han, kana and full-width punctuation."""
    return bool(ch) and (
        "\u3040" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        or "\uf900" <= ch <= "\ufaff"
        or "\uff00" <= ch <= "\uffef"
    )


def _needs_space(left, right):
    """Chinese text sets a space against Latin words, but not against itself."""
    if not left or not right or left.isspace() or right.isspace():
        return False
    if _is_wide(left) and right.isascii() and right.isalnum():
        return True
    if _is_wide(right) and left.isascii() and left.isalnum():
        return True
    return False


def _fill(template, args):
    """Substitute {fields}, spacing each seam by what lands next to it."""
    out = []
    cursor = 0
    for match in _FIELD.finditer(template):
        out.append(template[cursor:match.start()])
        value = str(args.get(match.group(1), match.group(0)))
        grown = "".join(out)
        if value and _needs_space(grown[-1:], value[:1]):
            out.append(" ")
        out.append(value)
        following = template[match.end():match.end() + 1]
        if value and _needs_space(value[-1:], following):
            out.append(" ")
        cursor = match.end()
    out.append(template[cursor:])
    return "".join(out)


def _lookup(locale, section, key):
    for candidate in (locale, FALLBACK_LOCALE):
        table = LOCALES.get(candidate, {}).get(section, {})
        if key in table:
            return table[key]
    return None


def text(locale, section, key, **args):
    """A finished sentence: `text(loc, "log", "filed", target="a.py")`."""
    template = _lookup(locale, section, key)
    if template is None:
        return str(key)
    if not args:
        return template
    try:
        return _fill(template, args)
    except (KeyError, IndexError, ValueError):
        return template


def station(locale, key):
    pair = _lookup(locale, "stations", key)
    if not pair:
        return (key, "")
    return pair


def role_title(locale, role):
    found = _lookup(locale, "roles", role)
    if found:
        return found
    cleaned = str(role or "").replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return _lookup(locale, "roles", "unknown")
    return cleaned.title() if cleaned.isascii() else cleaned


def worker_name(locale, worker_id):
    names = LOCALES.get(locale, LOCALES[FALLBACK_LOCALE]).get("names") or LOCALES[FALLBACK_LOCALE]["names"]
    digest = hashlib.sha1(str(worker_id).encode("utf-8", "replace")).digest()
    return names[digest[0] % len(names)]


def lead_name(locale):
    return LOCALES.get(locale, {}).get("lead_name") or "Claude"


def office_name(locale):
    return LOCALES.get(locale, {}).get("office_name") or "Office"


def available():
    return [{"code": code, "name": table["display_name"]} for code, table in LOCALES.items()]
