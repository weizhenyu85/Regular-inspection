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


def render_notification(platform_stats: Dict[str, Any]) -> str:
    """根据 platform_stats 渲染签到通知文本"""
    notification_lines = []

    # 标题和执行时间
    notification_lines.append(f"🕓 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    notification_lines.append("")

    # 统计结果
    total_success = sum(p['success'] for p in platform_stats.values())
    total_failed = sum(p['failed'] for p in platform_stats.values())

    notification_lines.append("📊 统计结果:")
    notification_lines.append(f"✓ 成功: {total_success} 个")
    notification_lines.append(f"✗ 失败: {total_failed} 个")
    notification_lines.append("")

    # 详细结果 - 按平台分组展示
    notification_lines.append("📝 详细结果:")
    notification_lines.append("")

    for platform, stats in sorted(platform_stats.items()):
        for account_info in stats['accounts']:
            status = account_info['status']
            name = account_info['name']

            if status == '✅':
                # 成功的账号
                quota = account_info.get('quota', 0)
                used = account_info.get('used', 0)
                balance_str = f"💰 余额: ${quota:.2f}, 已用: ${used:.2f}"

                # 检查是否有变化
                recharge = account_info.get('recharge')
                quota_change = account_info.get('quota_change')

                if recharge or quota_change:
                    change_parts = []
                    if recharge:
                        change_parts.append(f"增加+${abs(recharge):.2f}" if recharge > 0 else f"减少-${abs(recharge):.2f}")
                    if quota_change:
                        change_parts.append(f"可用+${abs(quota_change):.2f}" if quota_change > 0 else f"可用-${abs(quota_change):.2f}")
                    notification_lines.append(f"{status} {platform} {name}")
                    notification_lines.append(f"   签到成功 {balance_str}")
                    notification_lines.append(f"   📈 变动: {', '.join(change_parts)}")
                else:
                    notification_lines.append(f"{status} {platform} {name}")
                    notification_lines.append(f"   签到成功 {balance_str}")
            else:
                # 失败的账号
                error = account_info.get('error', 'Unknown error')
                quota = account_info.get('quota')
                used = account_info.get('used')
                notification_lines.append(f"{status} {platform} {name}")
                notification_lines.append(f"   签到失败: {error}")
                if quota is not None and used is not None:
                    notification_lines.append(f"   💰 余额: ${quota:.2f}, 已用: ${used:.2f} (未更新)")

            # 每个账号后添加空行分隔
            notification_lines.append("")

    # 移除最后一个多余的空行（因为后面紧跟着平台汇总）
    if notification_lines and notification_lines[-1] == "":
        notification_lines.pop()

    # 各平台汇总
    for platform, stats in sorted(platform_stats.items()):
        if stats['success'] + stats['failed'] == 0:
            continue

        notification_lines.append(f"─── {platform} 平台汇总 ───")
        notification_lines.append(f"✓ 成功: {stats['success']} 个 | ✗ 失败: {stats['failed']} 个")

        if stats['total_quota'] > 0 or stats['total_used'] > 0:
            notification_lines.append(f"💰 总余额: ${stats['total_quota']:.2f}, 总已用: ${stats['total_used']:.2f}")

        if stats['total_recharge'] != 0 or stats['total_quota_change'] != 0:
            change_parts = []
            if stats['total_recharge'] != 0:
                change_parts.append(f"增加+${abs(stats['total_recharge']):.2f}" if stats['total_recharge'] > 0 else f"减少-${abs(stats['total_recharge']):.2f}")
            if stats['total_quota_change'] != 0:
                change_parts.append(f"可用+${abs(stats['total_quota_change']):.2f}" if stats['total_quota_change'] > 0 else f"可用-${abs(stats['total_quota_change']):.2f}")
            notification_lines.append(f"📈 本期变动: {', '.join(change_parts)}")

        notification_lines.append("")

    # 全平台总汇总
    total_quota = sum(p['total_quota'] for p in platform_stats.values())
    total_used = sum(p['total_used'] for p in platform_stats.values())
    total_recharge = sum(p['total_recharge'] for p in platform_stats.values())
    total_quota_change = sum(p['total_quota_change'] for p in platform_stats.values())

    notification_lines.append("━━━ 全平台汇总 ━━━")
    if total_quota > 0 or total_used > 0:
        notification_lines.append(f"💰 总余额: ${total_quota:.2f}")
        notification_lines.append(f"📊 总已用: ${total_used:.2f}")

    if total_recharge != 0 or total_quota_change != 0:
        change_parts = []
        if total_recharge != 0:
            change_parts.append(f"增加+${abs(total_recharge):.2f}" if total_recharge > 0 else f"减少-${abs(total_recharge):.2f}")
        if total_quota_change != 0:
            change_parts.append(f"可用+${abs(total_quota_change):.2f}" if total_quota_change > 0 else f"可用-${abs(total_quota_change):.2f}")
        notification_lines.append(f"📈 本期变动: {', '.join(change_parts)}")

    return "\n".join(notification_lines)
