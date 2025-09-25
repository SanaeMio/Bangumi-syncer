"""
配置相关API
"""
import os
import json
import shutil
import time
from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pathlib import Path

from ..core.config import config_manager
from ..core.logging import logger
from ..core.security import security_manager
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["config"])


def _handle_multi_accounts_config(multi_accounts: Dict[str, Dict[str, Any]]) -> None:
    """处理多账号配置"""
    config = config_manager.get_config_parser()
    
    # 清除现有的多账号 bangumi-* 配置段（但保留 bangumi-data 和 bangumi-mapping）
    sections_to_remove = [
        section for section in config.sections() 
        if section.startswith('bangumi-') and section not in ['bangumi-data', 'bangumi-mapping']
    ]
    for section in sections_to_remove:
        config.remove_section(section)
        logger.info(f"清除旧的多账号配置段: {section}")
    
    # 添加新的多账号配置
    for account_key, account_config in multi_accounts.items():
        if not account_config.get('username') or not account_config.get('access_token') or not account_config.get('media_server_username'):
            logger.warning(f"跳过不完整的账号配置: {account_key}")
            continue
        
        # 生成配置段名称，使用 bangumi- 前缀
        section_name = f"bangumi-{account_config['username']}"
        
        # 确保配置段名称唯一
        counter = 1
        original_section_name = section_name
        while config.has_section(section_name):
            section_name = f"{original_section_name}-{counter}"
            counter += 1
        
        # 创建配置段
        config.add_section(section_name)
        config.set(section_name, 'username', account_config['username'])
        config.set(section_name, 'access_token', account_config['access_token'])
        config.set(section_name, 'media_server_username', account_config['media_server_username'])
        config.set(section_name, 'private', str(account_config.get('private', False)).lower())
        
        # 如果有显示名称，也保存
        if account_key != account_config['username']:
            config.set(section_name, 'display_name', account_key)
        
        logger.info(f"创建多账号配置段: {section_name} (媒体服务器用户: {account_config['media_server_username']})")
    
    logger.info(f"多账号配置处理完成，共配置 {len(multi_accounts)} 个账号")


