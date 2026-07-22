#!/usr/bin/env python3
"""
分组签到结果汇总通知

在 GitHub Actions 中，账号被拆分到多个并行任务（不同出口 IP）执行签到，
每个任务把结果写入 checkin_result_gN.json。本脚本下载/收集所有分组结果，
合并后发送**单条**通知，避免每个分组各发一条。

用法：
    python summary_notify.py [结果文件目录]

默认在当前目录递归查找 checkin_result_g*.json。
"""

import glob
import json
import logging
import os
import sys

from dotenv import load_dotenv

from utils.report import merge_platform_stats, render_notification
from utils.notify import notify

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def find_result_files(search_dir: str) -> list:
    """递归查找所有分组结果文件"""
    pattern = os.path.join(search_dir, "**", "checkin_result_g*.json")
    files = sorted(glob.glob(pattern, recursive=True))
    # 兼容结果文件直接位于 search_dir 根部的情况
    files += sorted(glob.glob(os.path.join(search_dir, "checkin_result_g*.json")))
    return sorted(set(files))


def main() -> int:
    search_dir = sys.argv[1] if len(sys.argv) > 1 else "."

    files = find_result_files(search_dir)
    if not files:
        logger.info(f"ℹ️ 在 '{search_dir}' 未找到任何分组结果文件（checkin_result_g*.json），跳过通知")
        return 0

    logger.info(f"🔍 找到 {len(files)} 个分组结果文件")

    all_stats = []
    need_notify = False
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            all_stats.append(data.get("platform_stats", {}))
            group_need = bool(data.get("need_notify", False))
            need_notify = need_notify or group_need
            logger.info(f"✅ 已加载 {fp} (group={data.get('group')}, need_notify={group_need})")
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.warning(f"⚠️ 读取 {fp} 失败: {e}")

    merged = merge_platform_stats(all_stats)
    if not merged:
        logger.info("ℹ️ 合并后没有任何签到结果，跳过通知")
        return 0

    total_success = sum(p["success"] for p in merged.values())
    total_failed = sum(p["failed"] for p in merged.values())
    logger.info(f"📊 合并结果: 成功 {total_success} 个，失败 {total_failed} 个")

    if not need_notify:
        logger.info("ℹ️ 所有分组均无需通知（全部成功且余额无变化），跳过")
        return 0

    notify_content = render_notification(merged)
    logger.info("\n" + notify_content)
    notify.push_message("Router签到提醒", notify_content, msg_type="text")
    logger.info("\n🔔 合并通知已发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
