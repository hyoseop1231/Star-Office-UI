#!/usr/bin/env python3
"""XP/Level manager for Star Office UI."""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Dict, List, Tuple

TOKEN_PATTERNS = [
    re.compile(r"tokens?\s*[:=]\s*(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*tokens?", re.IGNORECASE),
    re.compile(r"토큰\s*[:=]?\s*(\d+)")
]


def level_from_xp(xp: int) -> int:
    """Lv1(0), Lv2(100), Lv3(250), Lv4(500), Lv5(1000), then +500."""
    xp = int(xp or 0)
    if xp < 100:
        return 1
    if xp < 250:
        return 2
    if xp < 500:
        return 3
    if xp < 1000:
        return 4
    return 5 + max(0, (xp - 1000) // 500)


def title_from_level(level: int) -> str:
    if level <= 1:
        return "인턴"
    if level == 2:
        return "주니어"
    if level == 3:
        return "미드레벨"
    if level == 4:
        return "시니어"
    if level == 5:
        return "리드"
    return "전설의 에이전트"


def _extract_tokens(detail: str) -> int:
    if not detail:
        return 0
    for p in TOKEN_PATTERNS:
        m = p.search(str(detail))
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0
    return 0


def _iter_history_rows(history_file: str):
    if not os.path.exists(history_file):
        return
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    yield row
                except Exception:
                    continue
    except Exception:
        return


def compute_xp_by_agent(history_file: str) -> Dict[str, int]:
    xp_map: Dict[str, int] = {}
    prev_state: Dict[str, str] = {}

    for row in _iter_history_rows(history_file):
        agent = str(row.get("agent") or "").strip()
        if not agent:
            continue
        state = str(row.get("state") or "").strip().lower()
        detail = str(row.get("detail") or "")

        if prev_state.get(agent) == "executing" and state == "idle":
            gain = 10
            if "error" not in detail.lower() and "에러" not in detail:
                gain += 5
            gain += _extract_tokens(detail) // 1000
            xp_map[agent] = xp_map.get(agent, 0) + gain

        prev_state[agent] = state

    return xp_map


def refresh_agents_xp(agents_state_file: str, history_file: str) -> Tuple[List[dict], List[dict]]:
    """Recompute XP/levels from history and update agents-state file.

    Returns (agents, level_up_events)
    level_up_events: [{type, agent, level, xp}]
    """
    agents: List[dict] = []
    if os.path.exists(agents_state_file):
        try:
            with open(agents_state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    agents = raw
        except Exception:
            agents = []

    xp_map = compute_xp_by_agent(history_file)
    level_up_events: List[dict] = []

    for a in agents:
        name = str(a.get("name") or "").strip()
        old_level = int(a.get("level") or 1)
        new_xp = int(xp_map.get(name, 0))
        new_level = level_from_xp(new_xp)

        a["xp"] = new_xp
        a["level"] = new_level
        a["title"] = title_from_level(new_level)
        a.setdefault("updated_at", datetime.now().isoformat())

        if new_level > old_level:
            level_up_events.append({
                "type": "level_up",
                "agent": name,
                "level": new_level,
                "xp": new_xp,
            })

    try:
        with open(agents_state_file, "w", encoding="utf-8") as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return agents, level_up_events
