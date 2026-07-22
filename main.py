#!/usr/bin/env python3
"""
Router平台自动签到脚本 - 重构版
支持 AnyRouter、AgentRouter 等多平台
支持 Cookies、GitHub、Linux.do 等多种认证方式
"""

import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional

import httpx
from dotenv import load_dotenv

from checkin import CheckIn
from utils.config import AppConfig, load_accounts, validate_account
from utils.constants import get_account_group_suffix
from utils.report import render_notification
from utils.notify import notify

load_dotenv(override=True)

BALANCE_HASH_FILE = f"balance_hash{get_account_group_suffix()}.txt"


def check_dependencies():
    """检查必要的依赖是否已安装"""
    logger = logging.getLogger(__name__)
    missing_deps = []

    try:
        import playwright
    except ImportError:
        missing_deps.append("playwright")

    try:
        import httpx
    except ImportError:
        missing_deps.append("httpx")

    try:
        import pyotp
    except ImportError:
        # pyotp 是可选依赖（仅2FA需要）
        logger.info("ℹ️ pyotp 未安装（仅GitHub 2FA需要）")

    if missing_deps:
        logger.error(f"❌ 缺少必要依赖: {', '.join(missing_deps)}")
        logger.info("💡 请运行: pip install -r requirements.txt")
        sys.exit(1)

    # 检查 Playwright 浏览器是否已安装
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                p.chromium.launch(headless=True, timeout=5000)
            except Exception as browser_error:
                if "Executable doesn't exist" in str(browser_error):
                    logger.error("❌ Playwright 浏览器未安装")
                    logger.info("💡 请运行: playwright install chromium")
                    sys.exit(1)
    except Exception as e:
        logger.warning(f"⚠️ 无法验证 Playwright 浏览器: {e}")

    logger.info("✅ 所有必要依赖已安装")


def validate_env_vars():
    """验证必要的环境变量"""
    logger = logging.getLogger(__name__)
    missing_vars = []
    warnings = []

    # 检查账号配置（至少需要一个）
    account_vars = ["ANYROUTER_ACCOUNTS", "AGENTROUTER_ACCOUNTS", "ACCOUNTS"]
    has_account_config = any(os.getenv(var) for var in account_vars)

    if not has_account_config:
        missing_vars.append("账号配置环境变量")
        logger.error(f"❌ 缺少账号配置: 需要设置 {', '.join(account_vars)} 中的至少一个")

    # 检查可选但建议的环境变量
    optional_vars = {
        "NOTIFY_PUSHPLUS_TOKEN": "PushPlus 推送通知",
        "NOTIFY_DINGTALK_WEBHOOK": "钉钉webhook通知",
        "NOTIFY_FEISHU_WEBHOOK": "飞书webhook通知",
        "NOTIFY_WECHAT_WORK_WEBHOOK": "企业微信webhook通知",
    }

    has_notify = any(os.getenv(var) for var in optional_vars.keys())
    if not has_notify:
        warnings.append("未配置任何通知方式，将无法接收签到结果通知")

    # 输出验证结果
    if missing_vars:
        logger.error("\n❌ 环境变量验证失败:")
        for var in missing_vars:
            logger.error(f"   - 缺少: {var}")
        logger.info("\n💡 配置说明:")
        logger.info("   请在 .env 文件或环境变量中设置账号配置")
        logger.info(f"   支持的环境变量: {', '.join(account_vars)}")
        return False

    if warnings:
        logger.warning("\n⚠️ 环境变量警告:")
        for warn in warnings:
            logger.warning(f"   - {warn}")

    logger.info("✅ 环境变量验证通过")
    return True


def cleanup_old_logs(log_dir: str, days: int = 30) -> int:
    """清理旧日志文件（保留最近N天）

    Args:
        log_dir: 日志目录
        days: 保留天数

    Returns:
        删除的日志文件数量
    """
    try:
        from pathlib import Path
        import time

        log_path = Path(log_dir)
        if not log_path.exists():
            return 0

        cutoff_time = time.time() - (days * 24 * 60 * 60)
        deleted_count = 0

        for log_file in log_path.glob("checkin_*.log*"):
            # 检查文件修改时间
            if log_file.stat().st_mtime < cutoff_time:
                try:
                    log_file.unlink()
                    deleted_count += 1
                except (OSError, PermissionError) as e:
                    # 文件正在被使用或权限不足，跳过
                    pass

        if deleted_count > 0:
            logger = logging.getLogger(__name__)
            logger.info(f"🗑️ 已清理 {deleted_count} 个超过 {days} 天的旧日志文件")

        return deleted_count

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.debug(f"清理旧日志失败: {e}")
        return 0


