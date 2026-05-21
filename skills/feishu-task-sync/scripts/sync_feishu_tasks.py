#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from feishu_user_auth import FeishuUserAuth, FeishuUserAuthError
from runtime import (
    ConfigError,
    Settings,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)

TZ = ZoneInfo("Asia/Shanghai")

ACTION_PATTERNS = [
    r"\bTODO\b",
    r"待办",
    r"行动项",
    r"请你",
    r"麻烦你",
    r"帮我",
    r"跟进",
    r"落实",
    r"确认",
    r"提交",
    r"完成",
    r"修复",
    r"安排",
    r"整理",
    r"同步",
    r"回复",
    r"创建",
    r"推进",
    r"检查",
    r"处理",
    r"补充",
    r"发送",
    r"提醒",
]

STRONG_TASK_PATTERNS = [
    r"\bTODO\b",
    r"待办",
    r"行动项",
    r"请你",
    r"麻烦你",
    r"帮我",
]

DUE_PATTERNS = [
    r"今天",
    r"明天",
    r"后天",
    r"今晚",
    r"本周[一二三四五六日天]?",
    r"下周[一二三四五六日天]?",
    r"周[一二三四五六日天]",
    r"星期[一二三四五六日天]",
    r"\d{4}-\d{1,2}-\d{1,2}",
    r"\d{1,2}/\d{1,2}",
    r"\d{1,2}[:点时](\d{1,2})?",
    r"截止",
    r"deadline",
    r"due",
]

QUESTION_PATTERNS = [
    r"在哪里看",
    r"可以.*吗",
    r"能.*吗",
    r"有没有新结果",
    r"我没看到",
    r"看不到",
    r"为什么",
    r"怎么",
]

SKIP_STATUSES = {"baseline-seen", "created", "created+assigned"}
TASK_SCOPE_HINTS = ["task:task:read", "task:task:write"]
FEISHU_DOC_SCOPE_HINTS = [
    "docx:document:readonly",
    "wiki:wiki:readonly",
    "drive:drive:readonly",
    "search:docs:read",
]
FEISHU_IM_SCOPE_HINTS = [
    "im:message.history:readonly",
    "im:message:readonly",
    "im:message.p2p_msg:get_as_user",
    "im:message.group_msg:get_as_user",
    "im:message:read_as_user",
    "im:message:read",
    "im:message",
]
FEISHU_IM_CHAT_SCOPE_HINTS = [
    "im:chat:readonly",
    "im:chat:read",
    "im:chat.group_info:readonly",
    "im:chat",
]
FEISHU_DOC_ACTION_QUERIES = ["待办", "TODO", "行动项", "请你", "麻烦你", "帮我", "跟进", "确认", "处理"]


@dataclass
class SourceRef:
    source_type: str
    provider: str
    session_id: Optional[str]
    session_title: Optional[str]
    message_id: Optional[str]
    file_path: Optional[str]
    created_at: Optional[str]


@dataclass
class CandidateTask:
    fingerprint: str
    source: SourceRef
    title: str
    normalized_title: str
    description: str
    due_text: Optional[str]
    due_at: Optional[str]
    score: int
    reasons: List[str]
    raw_excerpt: str


@dataclass
class CreateResult:
    fingerprint: str
    status: str
    feishu_task_guid: Optional[str] = None
    feishu_task_id: Optional[str] = None
    error: Optional[str] = None
    response: Optional[Dict[str, Any]] = None


class FeishuApiError(RuntimeError):
    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload or {}


