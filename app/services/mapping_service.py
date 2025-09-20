"""
映射服务模块
"""
import json
import os
from typing import Dict, Any, Optional
from pathlib import Path

from ..core.config import config_manager
from ..core.logging import logger


class MappingService:
    """映射服务"""
    
    def __init__(self):
        self._cached_mappings: Dict[str, str] = {}
        self._mapping_file_path: Optional[str] = None
        self._last_modified_time: float = 0
    
    def load_custom_mappings(self) -> Dict[str, str]:
        """从外部JSON文件读取自定义映射配置"""
        # 定义可能的配置文件路径
        mapping_file_paths = [
            './bangumi_mapping.json',  # 当前目录
            '/app/config/bangumi_mapping.json',  # Docker挂载目录
            '/app/bangumi_mapping.json'  # Docker内部目录
        ]
        
        # 查找存在的配置文件
        current_file_path = None
        for mapping_file in mapping_file_paths:
            if os.path.exists(mapping_file):
                current_file_path = mapping_file
                break
        
        # 如果没有找到配置文件，创建默认文件
        if not current_file_path:
            default_file = './bangumi_mapping.json'
            try:
                default_config = {
                    "_comment": "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目，参考_examples的格式将新内容添加到mappings中",
                    "_format": "番剧名: bangumi_subject_id",
                    "_note": "bangumi_subject_id需要配置第一季的，程序会自动往后找",
                    "_examples": {
                        "魔王学院的不适任者": "292222",
                        "我推的孩子": "386809"
                    },
                    "mappings": {
                        "假面骑士加布": "502002"
                    }
                }
                with open(default_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
                logger.info(f'创建了默认的自定义映射文件: {default_file}')
                current_file_path = default_file
            except Exception as e:
                logger.error(f'创建默认映射文件失败: {e}')
                return {}
        
        try:
            # 获取文件修改时间
            current_modified_time = os.path.getmtime(current_file_path)
            
            # 检查是否需要重新加载
            need_reload = (
                self._mapping_file_path != current_file_path or  # 文件路径变化
                current_modified_time != self._last_modified_time or  # 文件被修改
                not self._cached_mappings  # 缓存为空
            )
            
            if need_reload:
                logger.debug(f'检测到映射配置文件变化，重新加载: {current_file_path}')
                
                with open(current_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    mappings = data.get('mappings', {})
                    
                    # 更新缓存
                    self._cached_mappings = mappings
                    self._mapping_file_path = current_file_path
                    self._last_modified_time = current_modified_time
                    
                    if mappings:
                        logger.debug(f'从 {current_file_path} 重新加载了 {len(mappings)} 个自定义映射')
                    else:
                        logger.debug(f'映射配置文件 {current_file_path} 中没有配置映射项')
            else:
                logger.debug(f'使用缓存的映射配置，共 {len(self._cached_mappings)} 个映射')
                
            return self._cached_mappings.copy()  # 返回副本以避免外部修改影响缓存
            
        except Exception as e:
            logger.error(f'读取自定义映射文件 {current_file_path} 失败: {e}')
            # 如果读取失败，返回缓存的配置（如果有的话）
            return self._cached_mappings.copy() if self._cached_mappings else {}
    
    def reload_custom_mappings(self) -> Dict[str, str]:
        """强制重新加载自定义映射配置"""
        # 清空缓存强制重新加载
        self._cached_mappings = {}
        self._mapping_file_path = None
        self._last_modified_time = 0
        
        logger.info('强制重新加载自定义映射配置')
        return self.load_custom_mappings()
    
    def update_custom_mappings(self, mappings: Dict[str, str]) -> bool:
        """更新自定义映射配置"""
        try:
            # 找到配置文件路径
            mapping_file_paths = [
                './bangumi_mapping.json',
                '/app/config/bangumi_mapping.json',
                '/app/bangumi_mapping.json'
            ]
            
            mapping_file_path = None
            for path in mapping_file_paths:
                if os.path.exists(path):
                    mapping_file_path = path
                    break
            
            if not mapping_file_path:
                mapping_file_path = './bangumi_mapping.json'
            
            # 读取现有配置
            config_data = {
                "_comment": "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目",
                "_format": "番剧名: bangumi_subject_id",
                "_note": "bangumi_subject_id需要配置第一季的，程序会自动往后找",
                "mappings": mappings
            }
            
            # 保存配置
            with open(mapping_file_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            
            # 重新加载映射
            self.reload_custom_mappings()
            
            logger.info(f'自定义映射已更新，共 {len(mappings)} 个映射')
            return True
        except Exception as e:
            logger.error(f'更新自定义映射失败: {e}')
            return False
    
    def delete_custom_mapping(self, title: str) -> bool:
        """删除自定义映射"""
        try:
            mappings = self.load_custom_mappings()
            if title in mappings:
                del mappings[title]
                
                # 更新配置文件
                if self.update_custom_mappings(mappings):
                    logger.info(f'映射 "{title}" 已删除')
                    return True
                else:
                    return False
            
            logger.warning(f'映射 "{title}" 不存在')
            return False
        except Exception as e:
            logger.error(f'删除自定义映射失败: {e}')
            return False
    
    def get_mappings_status(self) -> Dict[str, Any]:
        """获取映射配置状态"""
        mappings = self.load_custom_mappings()
        return {
            "mappings_count": len(mappings),
            "file_path": self._mapping_file_path,
            "last_modified": self._last_modified_time,
            "cached": bool(self._cached_mappings),
            "mappings": mappings
        }
    
    def get_all_mappings(self) -> Dict[str, str]:
        """获取所有映射"""
        return self.load_custom_mappings()
    
    def update_mappings(self, mappings: Dict[str, str]) -> bool:
        """更新映射（别名）"""
        return self.update_custom_mappings(mappings)


# 全局映射服务实例
mapping_service = MappingService() 