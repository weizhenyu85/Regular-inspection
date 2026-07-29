"""签到结果通知渲染 - 供单机模式和分组汇总模式共享"""

from datetime import datetime
from typing import Dict, Any, List


def merge_platform_stats(all_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """合并多个分组的 platform_stats

    每个分组的 platform_stats 结构一致：{platform: {success, failed, total_*, accounts}}。
    合并时对数值字段求和、拼接 accounts 列表。

    Args:
        all_stats: 各分组的 platform_stats 列表

    Returns:
        合并后的 platform_stats
    """
    numeric_keys = (
        "success", "failed", "total_quota", "total_used",
        "total_recharge", "total_used_change", "total_quota_change",
    )
    merged: Dict[str, Any] = {}
    for stats in all_stats:
        if not isinstance(stats, dict):
            continue
        for platform, s in stats.items():
            if platform not in merged:
                merged[platform] = {k: 0 for k in numeric_keys}
                merged[platform]["accounts"] = []
            for k in numeric_keys:
                merged[platform][k] += s.get(k, 0)
            merged[platform]["accounts"].extend(s.get("accounts", []))
    return merged


def _fmt_change(value: float, label: str = "") -> str:
    """把余额变化格式化为带正负号的短字符串，如 +$5.00 / -$5.00"""
    sign = "+" if value > 0 else "-"
    return f"{label}{sign}${abs(value):.2f}"


def render_notification(platform_stats: Dict[str, Any]) -> str:
    """渲染签到通知文本：按平台分组，每个账号占一行

    结构：
        🕓 时间 / 📊 总计
        ━━━ 平台A ━━━
        ✅ 账号  余额$x（已用$y） 📈+$z
        ❌ 账号  失败: 原因
        小计 ...
        ━━━ 平台B ━━━ ...
        ━━━ 全平台合计 ━━━（多平台时）
    """
    lines: List[str] = []

    # 标题：时间 + 总体统计
    lines.append(f"🕓 {datetime.now().strftime('%Y-%m-%d %H:%M')} (北京时间)")
    total_success = sum(p["success"] for p in platform_stats.values())
    total_failed = sum(p["failed"] for p in platform_stats.values())
    lines.append(f"📊 成功 {total_success} | 失败 {total_failed}")

    # 逐平台分区
    active_platforms = 0
    for platform, stats in sorted(platform_stats.items()):
        if stats["success"] + stats["failed"] == 0:
            continue
        active_platforms += 1

        lines.append("")
        lines.append(f"━━━ {platform} ━━━")

        for acc in stats["accounts"]:
            name = acc["name"]
            if acc["status"] == "✅":
                quota = acc.get("quota", 0) or 0
                used = acc.get("used", 0) or 0
                line = f"✅ {name}  余额${quota:.2f}（已用${used:.2f}）"
                # 余额变化：优先展示充值/总变动，否则展示可用额度变化
                recharge = acc.get("recharge")
                quota_change = acc.get("quota_change")
                if recharge:
                    line += f" 📈{_fmt_change(recharge)}"
                elif quota_change:
                    line += f" 📈{_fmt_change(quota_change, '可用')}"
                lines.append(line)
            else:
                error = acc.get("error", "Unknown error")
                lines.append(f"❌ {name}  失败: {error}")

        # 平台汇总（各平台分别计算）
        summary = f"─ {platform} 汇总: 成功{stats['success']} | 失败{stats['failed']}"
        if stats["total_quota"] > 0 or stats["total_used"] > 0:
            summary += f" · 余额${stats['total_quota']:.2f} · 已用${stats['total_used']:.2f}"
        if stats["total_recharge"] != 0:
            summary += f" 📈{_fmt_change(stats['total_recharge'])}"
        lines.append(summary)

    # 全平台合计（仅多平台时展示）
    if active_platforms > 1:
        total_quota = sum(p["total_quota"] for p in platform_stats.values())
        total_used = sum(p["total_used"] for p in platform_stats.values())
        total_recharge = sum(p["total_recharge"] for p in platform_stats.values())
        lines.append("")
        lines.append("━━━ 全平台合计 ━━━")
        tail = f"余额${total_quota:.2f} · 已用${total_used:.2f}"
        if total_recharge != 0:
            tail += f" 📈{_fmt_change(total_recharge)}"
        lines.append(tail)

    return "\n".join(lines)
