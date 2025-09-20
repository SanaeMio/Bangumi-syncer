"""
配置管理模块
"""
import os
import platform
import sys
from configparser import ConfigParser
from typing import Dict, Any, Optional
from pathlib import Path


class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        self.platform = platform.system()
        self.cwd = Path(__file__).parent.parent.parent
        
        # 配置文件路径
        self.config_paths = self._get_config_paths()
        self.active_config_path = self._find_active_config()
        
        # 配置缓存
        self._config_cache: Optional[ConfigParser] = None
        self._last_modified = 0
        
        # 初始化配置
        self._load_config()
        
        # 立即输出启动信息（在模块导入时）
        from .startup_info import startup_info
        startup_info.print_banner()
        startup_info.print_system_info(self.active_config_path)
    
    def _get_config_paths(self) -> Dict[str, Path]:
        """获取可能的配置文件路径"""
        return {
            'env': os.environ.get('CONFIG_FILE'),
            'mounted': Path('/app/config/config.ini'),
            'dev': self.cwd / 'config.dev.ini',
            'default': self.cwd / 'config.ini'
        }
    
    def _find_active_config(self) -> Path:
        """查找活动的配置文件"""
        # 1. 环境变量指定的配置文件
        if self.config_paths['env'] and Path(self.config_paths['env']).exists():
            return Path(self.config_paths['env'])
        
        # 2. Docker挂载的配置文件
        if self.config_paths['mounted'].exists():
            return self.config_paths['mounted']
        
        # 3. 开发配置文件
        if self.config_paths['dev'].exists():
            return self.config_paths['dev']
        
        # 4. 默认配置文件
        return self.config_paths['default']
    
    def _load_config(self) -> None:
        """加载配置文件"""
        config = ConfigParser()
        
        # 读取配置文件
        config.read(self.active_config_path, encoding='utf-8-sig')
        
        # 应用环境变量覆盖
        self._apply_env_overrides(config)
        
        # 更新缓存
        self._config_cache = config
        self._last_modified = self.active_config_path.stat().st_mtime if self.active_config_path.exists() else 0
    
    def _apply_env_overrides(self, config: ConfigParser) -> None:
        """应用环境变量覆盖"""
        env_overrides = {
            ('bangumi', 'username'): 'BANGUMI_USERNAME',
            ('bangumi', 'access_token'): 'BANGUMI_ACCESS_TOKEN',
            ('sync', 'single_username'): 'SINGLE_USERNAME',
            ('bangumi', 'private'): 'BANGUMI_PRIVATE',
            ('dev', 'script_proxy'): 'HTTP_PROXY',
            ('dev', 'debug'): 'DEBUG_MODE'
        }
        
        for (section, option), env_var in env_overrides.items():
            env_value = os.environ.get(env_var)
            if env_value:
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, option, env_value)
    
    def _check_config_updated(self) -> bool:
        """检查配置文件是否已更新"""
        if not self.active_config_path.exists():
            return False
        
        current_mtime = self.active_config_path.stat().st_mtime
        if current_mtime > self._last_modified:
            return True
        
        return False
    
    def get_config_parser(self) -> ConfigParser:
        """获取配置对象"""
        if self._check_config_updated():
            self._load_config()
        
        return self._config_cache
    
    def reload_config(self) -> None:
        """重新加载配置"""
        self._load_config()
    
    def reload(self) -> None:
        """重新加载配置（别名）"""
        self.reload_config()
    
    def get_section(self, section: str, fallback: Dict[str, Any] = None) -> Dict[str, Any]:
        """获取配置段"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback or {}
        
        result = {}
        for key, value in config.items(section):
            # 尝试转换为适当的数据类型
            if value.lower() in ('true', 'false'):
                result[key] = value.lower() == 'true'
            elif value.isdigit():
                result[key] = int(value)
            else:
                result[key] = value
        
        return result
    
    def get_config(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback
        
        if not config.has_option(section, key):
            return fallback
        
        value = config.get(section, key)
        
        # 尝试转换为适当的数据类型
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        elif value.isdigit():
            return int(value)
        else:
            return value
    
    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值（别名）"""
        return self.get_config(section, key, fallback)
    
    def set_config(self, section: str, key: str, value: Any) -> None:
        """设置配置值"""
        config = self.get_config_parser()
        if not config.has_section(section):
            config.add_section(section)
        
        config.set(section, key, str(value))
        self._save_config(config)
    
    def set(self, section: str, key: str, value: Any) -> None:
        """设置配置值（别名）"""
        self.set_config(section, key, value)
    
    def _save_config(self, config: ConfigParser) -> None:
        """保存配置文件"""
        with open(self.active_config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        
        # 更新缓存
        self._config_cache = config
        self._last_modified = self.active_config_path.stat().st_mtime
    
    def get_bangumi_configs(self) -> Dict[str, Dict[str, Any]]:
        """获取所有Bangumi配置"""
        config = self.get_config_parser()
        bangumi_configs = {}
        
        # 遍历所有配置段，查找多账号 bangumi-* 配置段（排除 bangumi-data 和 bangumi-mapping）
        for section_name in config.sections():
            if (section_name.startswith('bangumi-') and 
                section_name not in ['bangumi-data', 'bangumi-mapping']):
                section_config = self.get_section(section_name)
                if section_config.get('username') and section_config.get('access_token'):
                    bangumi_configs[section_name] = section_config
        
        return bangumi_configs
    
    def get_user_mappings(self) -> Dict[str, str]:
        """获取用户映射配置"""
        bangumi_configs = self.get_bangumi_configs()
        user_mappings = {}
        
        for section_name, config in bangumi_configs.items():
            media_server_username = config.get('media_server_username', '')
            if media_server_username:
                user_mappings[media_server_username] = section_name
        
        return user_mappings
    
    def get_all_config(self) -> Dict[str, Dict[str, Any]]:
        """获取所有配置"""
        config = self.get_config_parser()
        result = {}
        
        # 收集多账号配置
        multi_accounts = {}
        
        for section_name in config.sections():
            if (section_name.startswith('bangumi-') and 
                section_name not in ['bangumi-data', 'bangumi-mapping']):
                # 这是多账号配置段，收集到 multi_accounts 中
                section_config = self.get_section(section_name)
                # 使用 display_name 作为键，如果没有则使用配置段名
                account_key = section_config.get('display_name', section_name)
                multi_accounts[account_key] = section_config
            else:
                # 统一键名格式：将连字符转换为下划线
                normalized_key = section_name.replace('-', '_')
                result[normalized_key] = self.get_section(section_name)
        
        # 添加多账号配置到结果中
        if multi_accounts:
            result['multi_accounts'] = multi_accounts
        
        return result
    
    def save_config(self) -> None:
        """保存配置"""
        config = self.get_config_parser()
        self._save_config(config)
    
    def reload_multi_account_configs(self) -> None:
        """强制重新加载多账号配置"""
        from .logging import logger
        
        # 清除缓存
        self._config_cache = None
        self._last_modified = 0
        
        # 重新加载配置
        self._load_config()
        
        # 获取配置以触发日志输出
        bangumi_configs = self.get_bangumi_configs()
        user_mappings = self.get_user_mappings()
        
        logger.info('强制重新加载多账号配置')
        logger.info(f'加载了 {len(bangumi_configs)} 个bangumi账号配置')
        logger.info(f'加载了 {len(user_mappings)} 个用户映射配置')


# 全局配置实例
config_manager = ConfigManager() 