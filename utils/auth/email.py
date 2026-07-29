"""
邮箱密码认证器 - 使用用户名和密码进行表单登录
"""

from typing import Dict, Any, Optional, Tuple
from playwright.async_api import Page, BrowserContext

from utils.auth.base import Authenticator, logger
from utils.sanitizer import sanitize_exception
from utils.session_cache import SessionCache
from utils.constants import (
    EMAIL_INPUT_SELECTORS,
    PASSWORD_INPUT_SELECTORS,
    LOGIN_BUTTON_SELECTORS,
    POPUP_CLOSE_SELECTORS,
    TimeoutConfig,
)

# 会话缓存实例
session_cache = SessionCache()


class EmailAuthenticator(Authenticator):
    """邮箱密码认证"""

    async def _close_popups(self, page: Page):
        """关闭可能的弹窗"""
        try:
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(TimeoutConfig.VERY_SHORT_WAIT)
            for sel in POPUP_CLOSE_SELECTORS:
                try:
                    close_btn = await page.query_selector(sel)
                    if close_btn:
                        await close_btn.click()
                        await page.wait_for_timeout(TimeoutConfig.VERY_SHORT_WAIT)
                        break
                except:
                    continue
        except:
            pass

    async def _find_and_click_email_tab(self, page: Page) -> bool:
        """查找并点击邮箱登录选项"""
        logger.info(f"🔍 [{self.auth_config.username}] 查找邮箱登录选项...")

        # 等待页面交互元素就绪
        try:
            await page.wait_for_timeout(1500)
        except:
            pass

        for sel in [
            'button:has-text("邮箱")',
            'a:has-text("邮箱")',
            'button:has-text("Email")',
            'a:has-text("Email")',
            'text=邮箱登录',
            'text=Email Login',
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    logger.info(f"✅ [{self.auth_config.username}] 找到邮箱登录选项: {sel}")
                    await el.click()
                    await page.wait_for_timeout(800)
                    return True
            except:
                continue
        return False

    async def _find_email_input(self, page: Page):
        """查找邮箱输入框"""
        logger.info(f"🔍 [{self.auth_config.username}] 查找邮箱输入框...")
        email_input = None
        for sel in EMAIL_INPUT_SELECTORS:
            try:
                email_input = await page.query_selector(sel)
                if email_input:
                    logger.info(f"✅ [{self.auth_config.username}] 找到邮箱输入框: {sel}")
                    return email_input
            except:
                continue

        # 调试信息
        if not email_input:
            await self._debug_page_inputs(page)
        return None

    async def _debug_page_inputs(self, page: Page):
        """输出调试信息"""
        try:
            page_title = await page.title()
            page_url = page.url
            logger.error(f"❌ [{self.auth_config.username}] 邮箱输入框未找到")
            logger.info(f"   当前页面: {page_title}")
            logger.info(f"   当前URL: {page_url}")

            # 查找所有输入框
            all_inputs = await page.query_selector_all('input')
            logger.info(f"   页面共有 {len(all_inputs)} 个输入框")
            for i, inp in enumerate(all_inputs[:5]):  # 只显示前5个
                try:
                    inp_type = await inp.get_attribute('type')
                    inp_name = await inp.get_attribute('name')
                    inp_placeholder = await inp.get_attribute('placeholder')
                    logger.info(f"     输入框{i+1}: type={inp_type}, name={inp_name}, placeholder={inp_placeholder}")
                except:
                    logger.info(f"     输入框{i+1}: 无法获取属性")
        except Exception as e:
            logger.info(f"   调试信息获取失败: {e}")

    async def _find_and_click_login_button(self, page: Page):
        """查找并点击登录按钮"""
        for sel in LOGIN_BUTTON_SELECTORS:
            try:
                login_button = await page.query_selector(sel)
                if login_button:
                    return login_button
            except:
                continue
        return None

    async def _check_login_success(self, page: Page) -> Tuple[bool, Optional[str]]:
        """检查登录是否成功"""
        current_url = page.url
        logger.info(f"🔍 [{self.auth_config.username}] 登录后URL: {current_url}")

        # 方法1: 检查URL变化
        if "login" not in current_url.lower():
            logger.info(f"✅ [{self.auth_config.username}] URL已变化，登录可能成功")
            return True, None

        logger.warning(f"⚠️ [{self.auth_config.username}] 仍在登录页面，检查其他登录指标...")

        # 方法2: 检查页面标题
        try:
            page_title = await page.title()
            logger.info(f"🔍 [{self.auth_config.username}] 页面标题: {page_title}")
            if "login" not in page_title.lower() and "console" in page_title.lower():
                logger.info(f"✅ [{self.auth_config.username}] 页面标题显示已登录")
                return True, None
        except:
            pass

        # 方法3: 检查用户界面元素
        try:
            user_elements = await page.query_selector_all(
                '[class*="user"], [class*="avatar"], [class*="profile"], button:has-text("退出"), button:has-text("Logout")'
            )
            if user_elements:
                logger.info(f"✅ [{self.auth_config.username}] 找到用户界面元素，登录成功")
                return True, None
        except:
            pass

        # 方法4: 检查错误提示
        error_msg = await self._check_error_messages(page)
        if error_msg:
            return False, error_msg

        # 仍在登录页
        if "login" in current_url.lower():
            return False, "Login failed - still on login page (may need captcha)"

        return True, None

    async def _check_error_messages(self, page: Page) -> Optional[str]:
        """检查错误提示信息"""
        try:
            error_selectors = ['.error', '.alert-danger', '[class*="error"]', '.toast-error', '[role="alert"]']
            for sel in error_selectors:
                error_msg = await page.query_selector(sel)
                if error_msg:
                    try:
                        error_text = await error_msg.inner_text()
                        if error_text and error_text.strip():
                            # 检查是否是成功消息
                            success_keywords = ['成功', 'success', '登录成功', 'login success']
                            error_keywords = ['失败', '错误', 'error', 'invalid', 'incorrect', '验证码', 'captcha']

                            error_text_lower = error_text.lower()
                            is_success = any(keyword in error_text_lower for keyword in success_keywords)
                            is_real_error = any(keyword in error_text_lower for keyword in error_keywords)

                            if is_real_error:
                                logger.error(f"❌ [{self.auth_config.username}] 登录错误: {error_text}")
                                return f"Login failed: {error_text}"
                            elif is_success:
                                logger.info(f"✅ [{self.auth_config.username}] 检测到成功消息: {error_text}")
                            else:
                                logger.warning(f"⚠️ [{self.auth_config.username}] 检测到消息: {error_text}")
                    except:
                        pass
        except:
            pass
        return None

    async def _try_api_login(self, page: Page) -> Dict[str, Any]:
        """尝试直接调用登录 API（new-api 站点未开启验证码时可用，比表单填写更稳定）

        在浏览器上下文中执行 fetch，session cookie 会自动写入浏览器。
        AgentRouter 等站点登录即完成签到，此路径可完全避开表单选择器失配问题。

        Returns:
            {"status": "ok", "user_id": ..., "username": ...}  API登录成功
            {"status": "auth_failed", "message": ...}          凭据错误，表单登录也不会成功
            {"status": "unavailable"}                          API不可用，回退表单登录
        """
        login_api_url = f"{self.provider_config.base_url}/api/user/login"
        logger.info(f"🚀 [{self.auth_config.username}] 尝试 API 直接登录: {login_api_url}")
        try:
            result = await page.evaluate(
                """
                async ({url, username, password}) => {
                    try {
                        const resp = await fetch(url, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            credentials: 'include',
                            body: JSON.stringify({username, password})
                        });
                        const contentType = resp.headers.get('content-type') || '';
                        if (!contentType.includes('application/json')) {
                            return {status: resp.status, nonJson: true};
                        }
                        return {status: resp.status, data: await resp.json()};
                    } catch (e) {
                        return {status: 0, error: e.message};
                    }
                }
                """,
                {
                    "url": login_api_url,
                    "username": self.auth_config.username,
                    "password": self.auth_config.password,
                },
            )
        except Exception as e:
            logger.warning(f"⚠️ [{self.auth_config.username}] API 登录请求异常: {sanitize_exception(e)}")
            return {"status": "unavailable"}

        if result.get("error") or result.get("nonJson") or result.get("status") != 200:
            logger.info(f"ℹ️ [{self.auth_config.username}] API 登录不可用 (HTTP {result.get('status')})，回退表单登录")
            return {"status": "unavailable"}

        data = result.get("data") or {}
        if data.get("success"):
            user_data = data.get("data") or {}
            user_id = user_data.get("id")
            username = user_data.get("username")
            logger.info(f"✅ [{self.auth_config.username}] API 登录成功，用户ID: {user_id}")
            return {
                "status": "ok",
                "user_id": str(user_id) if user_id else None,
                "username": username,
            }

        message = data.get("message", "Login failed")
        logger.error(f"❌ [{self.auth_config.username}] API 登录失败: {message}")
        return {"status": "auth_failed", "message": message}

    async def _finalize_login(
        self, page: Page, context: BrowserContext,
        user_id: Optional[str] = None, username: Optional[str] = None
    ) -> Dict[str, Any]:
        """登录成功后的收尾：提取cookies、补齐用户标识、缓存会话"""
        final_cookies = await context.cookies()
        cookies_dict = {cookie["name"]: cookie["value"] for cookie in final_cookies}

        if "session" not in cookies_dict and "sessionid" not in cookies_dict:
            logger.warning(f"⚠️ [{self.auth_config.username}] 未找到session cookie")

        logger.info(f"✅ [{self.auth_config.username}] 邮箱认证完成，获取到 {len(cookies_dict)} 个cookies")

        # 优先使用登录响应中的用户ID，其次从localStorage提取，失败则尝试API
        if not user_id:
            user_id, username = await self._extract_user_from_localstorage(page)
            if not user_id:
                logger.info(f"ℹ️ [{self.auth_config.username}] localStorage未获取到用户ID，尝试API")
                user_id, username = await self._extract_user_info(page, cookies_dict)

        # AgentRouter 登录即签到，每次直接账号密码登录，无需缓存会话
        if self.provider_config.name.lower() == "agentrouter":
            logger.info(f"ℹ️ [{self.auth_config.username}] AgentRouter 不缓存会话，下次直接重新登录")
            return {"success": True, "cookies": cookies_dict, "user_id": user_id, "username": username}

        # 保存会话缓存
        try:
            session_cache.save(
                account_name=self.account_name,
                provider=self.provider_config.name,
                cookies=final_cookies,
                user_id=user_id,
                username=username,
                expiry_hours=24
            )
            logger.info(f"✅ [{self.auth_config.username}] 会话已缓存（24小时有效）")
        except Exception as cache_error:
            logger.warning(f"⚠️ [{self.auth_config.username}] 缓存保存失败: {cache_error}")

        return {"success": True, "cookies": cookies_dict, "user_id": user_id, "username": username}

    async def authenticate(self, page: Page, context: BrowserContext) -> Dict[str, Any]:
        """使用邮箱密码登录"""
        try:
            logger.info(f"ℹ️ Starting Email authentication")

            if not await self._init_page_and_check_cloudflare(page):
                return {"success": False, "error": "Cloudflare verification timeout"}

            await self._close_popups(page)

            # 快速路径：直接调用登录API（AgentRouter等未开验证码的new-api站点）
            api_result = await self._try_api_login(page)
            if api_result["status"] == "ok":
                return await self._finalize_login(
                    page, context, api_result.get("user_id"), api_result.get("username")
                )
            if api_result["status"] == "auth_failed":
                return {"success": False, "error": f"Login failed: {api_result['message']}"}

            # 回退路径：浏览器表单登录
            await self._find_and_click_email_tab(page)
            await page.wait_for_timeout(TimeoutConfig.SHORT_WAIT_2)

            email_input = await self._find_email_input(page)
            if not email_input:
                return {"success": False, "error": "Email input field not found"}

            password_input = await page.query_selector('input[type="password"]')
            if not password_input:
                return {"success": False, "error": "Password input field not found"}

            await email_input.fill(self.auth_config.username)

            error = await self._fill_password(password_input)
            if error:
                return {"success": False, "error": error}

            login_button = await self._find_and_click_login_button(page)
            if not login_button:
                return {"success": False, "error": "Login button not found"}

            logger.info(f"🔑 [{self.auth_config.username}] 点击登录按钮...")
            await login_button.click()

            try:
                await page.wait_for_load_state("networkidle", timeout=TimeoutConfig.MEDIUM_WAIT_10)
                await page.wait_for_timeout(TimeoutConfig.SHORT_WAIT_2)
            except Exception:
                logger.warning(f"⚠️ [{self.auth_config.username}] 页面加载超时，继续检查登录状态...")

            success, error_msg = await self._check_login_success(page)
            if not success:
                return {"success": False, "error": error_msg}

            return await self._finalize_login(page, context)

        except Exception as e:
            return {"success": False, "error": f"Email auth failed: {sanitize_exception(e)}"}
