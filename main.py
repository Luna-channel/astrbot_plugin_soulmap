import json
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api import AstrBotConfig
from astrbot.core.message.components import Plain

logger = logging.getLogger("SoulMap")


class SoulMapManager:
    """
    用户画像管理系统 (SoulMap)
    - 所有字段统一为字符串类型，AI负责数据格式管理
    - 备注字段特殊处理：追加模式，保留最近N条
    """

    def __init__(self, data_path: Path, allowed_fields: list, max_notes_count: int = 5):
        self.data_path = data_path
        self.allowed_fields = allowed_fields
        self.max_notes_count = max_notes_count
        self._init_path()
        self.user_data = self._load_data("user_profiles.json")

    def _init_path(self):
        self.data_path.mkdir(parents=True, exist_ok=True)

    def _load_data(self, filename: str) -> Dict[str, Any]:
        path = self.data_path / filename
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_data(self):
        path = self.data_path / "user_profiles.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.user_data, f, ensure_ascii=False, indent=2)

    def _get_user_key(self, user_id: str, session_id: Optional[str] = None) -> str:
        return f"{session_id}_{user_id}" if session_id else user_id

    def get_user_profile(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        key = self._get_user_key(user_id, session_id)
        return self.user_data.get(key, {}).copy()

    def update_field(self, user_id: str, field: str, value: str, 
                     session_id: Optional[str] = None) -> tuple:
        """更新字段值，备注字段特殊处理"""
        if field not in self.allowed_fields:
            return False, f"字段 '{field}' 不在允许列表中"

        key = self._get_user_key(user_id, session_id)
        if key not in self.user_data:
            self.user_data[key] = {}

        value = value.strip()
        
        # 备注字段特殊处理：追加模式，保留最近N条
        if field == "备注":
            existing = self.user_data[key].get("备注", "")
            # 解析现有备注（以顿号或分号分隔）
            if existing:
                notes = [n.strip() for n in re.split(r'[；;]', existing) if n.strip()]
            else:
                notes = []
            # 解析新备注
            new_notes = [n.strip()[:20] for n in re.split(r'[；;]', value) if n.strip()]
            # 去重并追加
            for note in new_notes:
                if note not in notes:
                    notes.append(note)
            # 保留最近N条
            notes = notes[-self.max_notes_count:]
            value = "；".join(notes)
        
        self.user_data[key][field] = value
        self.user_data[key]["_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self._save_data()
        return True, f"已更新 {field}"

    def delete_field(self, user_id: str, field: str, session_id: Optional[str] = None) -> tuple:
        """删除字段或备注条目（支持数字索引）"""
        key = self._get_user_key(user_id, session_id)
        if key not in self.user_data:
            return False, "没有找到你的画像数据"

        # 1. 精确匹配字段名
        if field in self.user_data[key]:
            del self.user_data[key][field]
            self.user_data[key]["_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_data()
            return True, f"已删除字段 {field}"

        # 2. 数字索引：删除备注中的第N条
        if "备注" in self.user_data[key] and field.isdigit():
            idx = int(field) - 1  # 转为0索引
            notes = [n.strip() for n in re.split(r'[；;]', self.user_data[key]["备注"]) if n.strip()]
            if 0 <= idx < len(notes):
                deleted_note = notes.pop(idx)
                if notes:
                    self.user_data[key]["备注"] = "；".join(notes)
                else:
                    del self.user_data[key]["备注"]
                self.user_data[key]["_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_data()
                return True, f"已删除备注第{field}条：{deleted_note}"
            return False, f"备注第{field}条不存在"

        # 3. 模糊匹配：在备注中搜索并删除包含该内容的条目
        if "备注" in self.user_data[key]:
            notes = [n.strip() for n in re.split(r'[；;]', self.user_data[key]["备注"]) if n.strip()]
            new_notes = [n for n in notes if field not in n]
            if len(new_notes) < len(notes):
                if new_notes:
                    self.user_data[key]["备注"] = "；".join(new_notes)
                else:
                    del self.user_data[key]["备注"]
                self.user_data[key]["_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_data()
                return True, f"已从备注中删除包含 '{field}' 的条目"

        return False, f"未找到字段或备注条目 '{field}'"

    def clear_profile(self, user_id: str, session_id: Optional[str] = None) -> bool:
        key = self._get_user_key(user_id, session_id)
        if key in self.user_data:
            del self.user_data[key]
            self._save_data()
            return True
        return False

    def format_profile_summary(self, user_id: str, session_id: Optional[str] = None) -> str:
        """格式化用户画像摘要"""
        profile = self.get_user_profile(user_id, session_id)
        if not profile:
            return "暂无记录"

        lines = []
        for field in self.allowed_fields:
            if field in profile and profile[field]:
                # 备注字段按条显示：1.xxx 2.xxx
                if field == "备注":
                    notes = [n.strip() for n in re.split(r'[；;]', profile[field]) if n.strip()]
                    notes_display = " ".join([f"{i}.{note}" for i, note in enumerate(notes, 1)])
                    lines.append(f"- 备注：{notes_display}")
                else:
                    lines.append(f"- {field}：{profile[field]}")

        return "\n".join(lines) if lines else "暂无记录"

    def export_all_profiles(self) -> Dict[str, Any]:
        return self.user_data.copy()


@register("SoulMap", "柯尔", "AI驱动的用户画像收集系统，简洁设计，AI负责数据管理", "1.1.0")
class SoulMapPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = StarTools.get_data_dir()

        # 从配置读取字段列表，直接使用
        allowed_fields = self.config.get("allowed_fields", [
            "昵称", "性别", "年龄", "所在地", "生日", "爱吃", "忌口",
            "爱好", "职业", "重要节日", "恐惧/弱点", "作息规律", "技能水平",
            "健康状况", "宠物", "备注"
        ])
        max_notes_count = self.config.get("max_notes_count", 5)

        self.manager = SoulMapManager(data_dir, allowed_fields, max_notes_count)

        # 配置日志
        log_level = self.config.get("debug_log_level", "INFO")
        if isinstance(log_level, str):
            log_level = getattr(logging, log_level.upper(), logging.INFO)
        logger.setLevel(log_level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)

        # 正则模式（支持中文字段名）
        self.profile_pattern = re.compile(r"\[Profile:\s*([^\]]+)\]", re.IGNORECASE)
        # 支持多字段删除: [ProfileDelete: 字段1, 字段2] 或 [ProfileDelete: 字段]
        self.delete_pattern = re.compile(r"\[ProfileDelete:\s*([^\]]+)\]", re.IGNORECASE)
        self.block_pattern = re.compile(r"\s*\[(?:Profile|ProfileDelete):[^\]]*\]\s*", re.IGNORECASE)

        self._install_global_interceptors(context)

    @property
    def session_based(self) -> bool:
        return bool(self.config.get("session_based", False))

    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        return event.unified_msg_origin if self.session_based else None

    def _install_global_interceptors(self, context: Context):
        """安装全局拦截器"""
        plugin_self = self

        original_send_message = context.send_message

        async def patched_send_message(session, message_chain):
            try:
                if message_chain and hasattr(message_chain, 'chain') and message_chain.chain:
                    for comp in message_chain.chain:
                        if isinstance(comp, Plain) and comp.text:
                            original_text = comp.text
                            cleaned_text = plugin_self.block_pattern.sub('', original_text).strip()
                            if cleaned_text != original_text:
                                comp.text = cleaned_text
            except Exception as e:
                logger.warning(f"[SoulMap] send_message 拦截器异常: {e}")
            return await original_send_message(session, message_chain)

        context.send_message = patched_send_message
        logger.info("[SoulMap] 已安装全局拦截器")

        # 注意：不在 patched_text_chat 中清理标签！
        # 因为 patched_text_chat 在 on_llm_response 钩子之前执行
        # 如果在这里清理，钩子就解析不到 [Profile:] 内容了
        # 清理工作统一由 on_llm_response 和 on_decorating_result 完成
        def wrap_provider_text_chat(provider):
            if hasattr(provider, '_soulmap_wrapped'):
                return
            provider._soulmap_wrapped = True

        try:
            for provider in context.get_all_providers():
                wrap_provider_text_chat(provider)
            logger.info(f"[SoulMap] 已为 {len(context.get_all_providers())} 个 Provider 安装拦截器")
        except Exception as e:
            logger.warning(f"[SoulMap] 安装 Provider 拦截器出错: {e}")

        self._wrap_provider_text_chat = wrap_provider_text_chat
        self._original_send_message = original_send_message

    def _get_allowed_fields_display(self) -> str:
        """生成可用字段的显示字符串"""
        return "/".join(self.manager.allowed_fields)

    @filter.on_llm_request()
    async def add_profile_context(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入画像信息"""
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)

        profile_summary = self.manager.format_profile_summary(user_id, session_id)
        allowed_fields_display = self._get_allowed_fields_display()
        max_notes_count = str(self.config.get("max_notes_count", 5))
        
        profile_prompt = self.config.get("profile_prompt", "")
        
        if profile_prompt:
            try:
                profile_prompt = profile_prompt.format(
                    profile_summary=profile_summary,
                    allowed_fields_display=allowed_fields_display,
                    max_notes_count=max_notes_count
                )
            except KeyError:
                # 兼容旧格式，使用replace
                profile_prompt = profile_prompt.replace("{profile_summary}", profile_summary)
                profile_prompt = profile_prompt.replace("{allowed_fields_display}", allowed_fields_display)
                profile_prompt = profile_prompt.replace("{max_notes_count}", max_notes_count)
            req.system_prompt += f"\n{profile_prompt}"

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """解析并更新画像"""
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        original_text = resp.completion_text or ""

        logger.debug(f"[SoulMap] on_llm_resp 被调用 - 用户: {user_id}, session_id: {session_id}, session_based: {self.session_based}")
        logger.debug(f"[SoulMap] 原始文本长度: {len(original_text)}")

        if not original_text:
            logger.debug("[SoulMap] 原始文本为空，直接返回")
            return

        # 处理更新
        profile_matches = self.profile_pattern.findall(original_text)
        logger.debug(f"[SoulMap] 匹配到的 Profile 块数量: {len(profile_matches)}")
        
        for match in profile_matches:
            logger.debug(f"[SoulMap] 解析 Profile 块: {match[:100]}...")
            pairs = re.findall(r'([\w\u4e00-\u9fff]+)\s*=\s*([^,\]]+)', match)
            logger.debug(f"[SoulMap] 解析到的字段对: {pairs}")
            for field, value in pairs:
                field = field.strip()
                value = value.strip()
                success, msg = self.manager.update_field(user_id, field, value, session_id)
                if success:
                    logger.info(f"[SoulMap] {user_id} 更新成功: {field}={value}")
                else:
                    logger.warning(f"[SoulMap] {user_id} 更新失败: {field}={value}, 原因: {msg}")

        # 处理删除（支持多字段：用逗号/分号/、分割）
        for match in self.delete_pattern.findall(original_text):
            # 分割多个字段名
            fields = [f.strip() for f in re.split(r'[,，;；、]', match) if f.strip()]
            
            # 数字索引从大到小排序，避免删除后索引错位
            # 分离数字和非数字
            digit_fields = sorted([f for f in fields if f.isdigit()], key=int, reverse=True)
            other_fields = [f for f in fields if not f.isdigit()]
            
            # 先删除非数字字段，再从大到小删除数字索引
            for field in other_fields + digit_fields:
                success, msg = self.manager.delete_field(user_id, field, session_id)
                if success:
                    logger.info(f"[SoulMap] {user_id} 删除成功: {field}")
                else:
                    logger.warning(f"[SoulMap] {user_id} 删除失败: {field}, 原因: {msg}")

        # 清理标签
        resp.completion_text = self.block_pattern.sub('', original_text).strip()
        if resp.result_chain and resp.result_chain.chain:
            for comp in resp.result_chain.chain:
                if isinstance(comp, Plain) and comp.text:
                    comp.text = self.block_pattern.sub('', comp.text).strip()

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """最后清理"""
        result = event.get_result()
        if result is None or not result.chain:
            return

        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                cleaned = self.block_pattern.sub('', comp.text).strip()
                if cleaned != comp.text:
                    comp.text = cleaned

    # ------------------- 用户命令 -------------------

    @filter.command("我的画像")
    async def show_my_profile(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)

        profile = self.manager.get_user_profile(user_id, session_id)
        if not profile:
            yield event.plain_result("暂时还没有记录内容，多和我聊聊吧")
            return

        summary = self.manager.format_profile_summary(user_id, session_id)
        last_updated = profile.get("_last_updated", "未知")
        yield event.plain_result(f"📋 你的画像：\n{summary}\n\n最后更新：{last_updated}")

    @filter.command("删除画像")
    async def delete_my_field(self, event: AstrMessageEvent, field: str):
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)

        field = field.strip()

        success, msg = self.manager.delete_field(user_id, field, session_id)
        if success:
            yield event.plain_result(f"✅ 已删除「{field}」")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.command("清空画像")
    async def clear_my_profile(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)

        success = self.manager.clear_profile(user_id, session_id)
        if success:
            yield event.plain_result("✅ 已清空你的所有画像数据")
        else:
            yield event.plain_result("你还没有任何画像数据")

    # ------------------- 管理员命令 -------------------

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return event.role == "admin"

    @filter.command("查询画像")
    async def admin_query_profile(self, event: AstrMessageEvent, user_id: str):
        if not self._is_admin(event):
            yield event.plain_result(self.config.get("admin_permission_denied_msg", "错误：此命令仅限管理员使用。"))
            return

        session_id = self._get_session_id(event)
        profile = self.manager.get_user_profile(user_id.strip(), session_id)

        if not profile:
            yield event.plain_result(f"用户 {user_id} 没有画像数据")
            return

        summary = self.manager.format_profile_summary(user_id.strip(), session_id)
        last_updated = profile.get("_last_updated", "未知")
        yield event.plain_result(f"📋 用户 {user_id} 的画像：\n{summary}\n\n最后更新：{last_updated}")

    @filter.command("导出画像")
    async def admin_export_profiles(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield event.plain_result(self.config.get("admin_permission_denied_msg", "错误：此命令仅限管理员使用。"))
            return

        all_profiles = self.manager.export_all_profiles()
        if not all_profiles:
            yield event.plain_result("暂无任何用户画像数据")
            return

        user_count = len(all_profiles)
        response = f"📊 画像统计：共 {user_count} 个用户\n\n"

        for i, (user_key, profile) in enumerate(all_profiles.items()):
            if i >= 10:
                response += f"... 还有 {user_count - 10} 个用户"
                break
            nickname = profile.get("nickname", "未知")
            response += f"• {user_key}: {nickname}\n"

        yield event.plain_result(response)

    @filter.command("画像统计")
    async def admin_profile_stats(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield event.plain_result(self.config.get("admin_permission_denied_msg", "错误：此命令仅限管理员使用。"))
            return

        all_profiles = self.manager.export_all_profiles()
        user_count = len(all_profiles)

        field_counts = {}
        for profile in all_profiles.values():
            for field in profile:
                if not field.startswith("_"):
                    field_counts[field] = field_counts.get(field, 0) + 1

        response = f"📊 画像系统统计\n\n总用户数：{user_count}\n\n字段填充情况：\n"

        for field in self.manager.allowed_fields:
            count = field_counts.get(field, 0)
            rate = (count / user_count * 100) if user_count > 0 else 0
            response += f"• {field}: {count} ({rate:.1f}%)\n"

        yield event.plain_result(response)

    async def terminate(self):
        self.manager._save_data()

        if hasattr(self, '_original_send_message') and self._original_send_message:
            try:
                self.context.send_message = self._original_send_message
            except Exception as e:
                logger.warning(f"[SoulMap] 恢复 send_message 失败: {e}")

        try:
            for provider in self.context.get_all_providers():
                if hasattr(provider, '_soulmap_wrapped'):
                    delattr(provider, '_soulmap_wrapped')
        except Exception as e:
            logger.warning(f"[SoulMap] 清理 Provider 标记失败: {e}")
