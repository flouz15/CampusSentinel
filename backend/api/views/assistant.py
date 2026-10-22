"""Campus assistant endpoints."""

import json
import re
import uuid

import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import AssistantSessionMessage, DetectionRecord, Report, SecurityTicket, UserProfile
from .common import (
    ASSISTANT_MEMORY_LIMIT,
    ASSISTANT_OLLAMA_BASE,
    ASSISTANT_OLLAMA_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_COMPAT_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT_SECONDS,
    LIGHTRAG_API_KEY,
    LIGHTRAG_BASE_URL,
    LIGHTRAG_TIMEOUT_SECONDS,
    _get_auth_user,
    _get_request_data,
    _parse_int,
)
from .patrol_assistant import handle_assistant_task_command

def _assistant_tokenize(text):
    return [t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", str(text or "")) if t]


def _assistant_score(question_tokens, content):
    content_lower = str(content or "").lower()
    if not content_lower:
        return 0
    score = 0
    for token in question_tokens:
        if token in content_lower:
            score += min(4, max(1, len(token) // 2))
    return score


def _assistant_build_documents():
    docs = []
    for report in Report.objects.order_by("-created_at")[:120]:
        docs.append({
            "id": str(report.id),
            "type": "report",
            "title": report.location or report.category or "校园上报",
            "content": " | ".join([
                report.location or "",
                report.category or "",
                report.status or "",
                report.reporter_name or "",
                report.note or "",
            ]),
            "time": report.created_at.isoformat() if report.created_at else "",
        })

    for ticket in SecurityTicket.objects.select_related("report").order_by("-updated_at")[:120]:
        docs.append({
            "id": str(ticket.id),
            "type": "ticket",
            "title": f"工单#{ticket.id}",
            "content": " | ".join([
                ticket.status or "",
                ticket.channel or "",
                ticket.assignee or "",
                ticket.message or "",
                (ticket.report.location if ticket.report else "") or "",
                (ticket.report.category if ticket.report else "") or "",
            ]),
            "time": ticket.updated_at.isoformat() if ticket.updated_at else "",
        })

    for rec in DetectionRecord.objects.order_by("-created_at")[:120]:
        docs.append({
            "id": str(rec.id),
            "type": "detection",
            "title": f"检测#{rec.id}",
            "content": " | ".join([
                rec.detect_type or "",
                rec.label or "",
                rec.status or "",
                rec.model_name or "",
                rec.original_name or "",
                rec.error_message or "",
            ]),
            "time": rec.created_at.isoformat() if rec.created_at else "",
        })
    return docs


def _assistant_retrieve(question, top_k=5):
    question_tokens = _assistant_tokenize(question)
    docs = _assistant_build_documents()
    ranked = []
    for doc in docs:
        score = _assistant_score(question_tokens, f"{doc.get('title', '')} {doc.get('content', '')}")
        if score > 0:
            ranked.append((score, doc))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in ranked[:top_k]]


def _assistant_build_history_text(history):
    if not history:
        return "无历史对话。"
    items = history[-ASSISTANT_MEMORY_LIMIT:]
    lines = []
    for idx, item in enumerate(items, start=1):
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if not q or not a:
            continue
        lines.append(f"{idx}. 用户：{q}\n助手：{a}")
    return "\n\n".join(lines) or "无历史对话。"



def _assistant_build_messages(question, contexts, history=None, admin_mode=False):
    prompt_context = "\n".join([
        f"[{idx + 1}] {ctx['type']} | {ctx['title']} | {ctx['time']}\n{ctx['content']}"
        for idx, ctx in enumerate(contexts)
    ]) or "暂无命中上下文，请仅给出谨慎建议并提示用户补充信息。"

    role_prompt = (
        "你是平安校园智能巡检助手，服务校园安保与巡检值班人员。"
        "请基于系统上下文回答，语言为简体中文，回答简洁可执行；"
        "若信息不足必须明确说明根据当前系统数据不足以确认。"
    )
    if admin_mode:
        role_prompt += "当前为管理员分析模式，请突出风险优先级、处置时效与责任建议。"

    messages = [{"role": "system", "content": role_prompt}]
    for item in (history or [])[-ASSISTANT_MEMORY_LIMIT:]:
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if q:
            messages.append({"role": "user", "content": q})
        if a:
            messages.append({"role": "assistant", "content": a})

    user_prompt = (
        f"用户问题：{question}\n\n"
        f"系统上下文：\n{prompt_context}"
    )
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _assistant_call_deepseek(question, contexts, history=None, admin_mode=False):
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY_MISSING")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": _assistant_build_messages(
            question,
            contexts,
            history=history,
            admin_mode=admin_mode,
        ),
        "temperature": 0.2,
        "top_p": 0.85,
        # 关闭思考/推理模式，直接返回最终回答
        "thinking": {"type": "disabled"},
    }
    response = requests.post(
        f"{DEEPSEEK_COMPAT_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=DEEPSEEK_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text_part = item.get("text") or item.get("content") or ""
                if text_part:
                    parts.append(str(text_part))
            elif item:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content or "").strip()

def _assistant_call_local_llm(question, contexts, history=None, admin_mode=False):
    prompt_context = "\n".join([
        f"[{idx + 1}] {ctx['type']} | {ctx['title']} | {ctx['time']}\n{ctx['content']}"
        for idx, ctx in enumerate(contexts)
    ]) or "暂无命中上下文，请仅给出谨慎建议并提示用户补充信息。"

    role_prompt = (
        "你是平安校园系统管理员分析助手。请突出风险优先级、处置时效、责任建议，并给出可执行动作。"
        if admin_mode
        else "你是平安校园系统问答助手。"
    )

    prompt = (
        f"{role_prompt}"
        "请基于上下文回答，语言为简体中文，"
        "回答要点不超过6条；若信息不足必须明确说明“根据当前系统数据不足以确认”。\n\n"
        f"历史对话：\n{_assistant_build_history_text(history)}\n\n"
        f"用户问题：{question}\n\n"
        f"上下文：\n{prompt_context}\n"
    )

    payload = {
        "model": ASSISTANT_OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.85,
        },
    }
    response = requests.post(
        f"{ASSISTANT_OLLAMA_BASE.rstrip('/')}/api/generate",
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return (data.get("response") or "").strip()


def _assistant_rule_answer(question, contexts, admin_mode=False):
    if not contexts:
        return (
            "根据当前系统数据不足以确认。建议先检查“告警中心/工单处置/检测记录”是否有相关记录，"
            "再重新提问。"
        )
    lines = [f"已为你检索到 {len(contexts)} 条相关记录："]
    for ctx in contexts[:3]:
        lines.append(f"- [{ctx['type']}] {ctx['title']}（{ctx['time'][:19].replace('T', ' ')}）")
    if admin_mode:
        lines.append("管理员建议：优先处置高优先级告警，明确责任人和预计完成时间，必要时触发跨部门协同。")
    else:
        lines.append("建议：进入对应页面核实详情，并按工单流程推进状态。")
    return "\n".join(lines)


def _assistant_normalize_session_id(session_id):
    text = str(session_id or "").strip()
    if text:
        return text[:64]
    return uuid.uuid4().hex


def _assistant_load_history(session_id, user=None):
    rows = (
        AssistantSessionMessage.objects.filter(session_id=session_id, user=user)
        .order_by("-id")[: ASSISTANT_MEMORY_LIMIT * 2]
    )
    rows = list(rows)
    rows.reverse()
    history = []
    pending_q = None
    for row in rows:
        if row.role == AssistantSessionMessage.ROLE_USER:
            pending_q = row.content
            continue
        if row.role == AssistantSessionMessage.ROLE_ASSISTANT:
            if pending_q:
                history.append({"q": pending_q, "a": row.content})
                pending_q = None
    if len(history) > ASSISTANT_MEMORY_LIMIT:
        history = history[-ASSISTANT_MEMORY_LIMIT:]
    return history


def _assistant_append_history(session_id, user, question, answer, mode):
    AssistantSessionMessage.objects.create(
        session_id=session_id,
        user=user,
        role=AssistantSessionMessage.ROLE_USER,
        content=question,
        meta_json={"mode": mode},
    )
    AssistantSessionMessage.objects.create(
        session_id=session_id,
        user=user,
        role=AssistantSessionMessage.ROLE_ASSISTANT,
        content=answer,
        meta_json={"mode": mode},
    )


def _assistant_history_size(session_id, user):
    return (
        AssistantSessionMessage.objects.filter(
            session_id=session_id,
            user=user,
            role=AssistantSessionMessage.ROLE_USER,
        ).count()
    )


def _assistant_to_lightrag_history(history):
    items = []
    for row in history or []:
        question = str(row.get("q") or "").strip()
        answer = str(row.get("a") or "").strip()
        if question:
            items.append({"role": "user", "content": question})
        if answer:
            items.append({"role": "assistant", "content": answer})
    return items


def _assistant_normalize_query_mode(mode, admin_mode=False):
    raw = str(mode or "").strip().lower()
    allowed = {"local", "global", "hybrid", "naive", "mix", "bypass"}
    if raw in allowed:
        return raw
    if raw in {"admin", "admin_analysis"}:
        return "global" if admin_mode else "mix"
    if raw == "default":
        return "mix"
    return "global" if admin_mode else "mix"


def _assistant_reference_to_source(item):
    if not isinstance(item, dict):
        text = str(item or "").strip()
        return {
            "id": "",
            "type": "reference",
            "title": text or "参考资料",
            "time": "",
            "url": "",
            "snippet": "",
        }
    item_type = str(item.get("type") or item.get("source_type") or "reference")
    raw_id = item.get("id") or item.get("doc_id") or item.get("entity_id") or ""
    url = (
        item.get("url")
        or item.get("link")
        or (
            _assistant_source_link({"type": item_type, "id": raw_id})
            if item_type in {"report", "ticket", "detection"}
            else ""
        )
    )
    title = (
        item.get("title")
        or item.get("name")
        or item.get("file_name")
        or item.get("source")
        or item.get("doc_name")
        or "参考资料"
    )
    return {
        "id": str(raw_id or ""),
        "type": item_type,
        "title": str(title),
        "time": str(item.get("time") or item.get("created_at") or item.get("updated_at") or ""),
        "url": str(url or ""),
        "snippet": str(item.get("content") or item.get("chunk_content") or item.get("text") or ""),
    }


def _assistant_call_lightrag(question, *, mode, top_k, history=None, admin_mode=False):
    query_mode = _assistant_normalize_query_mode(mode, admin_mode=admin_mode)
    payload = {
        "query": question,
        "mode": query_mode,
        "enable_rerank": True,
        "include_references": True,
        "include_chunk_content": False,
        "top_k": top_k,
        "chunk_top_k": top_k,
        "conversation_history": _assistant_to_lightrag_history(history),
    }
    if admin_mode:
        payload["response_type"] = "请输出结构化风险分析，包含结论、风险等级、依据和建议动作。"

    headers = {"Content-Type": "application/json"}
    if LIGHTRAG_API_KEY:
        headers["X-API-Key"] = LIGHTRAG_API_KEY

    response = requests.post(
        f"{LIGHTRAG_BASE_URL.rstrip('/')}/query",
        json=payload,
        headers=headers,
        timeout=LIGHTRAG_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}
    if not isinstance(data, dict):
        raise ValueError("Invalid LightRAG response")

    answer = str(data.get("response") or "").strip()
    references_raw = data.get("references")
    references = references_raw if isinstance(references_raw, list) else []
    sources = [_assistant_reference_to_source(item) for item in references]
    return {
        "answer": answer,
        "mode": query_mode,
        "sources": sources,
        "references": references,
        "raw": data,
    }


def _assistant_source_link(item):
    item_type = item.get("type")
    raw_id = item.get("id")
    if item_type == "report":
        return f"/frontend/index.html#alert={raw_id}"
    if item_type == "ticket":
        return f"/frontend/index.html#ticket={raw_id}"
    if item_type == "detection":
        return f"/frontend/index.html#report={raw_id}"
    return "/frontend/index.html"


@require_http_methods(["GET"])
def assistant_health(request):
    rag_ok = False
    rag_error = ""
    rag_status_code = None
    try:
        resp = requests.get(LIGHTRAG_BASE_URL.rstrip("/"), timeout=3)
        rag_status_code = resp.status_code
        rag_ok = resp.status_code < 500
    except Exception as exc:
        rag_error = str(exc)

    ollama_ok = False
    ollama_error = ""
    try:
        resp = requests.get(f"{ASSISTANT_OLLAMA_BASE.rstrip('/')}/api/tags", timeout=3)
        ollama_ok = resp.ok
    except Exception as exc:
        ollama_error = str(exc)

    deepseek_configured = bool(DEEPSEEK_API_KEY)

    return JsonResponse({
        "success": True,
        "deepseek": {
            "provider": "deepseek",
            "base": DEEPSEEK_COMPAT_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "configured": deepseek_configured,
            "available": deepseek_configured,
            "error": None if deepseek_configured else "DEEPSEEK_API_KEY_MISSING",
        },
        "rag": {
            "provider": "lightrag",
            "base": LIGHTRAG_BASE_URL,
            "available": rag_ok,
            "statusCode": rag_status_code,
            "error": rag_error or None,
        },
        "llm": {
            "provider": "ollama",
            "base": ASSISTANT_OLLAMA_BASE,
            "model": ASSISTANT_OLLAMA_MODEL,
            "available": ollama_ok,
            "error": ollama_error or None,
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def assistant_ask(request):
    payload = _get_request_data(request)
    question = str(payload.get("question") or "").strip()
    top_k = _parse_int(payload.get("topK")) or 5
    mode = str(payload.get("mode") or "default").strip().lower()
    query_mode = str(payload.get("queryMode") or "").strip().lower()
    session_id_input = payload.get("sessionId")
    reset_session = bool(payload.get("resetSession"))
    if top_k < 1:
        top_k = 1
    if top_k > 10:
        top_k = 10
    if not question:
        return JsonResponse({"success": False, "error": "MISSING_QUESTION"}, status=400)

    user, profile = _get_auth_user(request)
    admin_mode = mode in {"admin", "admin_analysis"}
    if admin_mode and (profile is None or profile.role != UserProfile.ROLE_ADMIN):
        return JsonResponse({"success": False, "error": "ADMIN_ONLY_MODE"}, status=403)

    session_id = _assistant_normalize_session_id(session_id_input)
    session_user = user if user else None
    if reset_session:
        AssistantSessionMessage.objects.filter(session_id=session_id, user=session_user).delete()
    history = _assistant_load_history(session_id, user=session_user)

    contexts = _assistant_retrieve(question, top_k=top_k)
    answer_mode = "rag_rule"
    provider = "local_fallback"
    llm_error = None
    rag_error = None
    deepseek_error = None
    answer = ""
    sources = []
    effective_query_mode = _assistant_normalize_query_mode(
        query_mode or mode,
        admin_mode=admin_mode,
    )

    task_command = handle_assistant_task_command(question)
    if task_command and task_command.get("handled"):
        answer = str(task_command.get("answer") or "").strip()
        if answer:
            _assistant_append_history(
                session_id=session_id,
                user=session_user,
                question=question,
                answer=answer,
                mode=("admin" if admin_mode else "default"),
            )
            history_size = _assistant_history_size(session_id, user=session_user)
            return JsonResponse({
                "success": True,
                "mode": "patrol_task_command",
                "provider": "patrol_task_control",
                "queryMode": effective_query_mode,
                "analysisMode": "admin" if admin_mode else "default",
                "sessionId": session_id,
                "historySize": history_size,
                "answer": answer,
                "question": question,
                "sources": [],
                "taskAction": task_command.get("action"),
                "task": task_command.get("task"),
                "report": task_command.get("report"),
                "ragError": None,
                "llmError": None,
                "deepseekError": None,
                "model": None,
                "userRole": profile.role if profile else None,
                "authed": bool(user),
            })

    try:
        answer = _assistant_call_deepseek(
            question=question,
            contexts=contexts,
            history=history,
            admin_mode=admin_mode,
        )
        if answer:
            answer_mode = "deepseek_chat"
            provider = "deepseek"
    except Exception as exc:
        deepseek_error = str(exc)

    if not answer:
        try:
            rag_result = _assistant_call_lightrag(
                question,
                mode=query_mode or mode,
                top_k=top_k,
                history=history,
                admin_mode=admin_mode,
            )
            answer = rag_result["answer"]
            sources = rag_result["sources"]
            effective_query_mode = rag_result["mode"]
            if answer:
                answer_mode = "lightrag_query"
                provider = "lightrag"
        except Exception as exc:
            rag_error = str(exc)

    if not answer:
        try:
            answer = _assistant_call_local_llm(
                question=question,
                contexts=contexts,
                history=history,
                admin_mode=admin_mode,
            )
            if answer:
                answer_mode = "local_llm_rag"
                provider = "ollama_rag"
        except Exception as exc:
            llm_error = str(exc)

    if not answer:
        answer = _assistant_rule_answer(question, contexts, admin_mode=admin_mode)
        provider = "rule_retrieval"
        answer_mode = "rag_rule"

    if not sources:
        sources = [
            {
                "id": item["id"],
                "type": item["type"],
                "title": item["title"],
                "time": item["time"],
                "url": _assistant_source_link(item),
                "snippet": "",
            }
            for item in contexts
        ]

    _assistant_append_history(
        session_id=session_id,
        user=session_user,
        question=question,
        answer=answer,
        mode=("admin" if admin_mode else "default"),
    )
    history_size = _assistant_history_size(session_id, user=session_user)

    return JsonResponse({
        "success": True,
        "mode": answer_mode,
        "provider": provider,
        "queryMode": effective_query_mode,
        "analysisMode": "admin" if admin_mode else "default",
        "sessionId": session_id,
        "historySize": history_size,
        "answer": answer,
        "question": question,
        "sources": sources,
        "ragError": rag_error,
        "llmError": llm_error,
        "deepseekError": deepseek_error,
        "model": DEEPSEEK_MODEL if provider == "deepseek" else None,
        "userRole": profile.role if profile else None,
        "authed": bool(user),
    })