def setup_logging():
    """配置日志系统（支持日志轮转）"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"checkin_{datetime.now().strftime('%Y%m%d')}.log")

    # 从环境变量读取日志级别，默认为INFO
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # 配置logging - 使用 RotatingFileHandler 实现日志轮转
    # maxBytes: 10MB, backupCount: 5 个备份文件
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            file_handler,
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger(__name__)
    if log_level_str != "INFO":
        logger.info(f"ℹ️ 日志级别已设置为: {log_level_str}")

    # 清理旧日志文件（保留最近30天）
    cleanup_old_logs(log_dir, days=30)

    return logger


def load_balance_hash() -> Optional[str]:
    """加载余额hash"""
    try:
        if os.path.exists(BALANCE_HASH_FILE):
            with open(BALANCE_HASH_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
    except (IOError, OSError, PermissionError) as e:
        logger = logging.getLogger(__name__)
        logger.warning(f"读取余额hash文件失败: {e}")
    except (ValueError, UnicodeDecodeError) as e:
        logger = logging.getLogger(__name__)
        logger.warning(f"解析余额hash内容失败: {e}")
    return None


def save_balance_hash(balance_hash: str) -> None:
    """保存余额hash"""
    logger = logging.getLogger(__name__)
    try:
        with open(BALANCE_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(balance_hash)
        logger.debug(f"余额hash已保存: {balance_hash}")
    except (IOError, OSError) as e:
        error_msg = f"Failed to save balance hash: {e}"
        logger.warning(f"⚠️ {error_msg}")
        logger.error(error_msg, exc_info=True)


def filter_account_group(accounts: List, logger) -> List:
    """按分组筛选账号（用于多 IP 分批签到，规避同 IP 风控）

    通过环境变量控制：
        ACCOUNT_GROUP: 当前任务负责的分组序号（从 0 开始）
        ACCOUNT_GROUP_COUNT: 总分组数

    采用取模轮转分配（i % count == group），把账号均匀分散到各分组。
    未设置或分组数 <= 1 时返回全部账号，保持原行为不变。
    """
    group = os.getenv("ACCOUNT_GROUP")
    count = os.getenv("ACCOUNT_GROUP_COUNT")

    if group is None or not count:
        return accounts

    try:
        g = int(group)
        n = int(count)
    except (ValueError, TypeError):
        logger.warning(f"⚠️ 账号分组配置无效 (ACCOUNT_GROUP={group}, ACCOUNT_GROUP_COUNT={count})，忽略分组")
        return accounts

    if n <= 1:
        return accounts

    if not (0 <= g < n):
        logger.warning(f"⚠️ ACCOUNT_GROUP={g} 超出范围 [0, {n})，忽略分组")
        return accounts

    selected = [a for i, a in enumerate(accounts) if i % n == g]
    logger.info(f"🧩 账号分组已启用: 第 {g + 1}/{n} 组，本组处理 {len(selected)}/{len(accounts)} 个账号")
    return selected


def generate_balance_hash(balances: dict) -> str:
    """生成余额数据的hash"""
    simple_balances = {}
    if balances:
        for account_key, account_balances in balances.items():
            quota_list = []
            for _, balance_info in account_balances.items():
                quota_list.append(balance_info["quota"])
            simple_balances[account_key] = quota_list

    balance_json = json.dumps(simple_balances, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(balance_json.encode("utf-8")).hexdigest()[:16]


async def main():
    """主函数"""
    logger = setup_logging()

    # 检查依赖
    check_dependencies()

    # 验证环境变量
    if not validate_env_vars():
        logger.error("❌ 环境变量验证失败，程序退出")
        return 1

    logger.info("=" * 80)
    logger.info("🚀 Router平台多账号自动签到脚本 (重构版)")
    logger.info(f"🕒 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    # 加载应用配置
    app_config = AppConfig.load_from_env()
    logger.info(f"\n⚙️ 已加载 {len(app_config.providers)} 个 Provider 配置")
    for name, provider in app_config.providers.items():
        logger.info(f"   - {provider.name} ({name})")

    # 加载账号配置
    accounts = load_accounts()
    if not accounts:
        logger.error("\n❌ 未找到任何账号配置，程序退出")
        logger.info("💡 提示: 请配置 ANYROUTER_ACCOUNTS、AGENTROUTER_ACCOUNTS 或 ACCOUNTS 环境变量")
        return 1

    logger.info(f"\n⚙️ 找到 {len(accounts)} 个账号配置")

    # 按分组筛选账号（多 IP 分批签到，规避同 IP 风控）
    accounts = filter_account_group(accounts, logger)
    if not accounts:
        logger.error("\n❌ 当前分组没有分配到任何账号，程序退出")
        return 1

    # 验证账号配置
    valid_accounts = []
    for i, account in enumerate(accounts):
        if validate_account(account, i):
            valid_accounts.append(account)
            auth_methods = ", ".join([auth.method for auth in account.auth_configs])
            logger.info(f"   ✅ {account.name} ({account.provider}) - 认证方式: {auth_methods}")
        else:
            logger.warning(f"   ❌ {account.name} - 配置无效，跳过")

    if not valid_accounts:
        logger.error("\n❌ 没有有效的账号配置，程序退出")
        return 1

    logger.info(f"\n✅ 共 {len(valid_accounts)} 个账号通过验证\n")

    # 预检测代理可用性
    from utils.enhanced_stealth import ProxyManager
    if ProxyManager.should_use_proxy():
        logger.info("🔍 检测代理配置...")
        proxy_config = await ProxyManager.get_verified_proxy_config()
        if proxy_config:
            logger.info(f"✅ 代理可用: {proxy_config['server']}")
        else:
            logger.warning("⚠️ 代理不可用或未配置，将直接连接目标网站")
    else:
        logger.info("ℹ️ 代理未启用")

    # 加载余额hash
    last_balance_hash = load_balance_hash()

    # 执行签到
    success_count = 0
    total_count = 0
    notification_content = []
    current_balances = {}
    need_notify = False

    # 按平台分组统计
    platform_stats = {}

    for i, account in enumerate(valid_accounts):
        account_key = f"account_{i + 1}"
        provider = account.provider.upper()

        # 初始化平台统计
        if provider not in platform_stats:
            platform_stats[provider] = {
                'success': 0,
                'failed': 0,
                'total_quota': 0.0,
                'total_used': 0.0,
                'total_recharge': 0.0,
                'total_used_change': 0.0,
                'total_quota_change': 0.0,
                'accounts': []
            }

        try:
            # 获取 Provider 配置
            provider_config = app_config.get_provider(account.provider)
            if not provider_config:
                logger.error(f"❌ {account.name}: Provider '{account.provider}' 配置未找到")
                need_notify = True
                platform_stats[provider]['failed'] += 1
                platform_stats[provider]['accounts'].append({
                    'name': account.name,
                    'status': '❌',
                    'error': f"Provider '{account.provider}' 配置未找到",
                    'balance': None
                })
                continue

            logger.info(f"\n🌀 正在处理 {account.name} (使用 Provider '{account.provider}')")

            # 执行签到 - 使用async with管理浏览器生命周期
            async with CheckIn(account, provider_config) as checkin:
                results = await checkin.execute()

            total_count += len(results)

            # 处理多个认证方式的结果
            account_success = False
            successful_methods = []
            failed_methods = []
            this_account_balances = {}
            account_quota = 0.0
            account_used = 0.0
            account_recharge = 0.0
            account_used_change = 0.0
            account_quota_change = 0.0
            account_error = None

            for auth_method, success, user_info in results:
                if success:
                    # 计入成功方法与账号成功标记
                    account_success = True
                    success_count += 1
                    successful_methods.append(auth_method)

                    # 记录余额信息
                    if user_info and user_info.get("success"):
                        current_quota = user_info.get("quota", 0)
                        current_used = user_info.get("used", 0)
                        if current_quota is not None and current_used is not None:
                            this_account_balances[auth_method] = {
                                "quota": current_quota,
                                "used": current_used,
                            }
                            account_quota = max(account_quota, current_quota)
                            account_used = max(account_used, current_used)

                        # 记录余额变化
                        if user_info.get("balance_change"):
                            change = user_info["balance_change"]
                            account_recharge += change.get("recharge", 0)
                            account_used_change += change.get("used_change", 0)
                            account_quota_change += change.get("quota_change", 0)
                else:
                    # 仅在认证/签到失败时计入失败方法
                    failed_methods.append(auth_method)
                    if not account_error:  # 记录第一个错误
                        account_error = user_info.get("error", "Unknown error") if user_info else "Unknown error"

            if account_success:
                current_balances[account_key] = this_account_balances
                platform_stats[provider]['success'] += 1
                platform_stats[provider]['total_quota'] += account_quota
                platform_stats[provider]['total_used'] += account_used
                platform_stats[provider]['total_recharge'] += account_recharge
                platform_stats[provider]['total_used_change'] += account_used_change
                platform_stats[provider]['total_quota_change'] += account_quota_change
            else:
                platform_stats[provider]['failed'] += 1

            # 记录账号信息
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '✅' if account_success else '❌',
                'auth_method': successful_methods[0] if successful_methods else (failed_methods[0] if failed_methods else 'unknown'),
                'quota': account_quota if account_success else None,
                'used': account_used if account_success else None,
                'recharge': account_recharge if account_recharge != 0 else None,
                'used_change': account_used_change if account_used_change != 0 else None,
                'quota_change': account_quota_change if account_quota_change != 0 else None,
                'error': account_error if not account_success else None
            })

            # 如果所有认证方式都失败，需要通知
            if not account_success and results:
                need_notify = True
                logger.warning(f"🔔 {account.name} 所有认证方式都失败，将发送通知")

            # 如果有部分失败，也通知
            if failed_methods and successful_methods:
                need_notify = True
                logger.warning(f"🔔 {account.name} 有部分认证方式失败，将发送通知")

        except asyncio.TimeoutError as e:
            error_msg = f"{account.name} 操作超时: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"超时: {str(e)[:60]}",
                'balance': None
            })
        except httpx.ConnectError as e:
            error_msg = f"{account.name} 无法连接到服务器: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"连接失败: {str(e)[:60]}",
                'balance': None
            })
        except httpx.TimeoutException as e:
            error_msg = f"{account.name} HTTP请求超时: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"请求超时: {str(e)[:60]}",
                'balance': None
            })
        except ValueError as e:
            error_msg = f"{account.name} 配置或数据异常: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"配置异常: {str(e)[:60]}",
                'balance': None
            })
        except (KeyError, TypeError, AttributeError) as e:
            error_msg = f"{account.name} 数据处理异常: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"数据处理异常: {str(e)[:60]}",
                'balance': None
            })
        except (IOError, OSError) as e:
            error_msg = f"{account.name} 文件或系统异常: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"系统异常: {str(e)[:60]}",
                'balance': None
            })
        except Exception as e:
            # 捕获所有其他未预期的异常（作为安全网）
            error_msg = f"{account.name} 未知异常: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            need_notify = True
            platform_stats[provider]['failed'] += 1
            platform_stats[provider]['accounts'].append({
                'name': account.name,
                'status': '❌',
                'error': f"未知异常: {str(e)[:60]}",
                'balance': None
            })

    # 检查余额变化
    current_balance_hash = generate_balance_hash(current_balances) if current_balances else None
    logger.info(f"\n\nℹ️ 当前余额 hash: {current_balance_hash}, 上次余额 hash: {last_balance_hash}")

    if current_balance_hash:
        if last_balance_hash is None:
            # 首次运行
            need_notify = True
            logger.info("🔔 首次运行检测到，将发送通知")
        elif current_balance_hash != last_balance_hash:
            # 余额有变化
            need_notify = True
            logger.info("🔔 余额变化检测到，将发送通知")
        else:
            logger.info("ℹ️ 余额无变化")

    # 保存当前余额hash
    if current_balance_hash:
        save_balance_hash(current_balance_hash)

    # 发送通知
    group_suffix = get_account_group_suffix()
    if group_suffix:
        # 分组模式：不单独发通知，把本组结果写入文件，
        # 由汇总任务（summary_notify.py）下载所有分组结果后合并成单条通知发送。
        result_file = f"checkin_result{group_suffix}.json"
        result_payload = {
            "group": os.getenv("ACCOUNT_GROUP"),
            "need_notify": need_notify,
            "platform_stats": platform_stats,
        }
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result_payload, f, ensure_ascii=False, indent=2)
            logger.info(f"📝 本组结果已写入 {result_file}（need_notify={need_notify}），通知将由汇总任务统一发送")
        except (IOError, OSError) as e:
            logger.error(f"❌ 写入分组结果失败: {e}")
    elif need_notify and platform_stats:
        notify_content = render_notification(platform_stats)
        logger.info("\n" + notify_content)
        notify.push_message("Router签到提醒", notify_content, msg_type="text")
        logger.info("\n🔔 通知已发送")
    else:
        # 区分无余额数据和余额无变化两种情况
        if current_balance_hash:
            logger.info("\nℹ️ 所有账号成功且余额无变化，跳过通知")
        else:
            logger.info("\nℹ️ 所有账号成功（未获取到余额数据），跳过通知")

    logger.info("\n" + "=" * 80)
    total_success_accounts = sum(p['success'] for p in platform_stats.values())
    total_accounts = sum(p['success'] + p['failed'] for p in platform_stats.values())
    logger.info(f"✅ 程序执行完成 - 成功: {total_success_accounts}/{total_accounts} 个账号")
    logger.info("=" * 80)

    # 设置退出码
    sys.exit(0 if total_success_accounts > 0 else 1)


def run_main():
    """运行主函数的包装函数"""
    logger = logging.getLogger(__name__)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        msg = "程序被用户中断"
        logger.warning(msg)
        sys.exit(1)
    except Exception as e:
        msg = f"程序执行出错: {e}"
        logger.error(msg, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run_main()
