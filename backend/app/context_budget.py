"""对话上下文 token 预算估算与历史裁剪（V1.2.3）。

用于前台对话路由（chat.py）在同一会话内注入历史消息做多轮连续对话，同时保证
[system + 历史 + 当前 user(含 RAG 上下文)] 不超过模型 context_window 减去输出预留，
避免历史把模型上下文撑爆。
"""
import re

# 预留输出 token 下限：max(LLM max_tokens, 该值)。防止输出配额占满窗口把上下文挤光。
RESERVED_MIN_OUTPUT = 8192

_CJK_RE = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文约 1.5 token/字，其余字符约 0.3 token/字符。

    与 chunking_service 的中文估算口径一致；对英文/数字略偏高，整体偏保守，
    宁可多估避免撑爆模型上下文。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    return int(cjk * 1.5 + (len(text) - cjk) * 0.3) + 1


def trim_history_for_budget(
    system_content: str,
    history: list[dict],
    current_user_content: str,
    max_tokens: int,
    context_window: int,
) -> list[dict]:
    """从最旧开始裁剪 history，使 system + 剩余 history + 当前 user 总 token
    不超过 context_window - max(max_tokens, RESERVED_MIN_OUTPUT)。

    保留策略：优先保留时间上最近的轮次（reversed 尾插），保证最近几轮上下文不丢；
    history 可被全部裁掉（退化为单轮）。任何情况下当前轮 user 消息始终保留。

    Args:
        system_content: system 提示词
        history: 历史消息，旧→新，每项 {"role", "content"}
        current_user_content: 当前轮 user 消息（已含 RAG 检索上下文）
        max_tokens: LLM 输出上限
        context_window: 模型上下文窗口大小（tokens）
    """
    output_reserve = max(int(max_tokens or 0), RESERVED_MIN_OUTPUT)
    budget = int(context_window or 0) - output_reserve

    if budget <= 0:
        # 窗口配置异常/过小：仅保留 system + 当前轮，防止撑爆
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": current_user_content},
        ]

    sys_cost = estimate_tokens(system_content)
    cur_cost = estimate_tokens(current_user_content)
    remaining = budget - sys_cost - cur_cost

    kept: list[dict] = []
    running = 0
    if remaining > 0:
        for msg in reversed(history):  # 从最新往最旧，保留最近上下文
            cost = estimate_tokens(msg.get("content", "")) + 4  # 每条消息角色开销
            if running + cost <= remaining:
                kept.append(msg)
                running += cost
            else:
                break
        kept.reverse()  # 恢复时间顺序

    messages = [
        {"role": "system", "content": system_content},
        *kept,
        {"role": "user", "content": current_user_content},
    ]

    # 极端兜底：system 本身超窗时截断 system，绝不丢弃当前轮 user
    if sys_cost > budget:
        messages[0]["content"] = system_content[: max(int(budget / 1.5), 500)]
    return messages