@router.get("/config")
async def get_config(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """获取配置信息"""
    try:
        config_data = config_manager.get_all_config()
        
        # 处理敏感信息：不向前端返回加密的密码
        if 'auth' in config_data and 'password' in config_data['auth']:
            # 检查密码是否已加密（长度为64的十六进制字符串）
            password = config_data['auth']['password']
            if password and len(str(password)) == 64:
                # 已加密的密码，返回空字符串给前端
                config_data['auth']['password'] = ''
                logger.debug("已隐藏加密密码，返回空字符串给前端")
        
        return {
            "status": "success",
            "data": config_data
        }
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")


@router.post("/config")
async def update_config(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """更新配置信息"""
    try:
        data = await request.json()
        
        # 处理多账号配置
        multi_accounts = data.pop('multi_accounts', None)
        
        # 更新常规配置
        password_updated = False
        for section, items in data.items():
            # 将下划线转换为连字符，以匹配配置文件格式
            normalized_section = section.replace('_', '-')
            for key, value in items.items():
                # 特殊处理auth段的password字段
                if normalized_section == 'auth' and key == 'password':
                    # 如果提交的密码为空，跳过更新（保持现有密码不变）
                    if not value or str(value).strip() == '':
                        logger.debug("跳过空密码更新，保持现有密码不变")
                        continue
                    
                    # 检查密码是否需要加密（如果长度小于64，说明是明文密码）
                    if len(str(value)) < 64:
                        # 获取当前的secret_key用于加密
                        auth_config = security_manager.get_auth_config()
                        encrypted_password = security_manager.hash_password(str(value), auth_config['secret_key'])
                        config_manager.set_config(normalized_section, key, encrypted_password)
                        password_updated = True
                        logger.info(f"密码已在保存时自动加密")
                    else:
                        # 已经是加密密码，直接保存
                        config_manager.set_config(normalized_section, key, value)
                else:
                    # 其他配置项正常保存
                    config_manager.set_config(normalized_section, key, value)
        
        # 处理多账号配置
        if multi_accounts is not None:
            _handle_multi_accounts_config(multi_accounts)
        
        # 保存配置
        config_manager.save_config()
        
        # 如果密码被更新，需要重新初始化安全管理器以确保运行时状态一致
        if password_updated:
            security_manager._init_auth_config()
            logger.info("密码更新完成，认证配置已重新加载")
        
        return {"status": "success", "message": "配置更新成功"}
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@router.get("/config/backups")
async def get_config_backups(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """获取配置备份列表"""
    try:
        backup_dir = Path("config_backups")
        if not backup_dir.exists():
            return {
                "status": "success",
                "data": {
                    "backups": []
                }
            }
        
        backups = []
        for file in backup_dir.glob("*.ini"):
            stat = file.stat()
            backups.append({
                "filename": file.name,
                "size": stat.st_size,
                "modified": stat.st_mtime * 1000,  # 转换为毫秒
                "created": datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
            })
        
        # 按修改时间排序
        backups.sort(key=lambda x: x["modified"], reverse=True)
        
        return {
            "status": "success",
            "data": {
                "backups": backups
            }
        }
    except Exception as e:
        logger.error(f"获取配置备份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置备份列表失败: {str(e)}")


@router.get("/config/backup/{filename}")
async def get_config_backup(filename: str, request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """获取特定配置备份内容"""
    try:
        backup_path = Path("config_backups") / filename
        
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        # 读取备份文件内容
        with open(backup_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {
            "status": "success",
            "data": {
                "filename": filename,
                "content": content
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置备份失败: {str(e)}")


@router.delete("/config/backup/{filename}")
async def delete_config_backup(filename: str, request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """删除配置备份文件"""
    try:
        backup_path = Path("config_backups") / filename
        
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        # 删除备份文件
        backup_path.unlink()
        
        return {
            "status": "success",
            "message": "备份文件删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除配置备份失败: {str(e)}")


@router.post("/config/backup")
async def create_config_backup(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """创建配置备份"""
    try:
        backup_dir = Path("config_backups")
        backup_dir.mkdir(exist_ok=True)
        
        # 生成备份文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"config_backup_{timestamp}.ini"
        backup_path = backup_dir / backup_filename
        
        # 复制当前配置文件
        shutil.copy2(config_manager.active_config_path, backup_path)
        
        return {
            "status": "success",
            "message": "配置备份创建成功",
            "data": {
                "filename": backup_filename
            }
        }
    except Exception as e:
        logger.error(f"创建配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建配置备份失败: {str(e)}")


@router.post("/config/restore/{filename}")
async def restore_config_backup(filename: str, request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """恢复配置备份"""
    try:
        backup_path = Path("config_backups") / filename
        
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        # 恢复配置文件
        shutil.copy2(backup_path, config_manager.active_config_path)
        
        # 重新加载配置
        config_manager.reload_config()
        
        return {
            "status": "success",
            "message": "配置恢复成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"恢复配置备份失败: {str(e)}")


@router.post("/config/backups/cleanup")
async def cleanup_config_backups(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """清理配置备份"""
    try:
        data = await request.json()
        strategy = data.get("strategy", "recent")
        
        backup_dir = Path("config_backups")
        if not backup_dir.exists():
            return {
                "status": "success",
                "message": "没有备份文件需要清理"
            }
        
        # 获取所有备份文件
        backup_files = list(backup_dir.glob("*.ini"))
        
        if not backup_files:
            return {
                "status": "success", 
                "message": "没有备份文件需要清理"
            }
        
        deleted_count = 0
        
        if strategy == "all":
            # 删除所有备份文件
            for file in backup_files:
                file.unlink()
                deleted_count += 1
        elif strategy == "recent":
            # 按数量保留最新的备份
            keep_count = data.get("keep_count", 10)
            backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            for file in backup_files[keep_count:]:
                file.unlink()
                deleted_count += 1
        elif strategy == "date":
            # 按日期删除旧备份
            keep_days = data.get("keep_days", 30)
            cutoff_time = time.time() - (keep_days * 24 * 60 * 60)
            for file in backup_files:
                if file.stat().st_mtime < cutoff_time:
                    file.unlink()
                    deleted_count += 1
        
        return {
            "status": "success",
            "message": f"清理完成，删除了 {deleted_count} 个备份文件"
        }
    except Exception as e:
        logger.error(f"清理配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理配置备份失败: {str(e)}") 