#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent管理器 - 专注于会话管理和API调用
使用AgentRegistry进行Agent注册和发现
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

# 导入AgentRegistry
from mcpserver.agent_registry import get_agent_registry, AgentConfig

# 配置日志
logger = logging.getLogger("AgentManager")
logger.setLevel(logging.INFO)

# 如果没有处理器，添加一个
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# 屏蔽HTTP库的DEBUG日志
logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore.connection").setLevel(logging.WARNING)

@dataclass
class AgentSession:
    """Agent会话类"""
    timestamp: float = field(default_factory=time.time)
    history: List[Dict[str, str]] = field(default_factory=list)
    session_id: str = "default_user_session"

class AgentManager:
    """Agent管理器 - 专注于会话管理和API调用"""
    
    def __init__(self):
        """初始化Agent管理器"""
        self.agent_sessions: Dict[str, Dict[str, AgentSession]] = {}
        self.max_history_rounds = 7  # 最大历史轮数
        self.context_ttl_hours = 24  # 上下文TTL（小时）
        self.debug_mode = True
        
        # 启动定期清理任务（只在事件循环中启动）
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self._periodic_cleanup())
        except RuntimeError:
            # 没有运行的事件循环，跳过定期清理任务
            pass
        
        logger.info("AgentManager初始化完成")
    

    
    def get_agent_session_history(self, agent_name: str, session_id: str = 'default_user_session') -> List[Dict[str, str]]:
        """获取Agent会话历史"""
        if agent_name not in self.agent_sessions:
            self.agent_sessions[agent_name] = {}
        
        agent_sessions = self.agent_sessions[agent_name]
        if session_id not in agent_sessions or self._is_context_expired(agent_sessions[session_id].timestamp):
            agent_sessions[session_id] = AgentSession(session_id=session_id)
        
        return agent_sessions[session_id].history
    
    def update_agent_session_history(self, agent_name: str, user_message: str, assistant_message: str, session_id: str = 'default_user_session'):
        """更新Agent会话历史"""
        if agent_name not in self.agent_sessions:
            self.agent_sessions[agent_name] = {}
        
        agent_sessions = self.agent_sessions[agent_name]
        if session_id not in agent_sessions or self._is_context_expired(agent_sessions[session_id].timestamp):
            agent_sessions[session_id] = AgentSession(session_id=session_id)
        
        session_data = agent_sessions[session_id]
        session_data.history.extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message}
        ])
        session_data.timestamp = time.time()
        
        # 限制历史消息数量
        max_messages = self.max_history_rounds * 2
        if len(session_data.history) > max_messages:
            session_data.history = session_data.history[-max_messages:]
    
    def _is_context_expired(self, timestamp: float) -> bool:
        """检查上下文是否过期"""
        return (time.time() - timestamp) > (self.context_ttl_hours * 3600)
    
    async def _periodic_cleanup(self):
        """定期清理过期的会话上下文"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时清理一次
                
                if self.debug_mode:
                    logger.debug("执行定期上下文清理...")
                
                for agent_name, sessions in list(self.agent_sessions.items()):
                    for session_id, session_data in list(sessions.items()):
                        if self._is_context_expired(session_data.timestamp):
                            sessions.pop(session_id, None)
                            if self.debug_mode:
                                logger.debug(f"清理过期上下文: {agent_name}, session {session_id}")
                    
                    if not sessions:
                        self.agent_sessions.pop(agent_name, None)
                        
            except Exception as e:
                logger.error(f"定期清理任务出错: {e}")
    
    def _replace_placeholders(self, text: str, agent_config: AgentConfig) -> str:
        """替换提示词中的占位符，支持Agent配置和环境变量"""
        if not text:
            return ""
        
        processed_text = str(text)
        
        # Agent配置相关的占位符替换
        if agent_config:
            # 基础Agent信息
            processed_text = processed_text.replace("{{AgentName}}", agent_config.name)
            processed_text = processed_text.replace("{{MaidName}}", agent_config.name)
            processed_text = processed_text.replace("{{BaseName}}", agent_config.base_name)
            processed_text = processed_text.replace("{{Description}}", agent_config.description)
            processed_text = processed_text.replace("{{ModelId}}", agent_config.id)
            
            # 配置参数
            processed_text = processed_text.replace("{{Temperature}}", str(agent_config.temperature))
            processed_text = processed_text.replace("{{MaxTokens}}", str(agent_config.max_output_tokens))
            processed_text = processed_text.replace("{{ModelProvider}}", agent_config.model_provider)
        
        # 环境变量占位符替换
        import os
        import re
        
        # 匹配 {{ENV_VAR_NAME}} 格式的环境变量
        env_pattern = r'\{\{([A-Z_][A-Z0-9_]*)\}\}'
        for match in re.finditer(env_pattern, processed_text):
            env_var_name = match.group(1)
            env_value = os.getenv(env_var_name, '')
            processed_text = processed_text.replace(f"{{{{{env_var_name}}}}}", env_value)
        
        # 时间相关占位符
        from datetime import datetime
        now = datetime.now()
        processed_text = processed_text.replace("{{CurrentTime}}", now.strftime("%H:%M:%S"))
        processed_text = processed_text.replace("{{CurrentDate}}", now.strftime("%Y-%m-%d"))
        processed_text = processed_text.replace("{{CurrentDateTime}}", now.strftime("%Y-%m-%d %H:%M:%S"))
        
        return processed_text
    
    def _build_system_message(self, agent_config: AgentConfig) -> Dict[str, str]:
        """构建系统消息，包含Agent的身份、行为、风格等"""
        # 处理系统提示词中的占位符
        processed_system_prompt = self._replace_placeholders(agent_config.system_prompt, agent_config)
        
        return {
            "role": "system",
            "content": processed_system_prompt
        }
    
    def _build_user_message(self, prompt: str, agent_config: AgentConfig) -> Dict[str, str]:
        """构建用户消息，处理用户输入"""
        # 处理用户提示词中的占位符
        processed_prompt = self._replace_placeholders(prompt, agent_config)
        
        return {
            "role": "user",
            "content": processed_prompt
        }
    
    def _build_assistant_message(self, content: str) -> Dict[str, str]:
        """构建助手消息"""
        return {
            "role": "assistant",
            "content": content
        }
    
    def _validate_messages(self, messages: List[Dict[str, str]]) -> bool:
        """验证消息序列的有效性"""
        if not messages:
            return False
        
        # 检查消息格式
        for msg in messages:
            if not isinstance(msg, dict):
                return False
            if 'role' not in msg or 'content' not in msg:
                return False
            if msg['role'] not in ['system', 'user', 'assistant']:
                return False
            if not isinstance(msg['content'], str):
                return False
        
        # 检查系统消息是否在开头
        if messages[0]['role'] != 'system':
            return False
        
        return True

    async def call_agent(self, agent_name: str, query: str, session_id: str = None) -> Dict[str, Any]:
        """
        调用指定的Agent
        
        Args:
            agent_name: Agent名称
            query: 任务内容
            session_id: 会话ID
            
        Returns:
            Dict[str, Any]: 调用结果
        """
        # 从AgentRegistry获取Agent配置
        registry = get_agent_registry()
        agent_config = registry.get_agent_config(agent_name)
        
        if not agent_config:
            available_agents = registry.get_available_agents()
            agent_names = [agent["base_name"] for agent in available_agents]
            error_msg = f"请求的Agent '{agent_name}' 未找到或未正确配置。"
            if agent_names:
                error_msg += f" 当前已加载的Agent有: {', '.join(agent_names)}。"
            else:
                error_msg += " 当前没有加载任何Agent。请检查配置文件。"
            error_msg += " 请确认您请求的Agent名称是否准确。"
            
            logger.error(f"Agent调用失败: {error_msg}")
            return {"status": "error", "error": error_msg}
        
        # 生成会话ID
        if not session_id:
            session_id = f"agent_{agent_config.base_name}_default_user_session"
        
        try:
            # 检查是否有自定义执行方法
            if hasattr(agent_config, 'execution_method') and agent_config.execution_method:
                logger.info(f"使用自定义执行方法: {agent_name}")
                try:
                    # 动态导入模块和函数
                    module_name = agent_config.execution_method.get('module')
                    function_name = agent_config.execution_method.get('function')
                    
                    if not module_name or not function_name:
                        raise ValueError("executionMethod配置不完整")
                    
                    # 导入模块
                    module = __import__(module_name, fromlist=[function_name])
                    # 获取函数
                    execution_function = getattr(module, function_name)
                    
                    # 调用函数
                    result = await execution_function(query)
                    return {"status": "success", "result": result}
                    
                except ImportError as e:
                    error_msg = f"无法导入模块 {module_name}: {e}"
                    logger.error(f"自定义执行方法调用失败: {error_msg}")
                    return {"status": "error", "error": error_msg}
                except AttributeError as e:
                    error_msg = f"无法找到函数 {function_name}: {e}"
                    logger.error(f"自定义执行方法调用失败: {error_msg}")
                    return {"status": "error", "error": error_msg}
                except Exception as e:
                    error_msg = f"自定义执行方法执行失败: {e}"
                    logger.error(f"自定义执行方法调用失败: {error_msg}")
                    return {"status": "error", "error": error_msg}
            
            # 标准Agent处理：使用LLM API
            # 获取会话历史
            history = self.get_agent_session_history(agent_name, session_id)
            
            # 构建完整的消息序列
            messages = []
            
            # 1. 系统消息：设定Agent的身份、行为、风格等
            system_message = self._build_system_message(agent_config)
            messages.append(system_message)
            
            # 2. 历史消息：保留多轮对话的上下文
            messages.extend(history)
            
            # 3. 当前用户输入：本次要处理的任务内容
            user_message = self._build_user_message(query, agent_config)
            messages.append(user_message)
            
            # 验证消息序列
            if not self._validate_messages(messages):
                return {"status": "error", "error": "消息序列格式无效"}
            
            # 记录调试信息
            if self.debug_mode:
                logger.debug(f"Agent调用消息序列:")
                for i, msg in enumerate(messages):
                    logger.debug(f"  [{i}] {msg['role']}: {msg['content'][:100]}...")
            
            # 调用LLM API
            response = await self._call_llm_api(agent_config, messages)
            
            if response.get("status") == "success":
                assistant_response = response.get("result", "")
                
                # 更新会话历史
                self.update_agent_session_history(
                    agent_name, user_message['content'], assistant_response, session_id
                )
                
                return {"status": "success", "result": assistant_response}
            else:
                return response
                
        except Exception as e:
            error_msg = f"调用Agent '{agent_name}' 时发生错误: {str(e)}"
            logger.error(f"Agent调用异常: {error_msg}")
            return {"status": "error", "error": error_msg}
    
    async def _call_llm_api(self, agent_config: AgentConfig, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """调用LLM API，使用Agent配置中的参数"""
        try:
            # 使用新版本的OpenAI API
            from openai import AsyncOpenAI
            
            # 记录调试信息
            if self.debug_mode:
                logger.debug(f"调用LLM API - Agent: {agent_config.name}")
                logger.debug(f"  模型: {agent_config.id}")
                logger.debug(f"  提供商: {agent_config.model_provider}")
                logger.debug(f"  API URL: {agent_config.api_base_url}")
                logger.debug(f"  温度: {agent_config.temperature}")
                logger.debug(f"  最大Token: {agent_config.max_output_tokens}")
                logger.debug(f"  消息数量: {len(messages)}")
            
            # 验证必要的配置参数
            if not agent_config.id:
                return {"status": "error", "error": "Agent配置缺少模型ID"}
            
            if not agent_config.api_key:
                return {"status": "error", "error": "Agent配置缺少API密钥"}
            
            # 创建客户端，使用Agent配置中的参数
            client = AsyncOpenAI(
                api_key=agent_config.api_key,
                base_url=agent_config.api_base_url or "https://api.deepseek.com/v1"
            )
            
            # 准备API调用参数
            api_params = {
                "model": agent_config.id,
                "messages": messages,
                "max_tokens": agent_config.max_output_tokens,
                "temperature": agent_config.temperature,
                "stream": False
            }
            
            # 记录API调用参数（调试模式）
            if self.debug_mode:
                logger.debug(f"API调用参数: {api_params}")
            
            # 调用API
            response = await client.chat.completions.create(**api_params)
            
            # 提取响应内容
            assistant_content = response.choices[0].message.content
            
            # 记录响应信息（调试模式）
            if self.debug_mode:
                usage = response.usage
                logger.debug(f"API响应成功:")
                logger.debug(f"  使用Token: {usage.prompt_tokens} (输入) + {usage.completion_tokens} (输出) = {usage.total_tokens} (总计)")
                logger.debug(f"  响应长度: {len(assistant_content)} 字符")
            
            return {"status": "success", "result": assistant_content}
            
        except Exception as e:
            error_msg = f"LLM API调用失败: {str(e)}"
            logger.error(f"Agent '{agent_config.name}' API调用失败: {error_msg}")
            
            # 记录详细的错误信息（调试模式）
            if self.debug_mode:
                import traceback
                logger.debug(f"详细错误信息:")
                logger.debug(traceback.format_exc())
            
            return {"status": "error", "error": error_msg}
    

    


# 全局Agent管理器实例
_AGENT_MANAGER = None

def get_agent_manager() -> AgentManager:
    """获取全局Agent管理器实例"""
    global _AGENT_MANAGER
    if _AGENT_MANAGER is None:
        _AGENT_MANAGER = AgentManager()
    return _AGENT_MANAGER

# 便捷函数
async def call_agent(agent_name: str, query: str, session_id: str = None) -> Dict[str, Any]:
    """便捷的Agent调用函数"""
    manager = get_agent_manager()
    return await manager.call_agent(agent_name, query, session_id)

def list_agents() -> List[Dict[str, Any]]:
    """便捷的Agent列表获取函数"""
    from mcpserver.agent_registry import list_agents as registry_list_agents
    return registry_list_agents()

def get_agent_info(agent_name: str) -> Optional[Dict[str, Any]]:
    """便捷的Agent信息获取函数"""
    from mcpserver.agent_registry import get_agent_info as registry_get_agent_info
    return registry_get_agent_info(agent_name) 