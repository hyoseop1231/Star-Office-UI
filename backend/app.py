#!/usr/bin/env python3
"""Star Office UI - Backend State Service"""

from flask import Flask, jsonify, send_from_directory, make_response, request
from flask_socketio import SocketIO
from datetime import datetime, timedelta
import json
import os
import re
import threading
import subprocess
from collections import defaultdict
from xp_manager import refresh_agents_xp, level_from_xp, title_from_level
from mission_manager import evaluate_and_save as evaluate_daily_missions

def notify_telegram_error(agent_name, detail):
    """에러 상태 시 텔레그램 알림"""
    try:
        msg = f"🚨 Star Office 에러\n에이전트: {agent_name}\n상세: {detail}"
        subprocess.Popen([
            "openclaw", "message", "send",
            "--channel", "telegram",
            "--message", msg
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# Paths (project-relative, no hardcoded absolute paths)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = "/Users/hyoseop1231/.openclaw/workspace/memory"
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
STATE_FILE = os.path.join(ROOT_DIR, "state.json")
AGENTS_STATE_FILE = os.path.join(ROOT_DIR, "agents-state.json")
JOIN_KEYS_FILE = os.path.join(ROOT_DIR, "join-keys.json")
HISTORY_FILE = os.path.join(ROOT_DIR, "history.jsonl")
MISSIONS_STATE_FILE = os.path.join(ROOT_DIR, "missions-state.json")
SHOP_STATE_FILE = os.path.join(ROOT_DIR, "shop-state.json")

LEVEL_TABLE = [
    (0, "인턴"),
    (100, "주니어"),
    (300, "미드레벨"),
    (600, "시니어"),
    (1000, "리드"),
    (2000, "전설의 에이전트")
]

MISSIONS = [
    {"id": "exec20", "title": "오늘 실행 20회", "target": 20, "type": "executing_count"},
    {"id": "noerror6h", "title": "6시간 에러 없음", "target": 360, "type": "error_free_minutes"},
    {"id": "allactive", "title": "전원 동시 작업", "target": 1, "type": "all_active"},
]

SHOP_ITEMS = [
    {"id": "cactus", "name": "선인장", "price": 50, "type": "decoration"},
    {"id": "poster2", "name": "새 포스터", "price": 100, "type": "decoration"},
    {"id": "fancy_desk", "name": "고급 책상", "price": 200, "type": "furniture"},
]

# 이전 auth 상태 추적 (offline 전환 알림용)
_agent_prev_status = {}


def get_yesterday_date_str():
    """어제 날짜 문자열 반환 YYYY-MM-DD"""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def sanitize_content(text):
    """개인정보 보호를 위한 내용 정리"""
    import re
    
    # OpenID, User ID 등 제거
    text = re.sub(r'ou_[a-f0-9]+', '[사용자]', text)
    text = re.sub(r'user_id="[^"]+"', 'user_id="[숨김]"', text)
    
    # 특정 이름 제거
    # 필요시 규칙 추가 가능
    
    # IP 주소, 경로 등 민감 정보 제거
    text = re.sub(r'/root/[^"\s]+', '[경로]', text)
    text = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[IP]', text)
    
    # 전화번호, 이메일 등 제거
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[이메일]', text)
    text = re.sub(r'1[3-9]\d{9}', '[전화번호]', text)
    
    return text


def extract_memo_from_file(file_path):
    """memory 파일에서 표시할 메모 내용 추출"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 실제 내용 추출, 과도한 포장 없이
        lines = content.strip().split("\n")
        
        # 핵심 요점 추출
        core_points = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith("- "):
                core_points.append(line[2:].strip())
            elif len(line) > 10:
                core_points.append(line)
        
        if not core_points:
            return "「어제는 특별한 기록 없음」\n\n「시작이 반이다.」"
        
        # 핵심 내용에서 2-3개 핵심 포인트 추출
        selected_points = core_points[:3]
        
        # 한국 속담 모음
        wisdom_quotes = [
            "「아는 것이 힘이다.」",
            "「천 리 길도 한 걸음부터.」",
            "「아는 것이 힘이다.」",
            "「천 리 길도 한 걸음부터.」",
            "「배움에는 끝이 없다.」",
            "「실패는 성공의 어머니.」",
            "「하늘은 스스로 돕는 자를 돕는다.」",
            "「오늘 할 수 있는 일을 내일로 미루지 마라.」",
            "「돌다리도 두드려 보고 건너라.」",
            "「세 살 버릇 여든까지 간다.」"
        ]
        
        import random
        quote = random.choice(wisdom_quotes)
        
        # 내용 조합
        result = []
        
        # 핵심 내용 추가
        if selected_points:
            for i, point in enumerate(selected_points):
                # 개인정보 정리
                point = sanitize_content(point)
                # 너무 긴 내용 자르기
                if len(point) > 40:
                    point = point[:37] + "..."
                # 한 줄 최대 20자
                if len(point) <= 20:
                    result.append(f"· {point}")
                else:
                    # 20자 단위로 분할
                    for j in range(0, len(point), 20):
                        chunk = point[j:j+20]
                        if j == 0:
                            result.append(f"· {chunk}")
                        else:
                            result.append(f"  {chunk}")
        
        # 속담 추가
        if quote:
            if len(quote) <= 20:
                result.append(f"\n{quote}")
            else:
                for j in range(0, len(quote), 20):
                    chunk = quote[j:j+20]
                    if j == 0:
                        result.append(f"\n{chunk}")
                    else:
                        result.append(chunk)
        
        return "\n".join(result).strip()
        
    except Exception as e:
        print(f"memo 추출 실패: {e}")
        return "「어제 기록 불러오기 실패」\n\n「실패는 성공의 어머니.」"


ACCESS_TOKEN = "4hEFoNBdKmme3zp7eRr9Mw"

def check_auth():
    t = request.args.get("token") or request.cookies.get("star_token")
    return t == ACCESS_TOKEN

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
socketio = SocketIO(app, cors_allowed_origins="*")

# Guard join-agent critical section to enforce per-key concurrency under parallel requests
join_lock = threading.Lock()

# Generate a version timestamp once at server startup for cache busting
VERSION_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


@app.after_request
def add_no_cache_headers(response):
    """Aggressively prevent caching for all responses"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Default state
DEFAULT_STATE = {
    "state": "idle",
    "detail": "대기중...",
    "progress": 0,
    "updated_at": datetime.now().isoformat()
}


def load_state():
    """Load state from file.

    Includes a simple auto-idle mechanism:
    - If the last update is older than ttl_seconds (default 25s)
      and the state is a "working" state, we fall back to idle.

    This avoids the UI getting stuck at the desk when no new updates arrive.
    """
    state = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = None

    if not isinstance(state, dict):
        state = dict(DEFAULT_STATE)

    # Auto-idle
    try:
        ttl = int(state.get("ttl_seconds", 300))
        updated_at = state.get("updated_at")
        s = state.get("state", "idle")
        working_states = {"writing", "researching", "executing"}
        if updated_at and s in working_states:
            # tolerate both with/without timezone
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            # Use UTC for aware datetimes; local time for naive.
            if dt.tzinfo:
                from datetime import timezone
                age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            else:
                age = (datetime.now() - dt).total_seconds()
            if age > ttl:
                state["state"] = "idle"
                state["detail"] = "대기중 (자동으로 휴게 구역 복귀)"
                state["progress"] = 0
                state["updated_at"] = datetime.now().isoformat()
                # persist the auto-idle so every client sees it consistently
                try:
                    save_state(state)
                except Exception:
                    pass
    except Exception:
        pass

    return state


def save_state(state: dict):
    """Save state to file"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def append_history(agent: str, state: str, detail: str):
    """Append one history row and keep only the latest 500 lines."""
    entry = {
        "ts": datetime.now().isoformat(),
        "agent": agent,
        "state": state,
        "detail": detail or ""
    }

    lines = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.readlines() if ln.strip()]
        except Exception:
            lines = []

    lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
    if len(lines) > 500:
        lines = lines[-500:]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)


def broadcast_state_update():
    """Broadcast the latest main + agent state to all websocket clients."""
    payload = {
        "main": load_state(),
        "agents": load_agents_state()
    }
    socketio.emit("state_update", payload)


@socketio.on("connect")
def handle_socket_connect():
    """Send an immediate snapshot when a new client connects."""
    broadcast_state_update()


# Initialize state
if not os.path.exists(STATE_FILE):
    save_state(DEFAULT_STATE)


@app.route("/", methods=["GET"])
def index():
    """Serve the pixel office UI with built-in version cache busting"""
    if not check_auth():
        return "<h2>🔐 접속 토큰이 필요합니다</h2><p>?token=YOUR_TOKEN 을 URL에 추가하세요.</p>", 401
    with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{VERSION_TIMESTAMP}}", VERSION_TIMESTAMP)
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.route("/join", methods=["GET"])
def join_page():
    """Serve the agent join page"""
    with open(os.path.join(FRONTEND_DIR, "join.html"), "r", encoding="utf-8") as f:
        html = f.read()
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.route("/invite", methods=["GET"])
def invite_page():
    """Serve human-facing invite instruction page"""
    with open(os.path.join(FRONTEND_DIR, "invite.html"), "r", encoding="utf-8") as f:
        html = f.read()
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


DEFAULT_AGENTS = [
    {
        "agentId": "star",
        "name": "왕천재",
        "isMain": True,
        "state": "idle",
        "detail": "대기중, 언제든 도와줄 준비 완료",
        "updated_at": datetime.now().isoformat(),
        "area": "breakroom",
        "source": "local",
        "joinKey": None,
        "authStatus": "approved",
        "authExpiresAt": None,
        "lastPushAt": None
    },
    {
        "agentId": "npc1",
        "name": "울트라맨",
        "isMain": False,
        "state": "writing",
        "detail": "작업중...",
        "updated_at": datetime.now().isoformat(),
        "area": "writing",
        "source": "demo",
        "joinKey": None,
        "authStatus": "approved",
        "authExpiresAt": None,
        "lastPushAt": None
    }
]



def track_agent_offline_transition(agents):
    """approved -> offline 전환 시 1회 텔레그램 알림"""
    global _agent_prev_status
    for a in agents:
        aid = a.get("agentId") or a.get("name")
        auth_status = a.get("authStatus", "pending")
        prev_status = _agent_prev_status.get(aid)
        if auth_status == "offline" and prev_status == "approved":
            notify_telegram_error(a.get("name", aid), "오프라인 전환됨")
        _agent_prev_status[aid] = auth_status


def load_agents_state():
    if os.path.exists(AGENTS_STATE_FILE):
        try:
            with open(AGENTS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for a in data:
                        ensure_agent_meta(a)
                    track_agent_offline_transition(data)
                    return data
        except Exception:
            pass
    defaults = [ensure_agent_meta(dict(a)) for a in DEFAULT_AGENTS]
    track_agent_offline_transition(defaults)
    return defaults


def save_agents_state(agents):
    with open(AGENTS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def load_join_keys():
    if os.path.exists(JOIN_KEYS_FILE):
        try:
            with open(JOIN_KEYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("keys"), list):
                    return data
        except Exception:
            pass
    return {"keys": []}


def save_join_keys(data):
    with open(JOIN_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_level(xp: int):
    xp = int(xp or 0)
    level = level_from_xp(xp)
    title = title_from_level(level)
    return level, title


def ensure_agent_meta(agent: dict):
    agent["xp"] = int(agent.get("xp", 0) or 0)
    lvl, title = calc_level(agent["xp"])
    agent["level"] = int(agent.get("level", lvl) or lvl)
    agent["title"] = str(agent.get("title", title) or title)
    agent["points"] = int(agent.get("points", 0) or 0)
    agent["combo"] = int(agent.get("combo", 0) or 0)
    agent["maxCombo"] = int(agent.get("maxCombo", 0) or 0)
    agent["lastComboAt"] = agent.get("lastComboAt")
    return agent


def notify_telegram(message_text: str):
    try:
        subprocess.Popen([
            "openclaw", "message", "send",
            "--channel", "telegram",
            "--message", message_text
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def notify_level_up(agent_name: str, level: int, title: str):
    notify_telegram(f"🎉 {agent_name}이 Lv.{level} {title}로 레벨업!")


def notify_combo(agent_name: str, combo: int):
    notify_telegram(f"🔥 {agent_name} {combo} 콤보!")


def load_shop_state():
    if os.path.exists(SHOP_STATE_FILE):
        try:
            with open(SHOP_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("purchases", {})
                    return data
        except Exception:
            pass
    return {"purchases": {}}


def save_shop_state(data):
    with open(SHOP_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_missions_state():
    if os.path.exists(MISSIONS_STATE_FILE):
        try:
            with open(MISSIONS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {"dates": {}}


def save_missions_state(data):
    with open(MISSIONS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iter_history_rows_for_date(date_str: str):
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
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
    except Exception:
        return


def compute_today_metrics(today: str):
    rows = list(iter_history_rows_for_date(today))
    exec_count = 0
    leaderboard = defaultdict(int)
    all_active = 0
    active_map = {}
    last_error_at = None
    for r in rows:
        state = r.get("state")
        agent = r.get("agent", "unknown")
        ts = r.get("ts")
        if state == "executing":
            exec_count += 1
            leaderboard[agent] += 1
            active_map[agent] = True
            if active_map and all(active_map.values()):
                all_active = 1
        elif state == "error":
            active_map[agent] = False
            if ts:
                last_error_at = ts
        else:
            active_map[agent] = False

    error_free_minutes = 0
    try:
        now = datetime.now()
        if last_error_at:
            dt = datetime.fromisoformat(str(last_error_at).replace("Z", "+00:00"))
            if dt.tzinfo:
                from datetime import timezone
                error_free_minutes = int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() // 60)
            else:
                error_free_minutes = int((now - dt).total_seconds() // 60)
        else:
            start = datetime.strptime(today, "%Y-%m-%d")
            error_free_minutes = int((now - start).total_seconds() // 60)
        error_free_minutes = max(0, error_free_minutes)
    except Exception:
        error_free_minutes = 0

    return {
        "executing_count": exec_count,
        "error_free_minutes": error_free_minutes,
        "all_active": all_active,
        "leaderboard": leaderboard,
    }


def evaluate_missions(today: str):
    metrics = compute_today_metrics(today)
    state = load_missions_state()
    date_state = state.setdefault("dates", {}).setdefault(today, {"completed": []})
    completed = set(date_state.get("completed", []))
    missions_payload = []

    for m in MISSIONS:
        progress = int(metrics.get(m["type"], 0) or 0)
        target = int(m.get("target", 1) or 1)
        done = progress >= target
        if done and m["id"] not in completed:
            notify_telegram(f"✅ 일일 미션 달성: {m['title']}")
            completed.add(m["id"])
        missions_payload.append({
            **m,
            "progress": min(progress, target),
            "rawProgress": progress,
            "done": done,
        })

    date_state["completed"] = sorted(list(completed))
    save_missions_state(state)
    return missions_payload


def normalize_agent_state(s):
    """상태 정규화, 호환성 향상.
    호환 입력: working/busy → writing; run/running → executing; sync → syncing researching.
    미인식 시 기본값 idle 반환.
    """
    if not s:
        return 'idle'
    s_lower = s.lower().strip()
    if s_lower in {'working', 'busy', 'write'}:
        return 'writing'
    if s_lower in {'run', 'running', 'execute', 'exec'}:
        return 'executing'
    if s_lower in {'sync'}:
        return 'syncing'
    if s_lower in {'research', 'search'}:
        return 'researching'
    if s_lower in {'idle', 'writing', 'researching', 'executing', 'syncing', 'error'}:
        return s_lower
    # 기본 폴백
    return 'idle'


def state_to_area(state):
    area_map = {
        "idle": "breakroom",
        "writing": "writing",
        "researching": "writing",
        "executing": "writing",
        "syncing": "writing",
        "error": "error"
    }
    return area_map.get(state, "breakroom")


# Ensure files exist
if not os.path.exists(AGENTS_STATE_FILE):
    save_agents_state([ensure_agent_meta(dict(a)) for a in DEFAULT_AGENTS])
if not os.path.exists(JOIN_KEYS_FILE):
    save_join_keys({"keys": []})
if not os.path.exists(MISSIONS_STATE_FILE):
    save_missions_state({"dates": {}})
if not os.path.exists(SHOP_STATE_FILE):
    save_shop_state({"purchases": {}})

# Initial XP/level sync from history (graceful when history missing/empty)
try:
    refresh_agents_xp(AGENTS_STATE_FILE, HISTORY_FILE)
except Exception:
    pass


@app.route("/agents", methods=["GET"])
def get_agents():
    """Get full agents list (for multi-agent UI), with auto-cleanup on access"""
    agents = load_agents_state()
    now = datetime.now()

    cleaned_agents = []
    keys_data = load_join_keys()

    for a in agents:
        if a.get("isMain"):
            cleaned_agents.append(a)
            continue

        auth_expires_at_str = a.get("authExpiresAt")
        auth_status = a.get("authStatus", "pending")

        # 1) 승인 타임아웃 시 자동 퇴장
        if auth_status == "pending" and auth_expires_at_str:
            try:
                auth_expires_at = datetime.fromisoformat(auth_expires_at_str)
                if now > auth_expires_at:
                    key = a.get("joinKey")
                    if key:
                        key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == key), None)
                        if key_item:
                            key_item["used"] = False
                            key_item["usedBy"] = None
                            key_item["usedByAgentId"] = None
                            key_item["usedAt"] = None
                    continue
            except Exception:
                pass

        # 2) 5분 이상 push 없으면 자동 오프라인
        last_push_at_str = a.get("lastPushAt")
        if auth_status == "approved" and last_push_at_str:
            try:
                last_push_at = datetime.fromisoformat(last_push_at_str)
                age = (now - last_push_at).total_seconds()
                if age > 300:  # 5분 push 없으면 자동 오프라인
                    a["authStatus"] = "offline"
            except Exception:
                pass

        cleaned_agents.append(a)

    track_agent_offline_transition(cleaned_agents)
    save_agents_state(cleaned_agents)
    save_join_keys(keys_data)

    return jsonify(cleaned_agents)


@app.route("/agent-approve", methods=["POST"])
def agent_approve():
    """Approve an agent (set authStatus to approved)"""
    try:
        data = request.get_json()
        agent_id = (data.get("agentId") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "msg": "agentId가 필요합니다"}), 400

        agents = load_agents_state()
        target = next((a for a in agents if a.get("agentId") == agent_id and not a.get("isMain")), None)
        if not target:
            return jsonify({"ok": False, "msg": "에이전트를 찾을 수 없습니다"}), 404

        target["authStatus"] = "approved"
        target["authApprovedAt"] = datetime.now().isoformat()
        target["authExpiresAt"] = (datetime.now() + timedelta(hours=24)).isoformat()  # 기본 승인 24시간

        save_agents_state(agents)
        return jsonify({"ok": True, "agentId": agent_id, "authStatus": "approved"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/agent-reject", methods=["POST"])
def agent_reject():
    """Reject an agent (set authStatus to rejected and optionally revoke key)"""
    try:
        data = request.get_json()
        agent_id = (data.get("agentId") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "msg": "agentId가 필요합니다"}), 400

        agents = load_agents_state()
        target = next((a for a in agents if a.get("agentId") == agent_id and not a.get("isMain")), None)
        if not target:
            return jsonify({"ok": False, "msg": "에이전트를 찾을 수 없습니다"}), 404

        target["authStatus"] = "rejected"
        target["authRejectedAt"] = datetime.now().isoformat()

        # Optionally free join key back to unused
        join_key = target.get("joinKey")
        keys_data = load_join_keys()
        if join_key:
            key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == join_key), None)
            if key_item:
                key_item["used"] = False
                key_item["usedBy"] = None
                key_item["usedByAgentId"] = None
                key_item["usedAt"] = None

        # Remove from agents list
        agents = [a for a in agents if a.get("agentId") != agent_id or a.get("isMain")]

        save_agents_state(agents)
        save_join_keys(keys_data)
        return jsonify({"ok": True, "agentId": agent_id, "authStatus": "rejected"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/join-agent", methods=["POST"])
def join_agent():
    """Add a new agent with one-time join key validation and pending auth"""
    try:
        data = request.get_json()
        if not isinstance(data, dict) or not data.get("name"):
            return jsonify({"ok": False, "msg": "이름을 입력해주세요"}), 400

        name = data["name"].strip()
        state = data.get("state", "idle")
        detail = data.get("detail", "")
        join_key = data.get("joinKey", "").strip()

        # Normalize state early for compatibility
        state = normalize_agent_state(state)

        if not join_key:
            return jsonify({"ok": False, "msg": "접속 키를 입력해주세요"}), 400

        keys_data = load_join_keys()
        key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == join_key), None)
        if not key_item:
            return jsonify({"ok": False, "msg": "접속 키가 유효하지 않습니다"}), 403
        # key 재사용 가능: used=true로 거부하지 않음

        with join_lock:
            # 락 내 재읽기: 동시 요청이 동일 스냅샷 기반으로 검사 통과하는 것 방지
            keys_data = load_join_keys()
            key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == join_key), None)
            if not key_item:
                return jsonify({"ok": False, "msg": "접속 키가 유효하지 않습니다"}), 403

            agents = load_agents_state()

            # 동시 접속 상한: 같은 key 최대 3개 동시 온라인.
            # 온라인 판정: lastPushAt/updated_at이 5분 이내; 아니면 offline으로 간주.
            now = datetime.now()
            existing = next((a for a in agents if a.get("name") == name and not a.get("isMain")), None)
            existing_id = existing.get("agentId") if existing else None

            def _age_seconds(dt_str):
                if not dt_str:
                    return None
                try:
                    dt = datetime.fromisoformat(dt_str)
                    return (now - dt).total_seconds()
                except Exception:
                    return None

            # opportunistic offline marking
            for a in agents:
                if a.get("isMain"):
                    continue
                if a.get("authStatus") != "approved":
                    continue
                age = _age_seconds(a.get("lastPushAt"))
                if age is None:
                    age = _age_seconds(a.get("updated_at"))
                if age is not None and age > 300:
                    a["authStatus"] = "offline"

            max_concurrent = int(key_item.get("maxConcurrent", 3))
            active_count = 0
            for a in agents:
                if a.get("isMain"):
                    continue
                if a.get("agentId") == existing_id:
                    continue
                if a.get("joinKey") != join_key:
                    continue
                if a.get("authStatus") != "approved":
                    continue
                age = _age_seconds(a.get("lastPushAt"))
                if age is None:
                    age = _age_seconds(a.get("updated_at"))
                if age is None or age <= 300:
                    active_count += 1

            if active_count >= max_concurrent:
                save_agents_state(agents)
                return jsonify({"ok": False, "msg": f"해당 키의 동시 접속이 상한({max_concurrent})에 도달했습니다"}), 429

            if existing:
                existing["state"] = state
                existing["detail"] = detail
                existing["updated_at"] = datetime.now().isoformat()
                existing["area"] = state_to_area(state)
                existing["source"] = "remote-openclaw"
                existing["joinKey"] = join_key
                existing["authStatus"] = "approved"
                existing["authApprovedAt"] = datetime.now().isoformat()
                existing["authExpiresAt"] = (datetime.now() + timedelta(hours=24)).isoformat()
                existing["lastPushAt"] = datetime.now().isoformat()  # join을 온라인으로 간주, 동시성/오프라인 판정에 포함
                if not existing.get("avatar"):
                    import random
                    existing["avatar"] = random.choice(["guest_role_1", "guest_role_2", "guest_role_3", "guest_role_4", "guest_role_5", "guest_role_6"])
                agent_id = existing.get("agentId")
            else:
                # Use ms + random suffix to avoid collisions under concurrent joins
                import random
                import string
                agent_id = "agent_" + str(int(datetime.now().timestamp() * 1000)) + "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
                agents.append({
                    "agentId": agent_id,
                    "name": name,
                    "isMain": False,
                    "state": state,
                    "detail": detail,
                    "updated_at": datetime.now().isoformat(),
                    "area": state_to_area(state),
                    "source": "remote-openclaw",
                    "joinKey": join_key,
                    "authStatus": "approved",
                    "authApprovedAt": datetime.now().isoformat(),
                    "authExpiresAt": (datetime.now() + timedelta(hours=24)).isoformat(),
                    "lastPushAt": datetime.now().isoformat(),
                    "avatar": random.choice(["guest_role_1", "guest_role_2", "guest_role_3", "guest_role_4", "guest_role_5", "guest_role_6"])
                })

            key_item["used"] = True
            key_item["usedBy"] = name
            key_item["usedByAgentId"] = agent_id
            key_item["usedAt"] = datetime.now().isoformat()
            key_item["reusable"] = True

            # 유효한 key 획득 시 즉시 승인, 수동 클릭 불필요
            # (상태는 위 existing/new 분기에서 이미 기록됨)
            for a in agents:
                ensure_agent_meta(a)
            save_agents_state(agents)
            save_join_keys(keys_data)

        return jsonify({"ok": True, "agentId": agent_id, "authStatus": "approved", "nextStep": "자동 승인 완료, 즉시 상태 push 시작"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/leave-agent", methods=["POST"])
def leave_agent():
    """Remove an agent and free its one-time join key for reuse (optional)

    Prefer agentId (stable). Name is accepted for backward compatibility.
    """
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"ok": False, "msg": "invalid json"}), 400

        agent_id = (data.get("agentId") or "").strip()
        name = (data.get("name") or "").strip()
        if not agent_id and not name:
            return jsonify({"ok": False, "msg": "agentId 또는 이름을 입력해주세요"}), 400

        agents = load_agents_state()

        target = None
        if agent_id:
            target = next((a for a in agents if a.get("agentId") == agent_id and not a.get("isMain")), None)
        if (not target) and name:
            # fallback: remove by name only if agentId not provided
            target = next((a for a in agents if a.get("name") == name and not a.get("isMain")), None)

        if not target:
            return jsonify({"ok": False, "msg": "퇴장할 에이전트를 찾을 수 없습니다"}), 404

        join_key = target.get("joinKey")
        new_agents = [a for a in agents if a.get("isMain") or a.get("agentId") != target.get("agentId")]

        # Optional: free key back to unused after leave
        keys_data = load_join_keys()
        if join_key:
            key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == join_key), None)
            if key_item:
                key_item["used"] = False
                key_item["usedBy"] = None
                key_item["usedByAgentId"] = None
                key_item["usedAt"] = None

        save_agents_state(new_agents)
        save_join_keys(keys_data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/status", methods=["GET"])
def get_status():
    """Get current main state (backward compatibility)"""
    state = load_state()
    return jsonify(state)


@app.route("/agent-push", methods=["POST"])
def agent_push():
    """Remote openclaw actively pushes status to office.

    Required fields:
    - agentId
    - joinKey
    - state
    Optional:
    - detail
    - name
    """
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"ok": False, "msg": "invalid json"}), 400

        agent_id = (data.get("agentId") or "").strip()
        join_key = (data.get("joinKey") or "").strip()
        state = (data.get("state") or "").strip()
        detail = (data.get("detail") or "").strip()
        name = (data.get("name") or "").strip()

        if not agent_id or not join_key or not state:
            return jsonify({"ok": False, "msg": "agentId/joinKey/state가 필요합니다"}), 400

        valid_states = {"idle", "writing", "researching", "executing", "syncing", "error"}
        state = normalize_agent_state(state)

        keys_data = load_join_keys()
        key_item = next((k for k in keys_data.get("keys", []) if k.get("key") == join_key), None)
        if not key_item:
            return jsonify({"ok": False, "msg": "joinKey가 유효하지 않습니다"}), 403
        # key 재사용 가능: used/usedByAgentId 바인딩 검사 생략


        agents = load_agents_state()
        target = next((a for a in agents if a.get("agentId") == agent_id and not a.get("isMain")), None)
        if not target:
            return jsonify({"ok": False, "msg": "에이전트 미등록, join 먼저 하세요"}), 404

        # Auth check: only approved agents can push.
        # Note: "offline" is a presence state (stale), not a revoked authorization.
        # Allow offline agents to resume pushing and auto-promote them back to approved.
        auth_status = target.get("authStatus", "pending")
        if auth_status not in {"approved", "offline"}:
            return jsonify({"ok": False, "msg": "에이전트 미승인, 승인 대기 중"}), 403
        if auth_status == "offline":
            target["authStatus"] = "approved"
            target["authApprovedAt"] = datetime.now().isoformat()
            target["authExpiresAt"] = (datetime.now() + timedelta(hours=24)).isoformat()

        if target.get("joinKey") != join_key:
            return jsonify({"ok": False, "msg": "joinKey가 일치하지 않습니다"}), 403

        ensure_agent_meta(target)
        prev_state = (target.get("state") or "idle").strip().lower()

        # Combo / streak
        if state == "executing":
            target["combo"] = int(target.get("combo", 0) or 0) + 1
            target["lastComboAt"] = datetime.now().isoformat()
            target["maxCombo"] = max(int(target.get("maxCombo", 0) or 0), target["combo"])
            if target["combo"] > 0 and target["combo"] % 5 == 0:
                notify_combo(target.get("name", agent_id), target["combo"])
        elif state == "error":
            target["combo"] = 0

        # XP/points on completion transitions
        xp_gain = 0
        if prev_state == "executing" and state == "idle":
            xp_gain = 10
            target["points"] = int(target.get("points", 0) or 0) + 5
        elif prev_state == "error" and state == "idle":
            xp_gain = 25
            target["points"] = int(target.get("points", 0) or 0) + 5

        old_level = int(target.get("level", 1) or 1)
        if xp_gain > 0:
            target["xp"] = int(target.get("xp", 0) or 0) + xp_gain

        new_level, new_title = calc_level(int(target.get("xp", 0) or 0))
        target["level"] = new_level
        target["title"] = new_title
        if new_level > old_level:
            notify_level_up(target.get("name", agent_id), new_level, new_title)

        target["state"] = state
        target["detail"] = detail
        if name:
            target["name"] = name
        target["updated_at"] = datetime.now().isoformat()
        target["area"] = state_to_area(state)
        target["source"] = "remote-openclaw"
        target["lastPushAt"] = datetime.now().isoformat()

        for a in agents:
            ensure_agent_meta(a)

        save_agents_state(agents)
        append_history(target.get("name", agent_id), state, detail)

        # Recompute XP/level from history and broadcast level-up events
        _, level_up_events = refresh_agents_xp(AGENTS_STATE_FILE, HISTORY_FILE)
        for ev in level_up_events:
            socketio.emit("level_up", ev)

        broadcast_state_update()
        if state == "error":
            notify_telegram_error(target.get("name", agent_id), detail)
        return jsonify({
            "ok": True,
            "agentId": agent_id,
            "area": target.get("area"),
            "xp": target.get("xp", 0),
            "level": target.get("level", 1),
            "title": target.get("title", LEVEL_TABLE[0][1]),
            "points": target.get("points", 0),
            "combo": target.get("combo", 0)
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/missions", methods=["GET"])
def missions():
    payload = evaluate_daily_missions(MISSIONS_STATE_FILE, HISTORY_FILE)
    return jsonify(payload)


@app.route("/missions/check", methods=["POST"])
def missions_check():
    payload = evaluate_daily_missions(MISSIONS_STATE_FILE, HISTORY_FILE)
    return jsonify({"ok": True, **payload})


@app.route("/leaderboard", methods=["GET"])
def leaderboard():
    today = datetime.now().strftime("%Y-%m-%d")
    metrics = compute_today_metrics(today)
    rows = sorted(metrics["leaderboard"].items(), key=lambda x: x[1], reverse=True)
    data = [{"rank": i + 1, "agent": name, "executingCount": count} for i, (name, count) in enumerate(rows)]
    return jsonify({"date": today, "leaderboard": data})


@app.route("/shop", methods=["GET"])
def shop():
    shop_state = load_shop_state()
    agents = load_agents_state()
    points = {a.get("name"): int(a.get("points", 0) or 0) for a in agents if not a.get("isMain")}
    return jsonify({"items": SHOP_ITEMS, "purchases": shop_state.get("purchases", {}), "points": points})


@app.route("/shop/buy", methods=["POST"])
def shop_buy():
    data = request.get_json() or {}
    agent_id = (data.get("agentId") or "").strip()
    item_id = (data.get("itemId") or "").strip()
    if not agent_id or not item_id:
        return jsonify({"ok": False, "msg": "agentId/itemId가 필요합니다"}), 400

    item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
    if not item:
        return jsonify({"ok": False, "msg": "존재하지 않는 상품입니다"}), 404

    agents = load_agents_state()
    target = next((a for a in agents if a.get("agentId") == agent_id and not a.get("isMain")), None)
    if not target:
        return jsonify({"ok": False, "msg": "에이전트를 찾을 수 없습니다"}), 404

    ensure_agent_meta(target)
    current_points = int(target.get("points", 0) or 0)
    if current_points < int(item["price"]):
        return jsonify({"ok": False, "msg": "포인트가 부족합니다", "points": current_points}), 400

    shop_state = load_shop_state()
    purchases = shop_state.setdefault("purchases", {})
    owned = purchases.setdefault(agent_id, [])
    if item_id in owned:
        return jsonify({"ok": False, "msg": "이미 구매한 아이템입니다"}), 400

    target["points"] = current_points - int(item["price"])
    owned.append(item_id)

    save_agents_state(agents)
    save_shop_state(shop_state)
    return jsonify({"ok": True, "agentId": agent_id, "itemId": item_id, "points": target["points"], "purchases": owned})


@app.route("/health", methods=["GET"])
def health():
    """Health check"""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/yesterday-memo", methods=["GET"])
def get_yesterday_memo():
    """어제 일기 가져오기"""
    try:
        # 먼저 어제 파일 찾기 시도
        yesterday_str = get_yesterday_date_str()
        yesterday_file = os.path.join(MEMORY_DIR, f"{yesterday_str}.md")
        
        target_file = None
        target_date = yesterday_str
        
        if os.path.exists(yesterday_file):
            target_file = yesterday_file
        else:
            # 어제 파일 없으면 가장 최근 날짜 파일 찾기
            if os.path.exists(MEMORY_DIR):
                files = [f for f in os.listdir(MEMORY_DIR) if f.endswith(".md") and re.match(r"\d{4}-\d{2}-\d{2}\.md", f)]
                if files:
                    files.sort(reverse=True)
                    # 오늘 파일은 건너뜀
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    for f in files:
                        if f != f"{today_str}.md":
                            target_file = os.path.join(MEMORY_DIR, f)
                            target_date = f.replace(".md", "")
                            break
        
        if target_file and os.path.exists(target_file):
            memo_content = extract_memo_from_file(target_file)
            return jsonify({
                "success": True,
                "date": target_date,
                "memo": memo_content
            })
        else:
            return jsonify({
                "success": False,
                "msg": "어제 일기를 찾을 수 없습니다"
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "msg": str(e)
        }), 500


@app.route("/set_state", methods=["POST"])
def set_state_endpoint():
    """Set state via POST (for UI control panel)"""
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"status": "error", "msg": "invalid json"}), 400
        state = load_state()
        if "state" in data:
            s = data["state"]
            valid_states = {"idle", "writing", "researching", "executing", "syncing", "error"}
            if s in valid_states:
                state["state"] = s
        if "detail" in data:
            state["detail"] = data["detail"]
        state["updated_at"] = datetime.now().isoformat()
        save_state(state)
        # agents-state.json의 main agent도 동기화
        agents = load_agents_state()
        for a in agents:
            if a.get("isMain"):
                a["state"] = state["state"]
                a["detail"] = state["detail"]
                a["updated_at"] = state["updated_at"]
                break
        save_agents_state(agents)
        append_history("왕천재", state.get("state", "idle"), state.get("detail", ""))
        broadcast_state_update()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@app.route("/history", methods=["GET"])
def get_history():
    """Get recent history rows from history.jsonl with optional agent/date filter."""
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 500))

    agent_filter = (request.args.get("agent", "") or "").strip()
    date_filter = (request.args.get("date", "") or "").strip()  # YYYY-MM-DD

    rows = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue

                    if agent_filter and row.get("agent") != agent_filter:
                        continue
                    if date_filter:
                        ts = row.get("ts", "")
                        if not str(ts).startswith(date_filter):
                            continue

                    rows.append(row)
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)}), 500

    return jsonify(rows[-limit:])


@app.route("/ping-agents", methods=["GET"])
def ping_agents():
    results = {}
    hosts = {
        "울트라맨": "192.168.0.3",
        "똘똘이": "192.168.0.109",
        "김양이": "192.168.0.130",
        "토르": "192.168.0.129",
        "카다스": "192.168.0.86"
    }
    for name, ip in hosts.items():
        start = datetime.now()
        try:
            result = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True)
            ms = round((datetime.now() - start).total_seconds() * 1000)
            results[name] = {"ip": ip, "alive": result.returncode == 0, "ms": ms}
        except Exception:
            results[name] = {"ip": ip, "alive": False, "ms": None}
    return jsonify(results)


@app.route("/daily-report", methods=["GET"])
def daily_report():
    today = datetime.now().strftime("%Y-%m-%d")
    stats = defaultdict(lambda: {"total": 0, "error": 0})

    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    ts = str(row.get("ts", ""))
                    if not ts.startswith(today):
                        continue
                    agent = row.get("agent", "unknown")
                    state = row.get("state", "idle")
                    stats[agent]["total"] += 1
                    if state == "error":
                        stats[agent]["error"] += 1
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)}), 500

    lines = ["📊 Star Office 일일 리포트", f"📅 {today}"]
    if not stats:
        lines.append("오늘 기록된 작업이 없습니다.")
    else:
        for agent, c in stats.items():
            if c["error"] > 0:
                lines.append(f"{agent}: 작업 {c['total']}회 / 에러 {c['error']}회")
            else:
                lines.append(f"{agent}: 작업 {c['total']}회")

    msg = "\n".join(lines)
    try:
        subprocess.Popen([
            "openclaw", "message", "send",
            "--channel", "telegram",
            "--message", msg
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sent = True
    except Exception:
        sent = False

    return jsonify({"ok": True, "date": today, "report": dict(stats), "message": msg, "sent": sent})


if __name__ == "__main__":
    print("=" * 50)
    print("Star Office UI - Backend State Service")
    print("=" * 50)
    print(f"State file: {STATE_FILE}")
    port = int(os.environ.get("PORT", 18795))
    print(f"Listening on: http://0.0.0.0:{port}")
    print("=" * 50)
    
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