class JsonStore:
    @staticmethod
    def load(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def save(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Feishu chat/docs and create Feishu Tasks (legacy fallback).")
    # NOTE: --config is recognised but not yet honoured in 0.1.x; the runtime
    # wiring lands in step 2 of the 0.2.0 refactor.
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to feishu-task-sync config.json. Recognised in 0.1.x but the "
            "legacy --settings-path / --state-path / --report-* flags still take "
            "effect; full integration lands in 0.2.0."
        ),
    )
    parser.add_argument("--chat-root", default=None, help="Override chat root; defaults to settings.paths.chat_root.")
    parser.add_argument("--docs-root", default=None, help="Override docs root; defaults to settings.paths.docs_root.")
    parser.add_argument("--state-path", default=None, help="Override the legacy state.json path.")
    parser.add_argument("--report-json", default=None, help="Override the latest-report.json path.")
    parser.add_argument("--report-md", default=None, help="Override the latest-report.md path.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-score", type=int, default=3)
    parser.add_argument("--create", action="store_true", help="Actually create tasks in Feishu.")
    parser.add_argument("--baseline", action="store_true", help="Mark current candidates as baseline-seen so future runs only act on new items.")
    parser.add_argument("--only-check-auth", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--include-assistant-messages", action="store_true")
    parser.add_argument("--include-non-feishu-chats", action="store_true")
    parser.add_argument("--assignee-user-id", default="", help="Feishu user/open_id used in add_members API.")
    parser.add_argument("--feishu-doc-lookback-days", type=int, default=7, help="Lookback window for Feishu cloud document @mentions.")
    parser.add_argument("--disable-feishu-doc-mentions", action="store_true", help="Disable Feishu cloud document @mention scanning.")
    return parser.parse_args(argv)


def parse_metadata(metadata_json: Optional[str]) -> Dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        return json.loads(metadata_json)
    except Exception:
        return {}


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"[`*_#>~\-]+", " ", text)
    text = re.sub(r"[，。！？、；：,.!?;:()（）\[\]{}]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def load_sessions(chat_root: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in JsonStore.load(chat_root / "sessions.json", []):
        session_id = str(item.get("id") or "")
        if session_id:
            result[session_id] = item
    return result


def text_contains_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_probably_task(text: str, metadata: Dict[str, Any], source_type: str) -> Tuple[int, List[str]]:
    normalized = normalize_whitespace(text)
    score = 0
    reasons: List[str] = []

    mentioned = metadata.get("mentioned") is True
    if mentioned:
        score += 2
        reasons.append("metadata.mentioned=true")

    if text_contains_any(normalized, STRONG_TASK_PATTERNS):
        score += 2
        reasons.append("strong-task-pattern")
    elif text_contains_any(normalized, ACTION_PATTERNS):
        score += 1
        reasons.append("action-pattern")

    if text_contains_any(normalized, DUE_PATTERNS):
        score += 1
        reasons.append("due-pattern")

    if "@" in normalized:
        score += 1
        reasons.append("contains-@")

    if re.search(r"(^|\n)\s*[-*+]\s+", text) or re.search(r"\[[ xX]\]", text):
        score += 1
        reasons.append("checklist")

    if source_type == "chat_message":
        if text_contains_any(normalized, QUESTION_PATTERNS) and not text_contains_any(normalized, STRONG_TASK_PATTERNS):
            score -= 2
            reasons.append("question-penalty")
        if normalized.endswith("吗") or normalized.endswith("吗？") or normalized.endswith("?") or normalized.endswith("？"):
            score -= 1
            reasons.append("question-ending")

    # Gate to reduce false positives in direct-chat support conversations.
    has_action = text_contains_any(normalized, ACTION_PATTERNS)
    has_due = text_contains_any(normalized, DUE_PATTERNS)
    has_strong = text_contains_any(normalized, STRONG_TASK_PATTERNS)
    if source_type == "chat_message" and not (has_strong or (has_action and (mentioned or has_due or "@" in normalized))):
        score -= 2
        reasons.append("task-gate")

    if len(normalized) <= 8 and score < 4:
        score -= 2
        reasons.append("too-short")

    return score, reasons


def split_sentences(text: str) -> List[str]:
    return [normalize_whitespace(p) for p in re.split(r"[\n。！？!?；;]+", text) if normalize_whitespace(p)]


def extract_title(text: str) -> str:
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    for line in lines:
        line = re.sub(r"^[-*+\d.()（）\[\]xX\s]+", "", line).strip()
        line = re.sub(r"^@\S+\s*", "", line)
        if text_contains_any(line, ACTION_PATTERNS) or text_contains_any(line, STRONG_TASK_PATTERNS):
            return line[:80]
    sentences = split_sentences(text)
    return (sentences[0] if sentences else normalize_whitespace(text))[:80]


def parse_due_datetime(text: str, now: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
    now = now or datetime.now(TZ)
    text = normalize_whitespace(text)

    def pack(label: str, dt: datetime) -> Tuple[str, str]:
        return label, dt.isoformat()

    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s*(\d{1,2})[:点时](\d{1,2})?)?", text)
    if m:
        year, month, day = map(int, m.group(1, 2, 3))
        hour = int(m.group(4) or 18)
        minute = int(m.group(5) or 0)
        return pack(m.group(0), datetime(year, month, day, hour, minute, tzinfo=TZ))

    hour_match = re.search(r"(\d{1,2})[:点时](\d{1,2})?", text)
    hour = int(hour_match.group(1)) if hour_match else 18
    minute = int(hour_match.group(2) or 0) if hour_match else 0

    if "今天" in text or "今晚" in text:
        return pack("今天", now.replace(hour=hour, minute=minute, second=0, microsecond=0))
    if "明天" in text or "明早" in text:
        return pack("明天", (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0))
    if "后天" in text:
        return pack("后天", (now + timedelta(days=2)).replace(hour=hour, minute=minute, second=0, microsecond=0))

    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.search(r"(本周|下周)?[周星期]([一二三四五六日天])", text)
    if m:
        prefix, day_cn = m.groups()
        target = weekday_map[day_cn]
        delta = target - now.weekday()
        if prefix == "下周":
            delta = delta + 7 if delta > 0 else delta + 7
        elif delta < 0:
            delta += 7
        return pack(m.group(0), (now + timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0))

    return None, None


def build_description(text: str, source: SourceRef, reasons: Sequence[str], due_text: Optional[str]) -> str:
    return "\n".join(
        [
            "来源内容：",
            text.strip(),
            "",
            f"来源类型：{source.source_type}",
            f"来源提供方：{source.provider}",
            f"来源会话：{source.session_title or source.session_id or '-'}",
            f"来源消息ID：{source.message_id or '-'}",
            f"来源文件：{source.file_path or '-'}",
            f"来源时间：{source.created_at or '-'}",
            f"识别原因：{', '.join(reasons) if reasons else '-'}",
            f"识别到的截止时间：{due_text or '-'}",
        ]
    ).strip()


def make_fingerprint(source: SourceRef, normalized_title: str, due_at: Optional[str]) -> str:
    raw = "|".join(
        [
            source.provider,
            source.source_type,
            source.session_id or "",
            source.message_id or "",
            source.file_path or "",
            normalized_title,
            (due_at or "none")[:10],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_feishu_doc_fingerprint(doc_token: str, paragraph: str, assignee_user_id: str) -> str:
    raw = "|".join(["feishu_docs", "feishu_doc_mention", doc_token, normalize_whitespace(paragraph), assignee_user_id])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_feishu_doc_description(
    paragraph: str,
    source: SourceRef,
    reasons: Sequence[str],
    due_text: Optional[str],
    doc_title: str,
) -> str:
    return "\n".join(
        [
            "来源内容：",
            paragraph.strip(),
            "",
            f"飞书文档 / Wiki 链接：{source.file_path or '-'}",
            f"文档标题：{doc_title or '-'}",
            f"被 @ 段落原文：{paragraph.strip()}",
            "识别原因：云端文档块中包含当前 assignee open_id 的 @mention，且段落命中待办动作信号。",
            "",
            f"来源类型：{source.source_type}",
            f"来源提供方：{source.provider}",
            f"来源消息ID：{source.message_id or '-'}",
            f"来源时间：{source.created_at or '-'}",
            f"识别原因明细：{', '.join(reasons) if reasons else '-'}",
            f"识别到的截止时间：{due_text or '-'}",
        ]
    ).strip()


def feishu_api_missing_scopes(payload: Dict[str, Any], scope_hints: Sequence[str]) -> List[str]:
    haystack = json.dumps(payload, ensure_ascii=False)
    found = [scope for scope in scope_hints if scope in haystack]
    msg = str(payload.get("msg") or payload.get("message") or "")
    if not found and re.search(r"permission|scope|auth|权限|无权|forbidden", msg, re.IGNORECASE):
        return list(scope_hints)
    return found


def clean_feishu_token(token: str) -> str:
    token = str(token or "").strip()
    token = urllib.parse.unquote(token)
    token = token.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    token = re.split(r"[\s\\]+", token, maxsplit=1)[0]
    token = token.split("?", 1)[0].split("#", 1)[0]
    # Tokens often come from Markdown / JSON text and may carry trailing
    # punctuation, escapes, quotes, or code-fence backticks. Feishu document
    # tokens are alphanumeric, so strip anything outside that set at edges.
    token = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", token)
    if token in {"document_id", "doc_token", "token", "file_token", "obj_token"}:
        return ""
    return token


def clean_feishu_url(url: str) -> str:
    url = str(url or "").strip()
    url = url.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    url = re.split(r"\s+", url, maxsplit=1)[0]
    url = url.rstrip(".,，。；;:：!！?？)）]】}>")
    url = url.rstrip("`'\"\\")
    return url


def parse_feishu_url(url: str) -> Optional[Dict[str, str]]:
    cleaned_url = clean_feishu_url(url)
    parsed = urllib.parse.urlparse(cleaned_url)
    if "feishu.cn" not in parsed.netloc and "larksuite.com" not in parsed.netloc:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    if parts[0] in {"docx", "doc", "wiki"}:
        token = clean_feishu_token(parts[1])
        if not token:
            return None
        normalized_url = urllib.parse.urlunparse(parsed._replace(path="/" + "/".join([parts[0], token]), params="", query="", fragment=""))
        return {"type": parts[0], "token": token, "url": normalized_url}
    return None


def iter_feishu_urls_from_local_sources(chat_root: Path, docs_root: Path) -> Iterable[str]:
    url_re = re.compile(r"https://[^\s)>\]\"']*feishu\.cn/[^\s)>\]\"']+")
    paths: List[Path] = []
    messages_dir = chat_root / "messages"
    if messages_dir.exists():
        paths.extend(sorted(messages_dir.glob("*.json")))
    if docs_root.exists():
        paths.extend(sorted({*docs_root.rglob("*.md"), *docs_root.rglob("*.txt"), *docs_root.rglob("*.markdown")}))
    seen: set[str] = set()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in url_re.finditer(text):
            url = clean_feishu_url(match.group(0))
            if url not in seen:
                seen.add(url)
                yield url


def iter_chat_candidates(
    chat_root: Path,
    sessions: Dict[str, Dict[str, Any]],
    min_score: int,
    include_assistant_messages: bool,
    include_non_feishu_chats: bool,
) -> Iterable[CandidateTask]:
    messages_dir = chat_root / "messages"
    if not messages_dir.exists():
        return
    for path in sorted(messages_dir.glob("*.json")):
        try:
            items = JsonStore.load(path, [])
        except Exception:
            continue
        session_id = path.stem
        session = sessions.get(session_id, {})
        session_title = str(session.get("title") or session_id)
        session_meta = parse_metadata(session.get("metadataJson"))
        session_provider = str(session_meta.get("provider") or "")
        for item in items:
            role = str(item.get("role") or "")
            if role == "assistant" and not include_assistant_messages:
                continue
            if role not in {"user", "assistant"}:
                continue
            text = str(item.get("content") or "").strip()
            if not text:
                continue
            metadata = parse_metadata(item.get("metadataJson"))
            provider = str(metadata.get("provider") or session_provider or "local_chat")
            if not include_non_feishu_chats and provider != "feishu":
                continue
            score, reasons = is_probably_task(text, metadata, "chat_message")
            if score < min_score:
                continue
            title = extract_title(text)
            normalized_title = normalize_title(title)
            due_text, due_at = parse_due_datetime(text)
            source = SourceRef(
                source_type="chat_message",
                provider=provider,
                session_id=session_id,
                session_title=session_title,
                message_id=str(item.get("id") or ""),
                file_path=str(path),
                created_at=item.get("createdAt"),
            )
            yield CandidateTask(
                fingerprint=make_fingerprint(source, normalized_title, due_at),
                source=source,
                title=title,
                normalized_title=normalized_title,
                description=build_description(text, source, reasons, due_text),
                due_text=due_text,
                due_at=due_at,
                score=score,
                reasons=reasons,
                raw_excerpt=text[:240],
            )


def iter_doc_candidates(docs_root: Path, min_score: int) -> Iterable[CandidateTask]:
    if not docs_root.exists():
        return
    for path in sorted({*docs_root.rglob("*.md"), *docs_root.rglob("*.txt"), *docs_root.rglob("*.markdown")}):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for idx, sentence in enumerate(split_sentences(text), start=1):
            score, reasons = is_probably_task(sentence, {}, "document_line")
            if score < min_score:
                continue
            title = extract_title(sentence)
            normalized_title = normalize_title(title)
            due_text, due_at = parse_due_datetime(sentence)
            source = SourceRef(
                source_type="document_line",
                provider="local_docs",
                session_id=None,
                session_title=None,
                message_id=f"line-{idx}",
                file_path=str(path),
                created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=TZ).isoformat(),
            )
            yield CandidateTask(
                fingerprint=make_fingerprint(source, normalized_title, due_at),
                source=source,
                title=title,
                normalized_title=normalized_title,
                description=build_description(sentence, source, reasons, due_text),
                due_text=due_text,
                due_at=due_at,
                score=score,
                reasons=reasons,
                raw_excerpt=sentence[:240],
            )


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _block_mentions_user(block: Dict[str, Any], assignee_user_id: str) -> bool:
    for item in _walk_json(block):
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"user_id", "open_id", "id"} and str(value) == assignee_user_id:
                    return True
        elif isinstance(item, str) and item == assignee_user_id:
            return True
    return False


def _text_from_feishu_block(block: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in _walk_json(block):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("content"), str):
            parts.append(item["content"])
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        mention = item.get("mention_user") or item.get("mention")
        if isinstance(mention, dict):
            name = mention.get("name") or mention.get("en_name") or mention.get("user_name") or mention.get("id") or mention.get("user_id")
            if name:
                parts.append(f"@{name}")
    return normalize_whitespace(" ".join(parts))


def _extract_items_from_api_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    for key in ("items", "files", "docs", "nodes"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    value = data.get("docs_entities")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(data.get("result"), dict):
        result = data["result"]
        for key in ("items", "docs"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_docs_from_search_item(item: Dict[str, Any]) -> List[Dict[str, str]]:
    docs: List[Dict[str, str]] = []
    for value in _walk_json(item):
        if not isinstance(value, dict):
            continue
        url = str(value.get("url") or value.get("link") or "")
        parsed = parse_feishu_url(url) if url else None
        token = clean_feishu_token(str(value.get("token") or value.get("doc_token") or value.get("document_id") or value.get("file_token") or value.get("obj_token") or ""))
        doc_type = str(value.get("type") or value.get("docs_type") or value.get("obj_type") or value.get("file_type") or "")
        if parsed:
            docs.append({"type": parsed["type"], "token": parsed["token"], "url": parsed["url"]})
        elif token and doc_type in {"docx", "doc", "wiki"}:
            docs.append({"type": doc_type, "token": token, "url": ""})
    return docs


def _created_at_from_doc_item(item: Dict[str, Any]) -> Optional[str]:
    for key in ("updated_time", "edit_time", "modified_time", "update_time", "created_time", "create_time"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)) or str(value).isdigit():
            timestamp = int(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            return datetime.fromtimestamp(timestamp, tz=TZ).isoformat()
        return str(value)
    return None


def _extract_doc_title(payload: Dict[str, Any], fallback: str) -> str:
    for item in _walk_json(payload):
        if isinstance(item, dict):
            title = item.get("title") or item.get("name")
            if isinstance(title, str) and title.strip():
                return normalize_whitespace(title)
    return fallback


def _resolve_feishu_doc(client: FeishuClient, doc: Dict[str, str]) -> Dict[str, str]:
    doc_type = doc.get("type") or ""
    token = clean_feishu_token(doc.get("token") or "")
    if doc_type == "wiki":
        data = client.get_wiki_node(token)
        node = (data.get("data") or {}).get("node") or data.get("data") or {}
        if isinstance(node, dict):
            obj_token = str(node.get("obj_token") or node.get("document_id") or "")
            obj_type = str(node.get("obj_type") or node.get("origin_node_type") or "docx")
            if obj_token:
                resolved = dict(doc)
                resolved["type"] = "docx" if obj_type in {"docx", "doc"} else obj_type
                resolved["document_id"] = obj_token
                resolved["title"] = str(node.get("title") or "")
                return resolved
    resolved = dict(doc)
    resolved["document_id"] = token
    return resolved


def _collect_feishu_doc_seeds(
    client: FeishuClient,
    assignee_user_id: str,
    chat_root: Path,
    docs_root: Path,
    diagnostics: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    seeds: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    def add(doc: Dict[str, str]) -> None:
        token = clean_feishu_token(doc.get("token") or doc.get("document_id") or "")
        doc_type = doc.get("type") or ""
        if not token or not doc_type:
            return
        key = (doc_type, token)
        if key in seen:
            return
        seen.add(key)
        seeds.append(doc)

    for url in iter_feishu_urls_from_local_sources(chat_root, docs_root):
        parsed = parse_feishu_url(url)
        if parsed:
            add(parsed)

    try:
        data = client.list_recent_drive_files()
        for item in _extract_items_from_api_payload(data):
            for doc in _candidate_docs_from_search_item(item):
                doc["created_at"] = _created_at_from_doc_item(item) or ""
                add(doc)
        diagnostics.append({"source": "drive.v1.files", "ok": True, "count": len(_extract_items_from_api_payload(data))})
    except FeishuApiError as exc:
        diagnostics.append({
            "source": "drive.v1.files",
            "ok": False,
            "error": str(exc),
            "response": exc.payload,
            "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
        })

    for query in [assignee_user_id, *FEISHU_DOC_ACTION_QUERIES]:
        try:
            data = client.search_docs(query)
            items = _extract_items_from_api_payload(data)
            for item in items:
                for doc in _candidate_docs_from_search_item(item):
                    doc["created_at"] = _created_at_from_doc_item(item) or ""
                    add(doc)
            diagnostics.append({"source": "suite.docs-api.search.object", "query": query, "ok": True, "count": len(items)})
        except FeishuApiError as exc:
            diagnostics.append({
                "source": "suite.docs-api.search.object",
                "query": query,
                "ok": False,
                "error": str(exc),
                "response": exc.payload,
                "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
            })
            break
    return seeds


def iter_feishu_doc_mention_candidates(
    client: FeishuClient,
    assignee_user_id: Optional[str],
    lookback_days: int,
    min_score: int,
    chat_root: Path,
    docs_root: Path,
    diagnostics: List[Dict[str, Any]],
) -> Iterable[CandidateTask]:
    if not assignee_user_id:
        diagnostics.append({"source": "feishu_doc_mentions", "ok": False, "error": "missing assignee_user_id"})
        return

    cutoff = datetime.now(TZ) - timedelta(days=max(1, lookback_days))
    for seed in _collect_feishu_doc_seeds(client, assignee_user_id, chat_root, docs_root, diagnostics):
        try:
            resolved = _resolve_feishu_doc(client, seed)
            document_id = resolved.get("document_id") or resolved.get("token") or ""
            if not document_id or resolved.get("type") not in {"docx", "doc"}:
                continue

            meta_payload: Dict[str, Any] = {}
            try:
                meta_payload = client.get_docx_metadata(document_id)
            except FeishuApiError as exc:
                diagnostics.append({
                    "source": "docx.v1.documents.get",
                    "doc_token": document_id,
                    "ok": False,
                    "error": str(exc),
                    "response": exc.payload,
                    "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
                })
            doc_title = resolved.get("title") or _extract_doc_title(meta_payload, document_id)
            doc_url = resolved.get("url") or seed.get("url") or f"https://bytedance.feishu.cn/docx/{document_id}"
            doc_updated_at = resolved.get("created_at") or _created_at_from_doc_item(meta_payload.get("data") or {}) or datetime.now(TZ).isoformat()
            try:
                doc_dt = datetime.fromisoformat(str(doc_updated_at).replace("Z", "+00:00")).astimezone(TZ)
                if doc_dt < cutoff:
                    continue
            except Exception:
                pass

            blocks_payload = client.get_docx_blocks(document_id)
            blocks = _extract_items_from_api_payload(blocks_payload)
            diagnostics.append({"source": "docx.v1.documents.blocks", "doc_token": document_id, "ok": True, "count": len(blocks)})
            for idx, block in enumerate(blocks, start=1):
                if not _block_mentions_user(block, assignee_user_id):
                    continue
                paragraph = _text_from_feishu_block(block)
                if not paragraph:
                    continue
                score, reasons = is_probably_task(paragraph, {"mentioned": True}, "feishu_doc_mention")
                reasons.append("feishu-doc-mentions-current-user")
                if score < min_score:
                    continue
                title = extract_title(paragraph)
                normalized_title = normalize_title(title)
                due_text, due_at = parse_due_datetime(paragraph)
                source = SourceRef(
                    source_type="feishu_doc_mention",
                    provider="feishu_docs",
                    session_id=document_id,
                    session_title=doc_title,
                    message_id=str(block.get("block_id") or block.get("id") or f"block-{idx}"),
                    file_path=doc_url,
                    created_at=doc_updated_at,
                )
                yield CandidateTask(
                    fingerprint=make_feishu_doc_fingerprint(document_id, paragraph, assignee_user_id),
                    source=source,
                    title=title,
                    normalized_title=normalized_title,
                    description=build_feishu_doc_description(paragraph, source, reasons, due_text, doc_title),
                    due_text=due_text,
                    due_at=due_at,
                    score=score,
                    reasons=reasons,
                    raw_excerpt=paragraph[:240],
                )
        except FeishuApiError as exc:
            diagnostics.append({
                "source": "feishu_doc_mentions",
                "doc_token": seed.get("token"),
                "ok": False,
                "error": str(exc),
                "response": exc.payload,
                "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
            })


def deduplicate_candidates(candidates: Iterable[CandidateTask], state: Dict[str, Any], limit: int) -> Tuple[List[CandidateTask], List[Dict[str, Any]]]:
    accepted: List[CandidateTask] = []
    skipped: List[Dict[str, Any]] = []
    processed = state.get("processed", {})
    seen_weak: Dict[Tuple[str, str], str] = {}

    for task in sorted(candidates, key=lambda item: (item.source.created_at or "", -item.score)):
        existing = processed.get(task.fingerprint)
        if isinstance(existing, dict) and existing.get("status") in SKIP_STATUSES:
            skipped.append({
                "fingerprint": task.fingerprint,
                "reason": f"already-{existing.get('status')}",
                "title": task.title,
                "source": asdict(task.source),
            })
            continue
        weak_key = (
            task.source.session_id or task.source.file_path or "",
            f"{(task.due_at or 'none')[:10]}|{task.normalized_title}",
        )
        if weak_key in seen_weak:
            skipped.append({
                "fingerprint": task.fingerprint,
                "reason": f"weak-duplicate-of:{seen_weak[weak_key]}",
                "title": task.title,
                "source": asdict(task.source),
            })
            continue
        seen_weak[weak_key] = task.fingerprint
        accepted.append(task)
        if len(accepted) >= limit:
            break
    return accepted, skipped


class FeishuClient:
    def __init__(
        self,
        settings: Settings,
        auth_mode: str = "tenant",
        user_auth_path: Optional[Path] = None,
    ):
        if auth_mode not in {"tenant", "user"}:
            raise RuntimeError(f"Invalid Feishu auth_mode: {auth_mode}")
        self.settings = settings
        self.app_id = settings.feishu.app_id
        self.app_secret = settings.feishu.app_secret
        self.user_ids = (
            [settings.feishu.default_assignee_open_id]
            if settings.feishu.default_assignee_open_id
            else []
        )
        self.auth_mode = auth_mode
        self.user_auth_path = Path(user_auth_path or settings.paths.user_auth_path)
        self._user_auth: Optional[FeishuUserAuth] = None
        self._tenant_access_token: Optional[str] = None
        if not self.app_id or not self.app_secret:
            raise RuntimeError(
                "feishu.app_id / feishu.app_secret missing from skill config."
            )

    @staticmethod
    def _http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req_headers = dict(headers or {})
        if payload is not None:
            req_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload_json = json.loads(body)
            except Exception:
                payload_json = {"raw": body}
            raise FeishuApiError(f"HTTP {exc.code} calling {url}", payload_json) from exc
        except urllib.error.URLError as exc:
            raise FeishuApiError(f"Network error calling {url}: {exc.reason}", {"raw": str(exc.reason)}) from exc
        except http.client.RemoteDisconnected as exc:
            raise FeishuApiError(f"Network error calling {url}: remote disconnected", {"raw": str(exc)}) from exc

    def tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        data = self._http_json(
            "POST",
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            payload={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = data.get("tenant_access_token")
        if data.get("code") != 0 or not token:
            raise FeishuApiError("Failed to get tenant_access_token", data)
        self._tenant_access_token = str(token)
        return self._tenant_access_token

    @property
    def user_auth(self) -> FeishuUserAuth:
        if self._user_auth is None:
            self._user_auth = FeishuUserAuth(
                self.settings,
                state_path=self.user_auth_path,
            )
        return self._user_auth

    def user_access_token(self) -> str:
        try:
            return self.user_auth.ensure_access_token()
        except FeishuUserAuthError as exc:
            raise FeishuApiError(str(exc), exc.payload) from exc

    def user_auth_status(self) -> Dict[str, Any]:
        return self.user_auth.status()

    def auth_headers(self) -> Dict[str, str]:
        if self.auth_mode == "user":
            return {"Authorization": f"Bearer {self.user_access_token()}"}
        return {"Authorization": f"Bearer {self.tenant_access_token()}"}

    def check_task_api(self) -> Dict[str, Any]:
        try:
            data = self._http_json(
                "GET",
                "https://open.feishu.cn/open-apis/task/v2/tasks?page_size=1",
                headers=self.auth_headers(),
            )
            return {"ok": True, "response": data}
        except FeishuApiError as exc:
            payload = exc.payload
            msg = str(payload.get("msg") or "")
            return {
                "ok": False,
                "error": str(exc),
                "response": payload,
                "missing_scopes": [scope for scope in TASK_SCOPE_HINTS if scope in msg],
            }

    def check_task_write_api(self) -> Dict[str, Any]:
        """Probe the task-write scope without actually creating a task.

        Strategy: issue a PATCH against a deliberately invalid task GUID
        and a known-invalid ``update_fields``. The feishu open platform
        validates the bearer token's scope *before* the route validates
        the GUID, so the response disambiguates between
        ``task:task:write`` / ``task:task:writeonly`` being missing (the
        scope-error code, typically ``99991679`` with explicit
        ``permission_violations``) and any subsequent business error
        (task not found, missing field, etc.). Anything that is not a
        scope rejection counts as \"write scope is granted\".
        """

        write_scopes = ("task:task:write", "task:task:writeonly")
        guid = "00000000-0000-0000-0000-000000000000"
        try:
            data = self._http_json(
                "PATCH",
                f"https://open.feishu.cn/open-apis/task/v2/tasks/{guid}",
                payload={"task": {"summary": "feishu-task-sync write probe"}, "update_fields": ["summary"]},
                headers=self.auth_headers(),
            )
            code = data.get("code") if isinstance(data, dict) else None
            return {
                "ok": True,
                "probe": "patch-invalid-guid",
                "response_code": code,
                "missing_scopes": [],
            }
        except FeishuApiError as exc:
            payload = exc.payload if isinstance(exc.payload, dict) else {}
            code = payload.get("code")
            msg = str(payload.get("msg") or "")
            scope_error = bool(feishu_api_missing_scopes(payload, write_scopes)) or code == 99991679
            if scope_error:
                return {
                    "ok": False,
                    "probe": "patch-invalid-guid",
                    "error": str(exc),
                    "response": payload,
                    "missing_scopes": list(write_scopes),
                }
            # Any non-scope error (route 404, GUID format error, etc.) means
            # the bearer is *allowed* to call write APIs and the failure is
            # purely on the made-up GUID we passed in. That is what we want
            # the probe to confirm.
            return {
                "ok": True,
                "probe": "patch-invalid-guid",
                "response_code": code,
                "response_msg": msg,
                "missing_scopes": [],
            }

    def check_doc_api(self) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        for name, call in (
            ("drive.v1.files", lambda: self.list_recent_drive_files(page_size=1)),
            ("suite.docs-api.search.object", lambda: self.search_docs("待办", count=1)),
        ):
            try:
                data = call()
                checks.append({"source": name, "ok": True, "response": data})
            except FeishuApiError as exc:
                checks.append({
                    "source": name,
                    "ok": False,
                    "error": str(exc),
                    "response": exc.payload,
                    "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
                })
        missing: List[str] = []
        for item in checks:
            for scope in item.get("missing_scopes") or []:
                if scope not in missing:
                    missing.append(scope)
        return {"ok": any(item.get("ok") for item in checks), "checks": checks, "missing_scopes": missing}

    def check_im_message_api(self, chat_id: Optional[str] = None) -> Dict[str, Any]:
        if not chat_id:
            return {
                "ok": False,
                "skipped": True,
                "error": "missing chat_id",
                "missing_scopes": [],
            }
        now = datetime.now(TZ)
        try:
            data = self.list_im_messages(
                chat_id=chat_id,
                start_time=int((now - timedelta(minutes=5)).timestamp()),
                end_time=int(now.timestamp()),
                page_size=1,
            )
            return {"ok": True, "chat_id": chat_id, "response": data}
        except FeishuApiError as exc:
            return {
                "ok": False,
                "chat_id": chat_id,
                "error": str(exc),
                "response": exc.payload,
                "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_IM_SCOPE_HINTS),
            }

    def create_task(self, summary: str, description: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = description[:3000]
        return self._http_json(
            "POST",
            "https://open.feishu.cn/open-apis/task/v2/tasks",
            payload=payload,
            headers=self.auth_headers(),
        )

    def update_task(self, task_guid: str, **fields: Any) -> Dict[str, Any]:
        payload = {key: value for key, value in fields.items() if value is not None}
        if "description" in payload and isinstance(payload["description"], str):
            payload["description"] = payload["description"][:3000]
        update_fields = [key for key in payload.keys() if key != "update_fields"]
        wrapped_payload = {"task": payload, "update_fields": update_fields}
        return self._http_json(
            "PATCH",
            f"https://open.feishu.cn/open-apis/task/v2/tasks/{task_guid}",
            payload=wrapped_payload,
            headers=self.auth_headers(),
        )

    def add_assignee(self, task_guid: str, user_id: str) -> Dict[str, Any]:
        return self._http_json(
            "POST",
            f"https://open.feishu.cn/open-apis/task/v2/tasks/{task_guid}/add_members",
            payload={"members": [{"id": user_id, "type": "user", "role": "assignee"}]},
            headers=self.auth_headers(),
        )

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"https://open.feishu.cn/open-apis{path}"
        if query:
            url = f"{url}?{query}"
        return self._http_json("GET", url, headers=self.auth_headers())

    def post_json(self, path: str, payload: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"https://open.feishu.cn/open-apis{path}"
        if query:
            url = f"{url}?{query}"
        return self._http_json("POST", url, payload=payload, headers=self.auth_headers())

    def search_docs(self, query: str, count: int = 20, offset: int = 0) -> Dict[str, Any]:
        return self.post_json(
            "/suite/docs-api/search/object",
            {
                "search_key": query,
                "docs_types": ["doc", "docx", "wiki"],
                "count": count,
                "offset": offset,
            }
        )

    def list_recent_drive_files(self, page_size: int = 50) -> Dict[str, Any]:
        return self.get_json(
            "/drive/v1/files",
            params={"page_size": page_size, "order_by": "EditedTime", "direction": "DESC", "user_id_type": "open_id"},
        )

    def get_wiki_node(self, node_token: str) -> Dict[str, Any]:
        return self.get_json("/wiki/v2/spaces/get_node", params={"token": node_token})

    def get_docx_metadata(self, document_id: str) -> Dict[str, Any]:
        return self.get_json(f"/docx/v1/documents/{urllib.parse.quote(document_id)}")

    def get_docx_raw_content(self, document_id: str) -> Dict[str, Any]:
        return self.get_json(f"/docx/v1/documents/{urllib.parse.quote(document_id)}/raw_content")

    def get_docx_blocks(self, document_id: str) -> Dict[str, Any]:
        return self.get_json(
            f"/docx/v1/documents/{urllib.parse.quote(document_id)}/blocks",
            params={"page_size": 500, "document_revision_id": -1, "user_id_type": "open_id"},
        )

    def list_im_chats(self, page_size: int = 50, page_token: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "page_size": page_size,
            "page_token": page_token,
            "user_id_type": "open_id",
        }
        data = self.get_json("/im/v1/chats", params=params)
        if data.get("code") not in (None, 0):
            raise FeishuApiError("Failed to list Feishu IM chats", data)
        return data

    def list_im_messages(
        self,
        chat_id: str,
        start_time: int,
        end_time: int,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "container_id_type": "chat",
            "container_id": chat_id,
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
            "page_token": page_token,
            "user_id_type": "open_id",
        }
        data = self.get_json("/im/v1/messages", params=params)
        if data.get("code") not in (None, 0):
            raise FeishuApiError("Failed to list Feishu IM messages", data)
        return data


def discover_assignee_user_id(
    explicit: str,
    settings: Any,
    sessions: Dict[str, Dict[str, Any]],
    chat_root: Path,
) -> Optional[str]:
    if explicit:
        return explicit
    # Support both the new ``Settings`` dataclass and legacy raw dicts.
    if isinstance(settings, Settings):
        configured = settings.feishu.default_assignee_open_id
        if configured:
            return configured
    else:
        try:
            ids = settings["chatChannels"]["feishu"].get("userIds") or []  # type: ignore[index]
            if ids and isinstance(ids[0], str) and ids[0].strip():
                return ids[0].strip()
        except Exception:
            pass

    session_items = sorted(sessions.values(), key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    for item in session_items:
        meta = parse_metadata(item.get("metadataJson"))
        if meta.get("provider") == "feishu":
            conversation_id = str(meta.get("conversationId") or "")
            if conversation_id.startswith("ou_"):
                return conversation_id

    messages_dir = chat_root / "messages"
    for path in sorted(messages_dir.glob("*.json"), reverse=True):
        try:
            items = JsonStore.load(path, [])
        except Exception:
            continue
        for message in reversed(items):
            meta = parse_metadata(message.get("metadataJson"))
            sender_id = str(meta.get("senderId") or "")
            if meta.get("provider") == "feishu" and sender_id.startswith("ou_"):
                return sender_id

    # Scheme B may run from Kian-created local sessions that do not carry Feishu
    # sender metadata. In that case, recover the assignee from prior successful
    # task creation state instead of writing the open_id into global settings.
    state_candidates: List[Path] = [chat_root.parent / "tools/feishu-task-sync/state/state.json"]
    for state_path in state_candidates:
        try:
            processed = JsonStore.load(state_path, {}).get("processed", {})
        except Exception:
            continue
        if not isinstance(processed, dict):
            continue
        records = sorted(
            [item for item in processed.values() if isinstance(item, dict)],
            key=lambda item: str(item.get("updated_at") or item.get("first_seen_at") or ""),
            reverse=True,
        )
        for record in records:
            response = record.get("response")
            if not isinstance(response, dict):
                continue
            stack: List[Any] = [response]
            while stack:
                current = stack.pop()
                if isinstance(current, dict):
                    if str(current.get("role") or "") == "assignee":
                        member_id = str(current.get("id") or "")
                        if member_id.startswith("ou_"):
                            return member_id
                    stack.extend(current.values())
                elif isinstance(current, list):
                    stack.extend(current)

    # Last-resort fallback: previous legacy logs include the resolved assignee.
    log_candidates: List[Path] = [chat_root.parent / "tools/feishu-task-sync/output/cron.log"]
    for log_path in log_candidates:
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")[-200_000:]
        except Exception:
            continue
        matches = re.findall(r"ou_[A-Za-z0-9]+", text)
        if matches:
            return matches[-1]
    return None


def render_report(report: Dict[str, Any]) -> str:
    lines = ["# 飞书任务同步报告", ""]
    lines.append(f"- 生成时间：{report.get('generated_at')}")
    lines.append(f"- 模式：{report.get('mode')}")
    lines.append(f"- assignee_user_id：{report.get('assignee_user_id') or '-'}")
    lines.append(f"- 候选数：{report.get('candidate_count', 0)}")
    lines.append(f"- 本次处理数：{report.get('accepted_count', 0)}")
    lines.append(f"- 跳过数：{report.get('skipped_count', 0)}")
    lines.append("")
    auth = report.get("auth_check") or {}
    lines.append("## 权限检查")
    lines.append(f"- 结果：{'通过' if auth.get('ok') else '未通过'}")
    missing = auth.get("missing_scopes") or []
    lines.append(f"- 缺失权限：{', '.join(missing) if missing else '-'}")
    if auth.get("response"):
        lines.append(f"- 返回：`{json.dumps(auth.get('response'), ensure_ascii=False)[:500]}`")
    doc_auth = report.get("feishu_doc_auth_check") or {}
    if doc_auth:
        lines.append("")
        lines.append("## 飞书文档权限检查")
        lines.append(f"- 结果：{'至少一个发现接口可用' if doc_auth.get('ok') else '未通过'}")
        missing = doc_auth.get("missing_scopes") or []
        lines.append(f"- 可能缺失权限：{', '.join(missing) if missing else '-'}")
        for item in (doc_auth.get("checks") or [])[:5]:
            lines.append(f"- {item.get('source')}: {'ok' if item.get('ok') else item.get('error')}")
    diagnostics = report.get("feishu_doc_diagnostics") or []
    if diagnostics:
        lines.append("")
        lines.append("## 飞书文档 @我 诊断")
        for item in diagnostics[:20]:
            status = "ok" if item.get("ok") else "failed"
            detail = item.get("query") or item.get("doc_token") or item.get("error") or ""
            missing = item.get("missing_scopes") or []
            lines.append(f"- {item.get('source')}: {status} {detail} missing={','.join(missing) if missing else '-'}")
    lines.append("")
    lines.append("## 候选任务")
    accepted = report.get("accepted_candidates") or []
    if not accepted:
        lines.append("- 无")
    else:
        for idx, item in enumerate(accepted, start=1):
            lines.append(f"### {idx}. {item['title']}")
            lines.append(f"- score: {item['score']}")
            lines.append(f"- due: {item.get('due_text') or '-'} / {item.get('due_at') or '-'}")
            lines.append(f"- source: {item['source']['source_type']} / {item['source'].get('session_title') or item['source'].get('file_path')}")
            lines.append(f"- reasons: {', '.join(item.get('reasons') or [])}")
            lines.append(f"- excerpt: {item.get('raw_excerpt') or ''}")
            lines.append("")
    lines.append("## 创建结果")
    results = report.get("create_results") or []
    if not results:
        lines.append("- 本次未执行创建")
    else:
        for item in results:
            lines.append(
                f"- {item['status']} / guid={item.get('feishu_task_guid') or '-'} / task_id={item.get('feishu_task_id') or '-'} / {item.get('error') or ''}"
            )
    lines.append("")
    lines.append("## 跳过项")
    skipped = report.get("skipped") or []
    if not skipped:
        lines.append("- 无")
    else:
        for item in skipped[:50]:
            lines.append(f"- {item.get('title') or item.get('fingerprint')} / {item.get('reason')}")
    lines.append("")
    return "\n".join(lines)


def update_state(
    state_path: Path,
    existing_state: Dict[str, Any],
    baseline_items: List[CandidateTask],
    dry_run_items: List[CandidateTask],
    create_results: List[CreateResult],
) -> Dict[str, Any]:
    state = dict(existing_state)
    processed = dict(state.get("processed", {}))
    now = datetime.now(TZ).isoformat()

    for item in baseline_items:
        processed[item.fingerprint] = {
            "status": "baseline-seen",
            "title": item.title,
            "source": asdict(item.source),
            "updated_at": now,
        }

    for item in dry_run_items:
        record = dict(processed.get(item.fingerprint, {}))
        if record.get("status") in SKIP_STATUSES:
            continue
        record.update({
            "status": "dry-run-seen",
            "title": item.title,
            "source": asdict(item.source),
            "updated_at": now,
        })
        processed[item.fingerprint] = record

    for result in create_results:
        record = dict(processed.get(result.fingerprint, {}))
        record.update({
            "status": result.status,
            "feishu_task_guid": result.feishu_task_guid,
            "feishu_task_id": result.feishu_task_id,
            "error": result.error,
            "updated_at": now,
            "response": result.response,
        })
        processed[result.fingerprint] = record

    state["processed"] = processed
    state["updated_at"] = now
    JsonStore.save(state_path, state)
    return state


def main(argv: Sequence[str]) -> int:
    if argv and argv[0] in {"plan-b", "scheme-b", "方案B", "方案-b"}:
        print("方案 B 入口：")
        print("1. python3 collect.py --since-hours 1")
        print("2. 主 Agent 阅读 output/collected/latest.json，写 output/todos/latest-todos.json")
        print("3. python3 feishu_tasks.py create --input output/todos/latest-todos.json")
        print("旧入口 sync_feishu_tasks.py --create 保留为 deprecated fallback。")
        return 0
    args = parse_args(argv)
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    chat_root = Path(args.chat_root) if args.chat_root else settings.paths.chat_root
    docs_root = Path(args.docs_root) if args.docs_root else settings.paths.docs_root
    state_path = Path(args.state_path) if args.state_path else settings.paths.state_main_path
    report_json = Path(args.report_json) if args.report_json else settings.paths.report_json_path
    report_md = Path(args.report_md) if args.report_md else settings.paths.report_md_path

    state = JsonStore.load(state_path, {"processed": {}})
    client = FeishuClient(settings)
    auth_check = client.check_task_api()
    sessions = load_sessions(chat_root)
    assignee_user_id = discover_assignee_user_id(args.assignee_user_id, settings, sessions, chat_root)
    feishu_doc_auth_check = {} if args.disable_feishu_doc_mentions else client.check_doc_api()
    feishu_doc_diagnostics: List[Dict[str, Any]] = []

    if args.only_check_auth:
        report = {
            "generated_at": datetime.now(TZ).isoformat(),
            "mode": "auth-check",
            "assignee_user_id": assignee_user_id,
            "auth_check": auth_check,
            "feishu_doc_auth_check": feishu_doc_auth_check,
        }
        JsonStore.save(report_json, report)
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(render_report(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if auth_check.get("ok") else 2

    candidates = list(
        iter_chat_candidates(
            chat_root,
            sessions,
            args.min_score,
            args.include_assistant_messages,
            args.include_non_feishu_chats,
        )
    )
    candidates.extend(list(iter_doc_candidates(docs_root, args.min_score)))
    if not args.disable_feishu_doc_mentions:
        candidates.extend(
            list(
                iter_feishu_doc_mention_candidates(
                    client,
                    assignee_user_id,
                    args.feishu_doc_lookback_days,
                    args.min_score,
                    chat_root,
                    docs_root,
                    feishu_doc_diagnostics,
                )
            )
        )
    accepted, skipped = deduplicate_candidates(candidates, state, args.limit)

    create_results: List[CreateResult] = []
    baseline_items: List[CandidateTask] = []
    dry_run_items: List[CandidateTask] = []

    if args.baseline:
        baseline_items = accepted
    elif args.create:
        for candidate in accepted:
            try:
                create_resp = client.create_task(candidate.title)
                task = ((create_resp.get("data") or {}).get("task") or {})
                task_guid = task.get("guid") or create_resp.get("task_guid")
                task_id = task.get("task_id") or create_resp.get("task_id")
                status = "created"
                response_bundle: Dict[str, Any] = {"create": create_resp}
                if assignee_user_id and task_guid:
                    assign_resp = client.add_assignee(str(task_guid), assignee_user_id)
                    response_bundle["assign"] = assign_resp
                    status = "created+assigned"
                create_results.append(
                    CreateResult(
                        fingerprint=candidate.fingerprint,
                        status=status,
                        feishu_task_guid=str(task_guid) if task_guid else None,
                        feishu_task_id=str(task_id) if task_id else None,
                        response=response_bundle,
                    )
                )
            except Exception as exc:
                payload = exc.payload if isinstance(exc, FeishuApiError) else None
                create_results.append(
                    CreateResult(
                        fingerprint=candidate.fingerprint,
                        status="failed",
                        error=str(exc),
                        response=payload,
                    )
                )
    else:
        dry_run_items = accepted

    update_state(state_path, state, baseline_items, dry_run_items, create_results)

    mode = "baseline" if args.baseline else ("create" if args.create else "dry-run")
    report = {
        "generated_at": datetime.now(TZ).isoformat(),
        "mode": mode,
        "assignee_user_id": assignee_user_id,
        "auth_check": auth_check,
        "feishu_doc_auth_check": feishu_doc_auth_check,
        "feishu_doc_diagnostics": feishu_doc_diagnostics,
        "feishu_doc_lookback_days": args.feishu_doc_lookback_days,
        "feishu_doc_mentions_enabled": not args.disable_feishu_doc_mentions,
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "accepted_candidates": [asdict(item) for item in accepted],
        "create_results": [asdict(item) for item in create_results],
        "skipped": skipped,
        "paths": {
            "source": settings.config_source,
            "config_path": str(settings.config_path),
            "chat_root": str(chat_root),
            "docs_root": str(docs_root),
            "state_path": str(state_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
    }
    JsonStore.save(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_report(report), encoding="utf-8")

    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[ok] report json: {report_json}")
        print(f"[ok] report md:   {report_md}")
        print(f"[info] mode:      {mode}")
        print(f"[info] auth ok:   {auth_check.get('ok')}")
        print(f"[info] assignee:  {assignee_user_id or '-'}")
        print(f"[info] feishu doc mentions: {'enabled' if not args.disable_feishu_doc_mentions else 'disabled'} / lookback={args.feishu_doc_lookback_days}d")
        if feishu_doc_auth_check:
            missing = feishu_doc_auth_check.get("missing_scopes") or []
            print(f"[info] feishu doc auth ok: {feishu_doc_auth_check.get('ok')} / missing scopes: {', '.join(missing) if missing else '-'}")
        print(f"[info] candidates: {len(candidates)} total / {len(accepted)} accepted / {len(skipped)} skipped")
        if args.create:
            created = sum(1 for item in create_results if item.status.startswith('created'))
            failed = sum(1 for item in create_results if item.status == 'failed')
            print(f"[info] create results: {created} created / {failed} failed")
        elif args.baseline:
            print("[info] baseline recorded; existing candidates will not be backfilled.")
        else:
            print("[info] dry-run only. Use --create to actually create Feishu Tasks.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except ConfigError as exc:
        print(f"[sync_feishu_tasks] config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
