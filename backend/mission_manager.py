#!/usr/bin/env python3
"""Daily mission + streak manager for Star Office UI."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from typing import Dict, List


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass
    return default


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _iter_rows_for_date(history_file: str, date_str: str):
    if not os.path.exists(history_file):
        return
    with open(history_file, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if str(row.get("ts", "")).startswith(date_str):
                yield row


def compute_daily_metrics(history_file: str, date_str: str) -> Dict[str, int]:
    prev_state_by_agent: Dict[str, str] = {}
    completions = 0
    error_count = 0
    active_agents = set()
    max_concurrent_active = 0

    active_states = {"writing", "researching", "executing", "syncing"}

    for row in _iter_rows_for_date(history_file, date_str):
        agent = str(row.get("agent") or "").strip()
        if not agent:
            continue
        state = str(row.get("state") or "").strip().lower()

        if prev_state_by_agent.get(agent) == "executing" and state == "idle":
            completions += 1

        if state == "error":
            error_count += 1

        if state in active_states:
            active_agents.add(agent)
        elif state in {"idle", "error"} and agent in active_agents:
            active_agents.remove(agent)

        max_concurrent_active = max(max_concurrent_active, len(active_agents))
        prev_state_by_agent[agent] = state

    return {
        "task_completions": completions,
        "error_count": error_count,
        "max_concurrent_active": max_concurrent_active,
    }


def _default_missions() -> List[dict]:
    target_n = 5
    return [
        {"id": "daily_tasks", "title": f"오늘 작업 {target_n}개 완료", "target": target_n, "metric": "task_completions", "goal": "gte"},
        {"id": "daily_no_error", "title": "에러 0회", "target": 0, "metric": "error_count", "goal": "eq"},
        {"id": "daily_3_agents", "title": "에이전트 동시 가동 3개", "target": 3, "metric": "max_concurrent_active", "goal": "gte"},
    ]


def _mission_done(mission: dict, metrics: dict) -> bool:
    value = int(metrics.get(mission.get("metric"), 0) or 0)
    target = int(mission.get("target", 0) or 0)
    if mission.get("goal") == "eq":
        return value == target
    return value >= target


def _mission_progress(mission: dict, metrics: dict) -> int:
    value = int(metrics.get(mission.get("metric"), 0) or 0)
    target = int(mission.get("target", 0) or 0)
    if mission.get("goal") == "eq":
        return max(0, 1 if value == target else 0)
    return min(value, target)


def _date_diff_days(a: str, b: str) -> int:
    da = datetime.strptime(a, "%Y-%m-%d")
    db = datetime.strptime(b, "%Y-%m-%d")
    return (da - db).days


def evaluate_and_save(missions_state_file: str, history_file: str, date_str: str | None = None) -> dict:
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    state = _load_json(missions_state_file, {"dates": {}, "streak": {"current": 0, "max": 0, "lastCompletedDate": None}})

    state.setdefault("dates", {})
    state.setdefault("streak", {"current": 0, "max": 0, "lastCompletedDate": None})

    date_state = state["dates"].setdefault(today, {})
    missions = date_state.get("missions")
    if not isinstance(missions, list) or not missions:
        missions = _default_missions()
        date_state["missions"] = missions

    metrics = compute_daily_metrics(history_file, today)

    mission_payload = []
    done_all = True
    completed_ids = []
    for m in missions:
        done = _mission_done(m, metrics)
        if done:
            completed_ids.append(m.get("id"))
        else:
            done_all = False

        target = int(m.get("target", 1) or 1)
        progress = _mission_progress(m, metrics)
        progress_target = 1 if m.get("goal") == "eq" else target
        mission_payload.append({
            **m,
            "progress": progress,
            "rawProgress": int(metrics.get(m.get("metric"), 0) or 0),
            "target": progress_target,
            "done": done,
        })

    date_state["completed"] = sorted(set(completed_ids))
    date_state["allDone"] = done_all

    streak = state["streak"]
    last_completed = streak.get("lastCompletedDate")

    if done_all and last_completed != today:
        if last_completed and _date_diff_days(today, last_completed) == 1:
            streak["current"] = int(streak.get("current", 0) or 0) + 1
        else:
            streak["current"] = 1
        streak["lastCompletedDate"] = today
        streak["max"] = max(int(streak.get("max", 0) or 0), int(streak.get("current", 0) or 0))

    _save_json(missions_state_file, state)

    return {
        "date": today,
        "missions": mission_payload,
        "metrics": metrics,
        "streak": {
            "current": int(streak.get("current", 0) or 0),
            "max": int(streak.get("max", 0) or 0),
            "lastCompletedDate": streak.get("lastCompletedDate"),
            "flame": "🔥" * max(1, min(5, int(streak.get("current", 0) or 0))) if int(streak.get("current", 0) or 0) > 0 else ""
        }
    }
