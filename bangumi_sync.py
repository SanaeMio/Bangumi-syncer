import json
from typing import Dict, Optional, Any, List
import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from functools import lru_cache
import os
import time
import sqlite3
from datetime import datetime, timedelta
import configparser

from utils.configs import configs, MyLogger
from utils.bangumi_api import BangumiApi
from utils.data_util import extract_plex_json
from utils.bangumi_data import BangumiData

logger = MyLogger()

app = FastAPI(title="Bangumi-Syncer", description="自动同步Bangumi观看记录")

# 创建静态文件和模板目录
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 设置模板引擎
templates = Jinja2Templates(directory="templates")

# 数据库初始化
def init_database():
    """初始化SQLite数据库"""
    db_path = "sync_records.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建同步记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_name TEXT NOT NULL,
            title TEXT NOT NULL,
            ori_title TEXT,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            subject_id TEXT,
            episode_id TEXT,
            status TEXT NOT NULL,
            message TEXT,
            source TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()

# 初始化数据库
init_database()

# 记录同步日志到数据库
def log_sync_record(user_name: str, title: str, ori_title: str, season: int, episode: int, 
                   subject_id: str = None, episode_id: str = None, status: str = "success", 
                   message: str = "", source: str = "custom"):
    """记录同步日志到数据库"""
    try:
        conn = sqlite3.connect("sync_records.db")
        cursor = conn.cursor()
        
        # 使用本地时间而不是UTC时间
        local_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            INSERT INTO sync_records 
            (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (local_time, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"记录同步日志失败: {e}")


class CustomItem(BaseModel):
    media_type: str
    title: str
    ori_title: str = None
    season: int
    episode: int
    release_date: str
    user_name: str
    source: str = None  # 可选的source字段


# 全局变量用于缓存映射配置和文件状态
_cached_mappings: Dict[str, str] = {}
_mapping_file_path: Optional[str] = None
_last_modified_time: float = 0

# 多账号配置缓存
_bangumi_configs_cache: Optional[Dict[str, Dict[str, str]]] = None
_user_mappings_cache: Optional[Dict[str, str]] = None


# 获取多账号配置
def get_multi_account_configs() -> Dict[str, Dict[str, str]]:
    """获取所有bangumi账号配置
    
    Returns:
        Dict[str, Dict[str, str]]: 配置段名到配置内容的映射
    """
    global _bangumi_configs_cache
    
    if _bangumi_configs_cache is not None:
        return _bangumi_configs_cache
    
    bangumi_configs = {}
    
    # 遍历所有配置段，查找以 'bangumi-' 开头的段
    for section_name in configs.raw.sections():
        if section_name.startswith('bangumi-'):
            config = {
                'username': configs.raw.get(section_name, 'username', fallback=''),
                'access_token': configs.raw.get(section_name, 'access_token', fallback=''),
                'private': configs.raw.getboolean(section_name, 'private', fallback=False),
                'media_server_username': configs.raw.get(section_name, 'media_server_username', fallback=''),
                'display_name': configs.raw.get(section_name, 'display_name', fallback=''),
            }
            # 只保存有效的配置（至少有用户名、access_token和媒体服务器用户名）
            if config['username'] and config['access_token'] and config['media_server_username']:
                bangumi_configs[section_name] = config
                logger.debug(f'加载多账号配置: {section_name}')
            elif config['username'] and config['access_token']:
                logger.error(f'多账号配置 {section_name} 缺少 media_server_username 字段，配置无效')
    
    _bangumi_configs_cache = bangumi_configs
    logger.info(f'加载了 {len(bangumi_configs)} 个bangumi账号配置')
    return bangumi_configs


# 获取用户映射配置
def get_user_mappings() -> Dict[str, str]:
    """获取媒体服务器用户名到bangumi配置段的映射
    
    Returns:
        Dict[str, str]: 媒体服务器用户名到bangumi配置段名的映射
    """
    global _user_mappings_cache
    
    if _user_mappings_cache is not None:
        return _user_mappings_cache
    
    user_mappings = {}
    
    # 从bangumi配置中自动生成用户映射
    bangumi_configs = get_multi_account_configs()
    for section_name, config in bangumi_configs.items():
        media_server_username = config.get('media_server_username', '')
        if media_server_username:
            user_mappings[media_server_username] = section_name
            logger.debug(f'自动生成用户映射: {media_server_username} -> {section_name}')
    
    _user_mappings_cache = user_mappings
    logger.info(f'加载了 {len(user_mappings)} 个用户映射配置')
    return user_mappings


# 根据用户名获取对应的bangumi配置
def get_bangumi_config_for_user(user_name: str) -> Optional[Dict[str, str]]:
    """根据媒体服务器用户名获取对应的bangumi配置
    
    Args:
        user_name: 媒体服务器用户名
        
    Returns:
        Optional[Dict[str, str]]: bangumi配置，如果找不到则返回None
    """
    mode = configs.raw.get('sync', 'mode', fallback='single')
    
    if mode == 'single':
        # 单用户模式，使用默认的bangumi配置
        return {
            'username': configs.raw.get('bangumi', 'username', fallback=''),
            'access_token': configs.raw.get('bangumi', 'access_token', fallback=''),
            'private': configs.raw.getboolean('bangumi', 'private', fallback=False),
        }
    elif mode == 'multi':
        # 多用户模式，根据用户映射查找对应的配置
        user_mappings = get_user_mappings()
        bangumi_configs = get_multi_account_configs()
        
        bangumi_section = user_mappings.get(user_name)
        if bangumi_section and bangumi_section in bangumi_configs:
            return bangumi_configs[bangumi_section]
        else:
            logger.error(f'多用户模式下未找到用户 {user_name} 的bangumi配置映射')
            return None
    
    return None


# 创建BangumiApi实例的函数，支持多账号
def get_bangumi_api_for_user(user_name: str) -> Optional[BangumiApi]:
    """根据用户名创建对应的BangumiApi实例
    
    Args:
        user_name: 媒体服务器用户名
        
    Returns:
        Optional[BangumiApi]: BangumiApi实例，如果配置无效则返回None
    """
    bangumi_config = get_bangumi_config_for_user(user_name)
    if not bangumi_config:
        return None
    
    if not bangumi_config['username'] or not bangumi_config['access_token']:
        logger.error(f'用户 {user_name} 的bangumi配置不完整')
        return None
    
    return BangumiApi(
        username=bangumi_config['username'],
        access_token=bangumi_config['access_token'],
        private=bangumi_config['private'],
        http_proxy=configs.raw.get('dev', 'script_proxy', fallback='')
    )


# 创建BangumiApi实例的函数，使用缓存减少重复初始化（保持向后兼容）
@lru_cache(maxsize=1)
def get_bangumi_api() -> BangumiApi:
    return BangumiApi(
        username=configs.raw.get('bangumi', 'username', fallback=''),
        access_token=configs.raw.get('bangumi', 'access_token', fallback=''),
        private=configs.raw.getboolean('bangumi', 'private', fallback=False),
        http_proxy=configs.raw.get('dev', 'script_proxy', fallback='')
    )


# 读取自定义映射配置文件（带缓存优化）
def load_custom_mappings() -> Dict[str, str]:
    """从外部JSON文件读取自定义映射配置
    
    Returns:
        Dict[str, str]: 番剧名到bangumi ID的映射字典
    """
    global _cached_mappings, _mapping_file_path, _last_modified_time
    
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
            _mapping_file_path != current_file_path or  # 文件路径变化
            current_modified_time != _last_modified_time or  # 文件被修改
            not _cached_mappings  # 缓存为空
        )
        
        if need_reload:
            logger.debug(f'检测到映射配置文件变化，重新加载: {current_file_path}')
            
            with open(current_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                mappings = data.get('mappings', {})
                
                # 更新缓存
                _cached_mappings = mappings
                _mapping_file_path = current_file_path
                _last_modified_time = current_modified_time
                
                if mappings:
                    logger.debug(f'从 {current_file_path} 重新加载了 {len(mappings)} 个自定义映射')
                else:
                    logger.debug(f'映射配置文件 {current_file_path} 中没有配置映射项')
        else:
            logger.debug(f'使用缓存的映射配置，共 {len(_cached_mappings)} 个映射')
            
        return _cached_mappings.copy()  # 返回副本以避免外部修改影响缓存
        
    except Exception as e:
        logger.error(f'读取自定义映射文件 {current_file_path} 失败: {e}')
        # 如果读取失败，返回缓存的配置（如果有的话）
        return _cached_mappings.copy() if _cached_mappings else {}


# 强制重新加载映射配置（用于调试或手动刷新）
def reload_custom_mappings() -> Dict[str, str]:
    """强制重新加载自定义映射配置
    
    Returns:
        Dict[str, str]: 番剧名到bangumi ID的映射字典
    """
    global _cached_mappings, _mapping_file_path, _last_modified_time
    
    # 清空缓存强制重新加载
    _cached_mappings = {}
    _mapping_file_path = None
    _last_modified_time = 0
    
    logger.info('强制重新加载自定义映射配置')
    return load_custom_mappings()


# 强制重新加载多账号配置
def reload_multi_account_configs():
    """强制重新加载多账号配置"""
    global _bangumi_configs_cache, _user_mappings_cache
    
    _bangumi_configs_cache = None
    _user_mappings_cache = None
    
    logger.info('强制重新加载多账号配置')
    get_multi_account_configs()
    get_user_mappings()


# 创建BangumiData实例的函数，使用缓存减少重复初始化
@lru_cache(maxsize=1)
def get_bangumi_data() -> BangumiData:
    return BangumiData()


# 检查用户权限
def check_user_permission(user_name: str) -> bool:
    """检查用户是否有权限同步"""
    mode = configs.raw.get('sync', 'mode', fallback='single')
    
    if mode == 'single':
        single_username = configs.raw.get('sync', 'single_username', fallback='')
        if single_username and user_name != single_username:
            logger.debug(f'非配置同步用户：{user_name}，跳过')
            return False
        if not single_username:
            logger.error(f'未设置同步用户single_username，请检查config.ini配置')
            return False
    elif mode == 'multi':
        # 多用户模式，检查用户是否在映射配置中
        user_mappings = get_user_mappings()
        if user_name not in user_mappings:
            logger.debug(f'多用户模式下用户 {user_name} 未配置映射，跳过')
            return False
        
        # 检查对应的bangumi配置是否存在且有效
        bangumi_config = get_bangumi_config_for_user(user_name)
        if not bangumi_config:
            logger.error(f'多用户模式下用户 {user_name} 的bangumi配置无效')
            return False
    else:
        logger.error(f'不支持的同步模式: {mode}')
        return False
    
    return True


def is_title_blocked(title: str, ori_title: str = None) -> bool:
    """检查番剧标题是否包含屏蔽关键词
    
    Args:
        title: 番剧标题
        ori_title: 原始标题（可选）
        
    Returns:
        bool: 如果包含屏蔽关键词返回True，否则返回False
    """
    # 获取屏蔽关键词配置
    blocked_keywords_str = configs.raw.get('sync', 'blocked_keywords', fallback='').strip()
    
    # 如果没有配置屏蔽关键词，直接返回False
    if not blocked_keywords_str:
        return False
    
    # 解析屏蔽关键词列表
    blocked_keywords = [keyword.strip() for keyword in blocked_keywords_str.split(',') if keyword.strip()]
    
    # 如果解析后的关键词列表为空，直接返回False
    if not blocked_keywords:
        return False
    
    # 检查主标题
    if title:
        for keyword in blocked_keywords:
            if keyword.lower() in title.lower():
                logger.info(f'番剧标题 "{title}" 包含屏蔽关键词 "{keyword}"，跳过同步')
                return True
    
    # 检查原始标题
    if ori_title:
        for keyword in blocked_keywords:
            if keyword.lower() in ori_title.lower():
                logger.info(f'番剧原始标题 "{ori_title}" 包含屏蔽关键词 "{keyword}"，跳过同步')
                return True
    
    return False


# 查找番剧ID
async def find_subject_id(item: CustomItem) -> tuple[Optional[str], bool]:
    """根据标题和日期查找番剧ID
    
    Returns:
        tuple: (subject_id, is_season_matched_id)
            subject_id: 番剧ID
            is_season_matched_id: 对于第二季及以上，该值为True表示ID可能已经是指定季度的ID
    """
    # 获取自定义映射 - 使用优化后的缓存机制
    custom_mappings = load_custom_mappings()
    mapping_subject_id = custom_mappings.get(item.title, '')
    
    if mapping_subject_id:
        logger.debug(f'匹配到自定义映射：{item.title}={mapping_subject_id}')
        # 自定义映射的ID不视为特定季度的ID
        return mapping_subject_id, False
    
    # 标记是否通过bangumi-data获取的ID
    is_season_matched_id = False
    
    # 尝试使用 bangumi-data 匹配番剧ID
    if configs.raw.getboolean('bangumi-data', 'enabled', fallback=True):
        try:
            bgm_data = get_bangumi_data()
            release_date = None
            
            if item.release_date and len(item.release_date) >= 8:
                release_date = item.release_date[:10]
            else:
                logger.debug(f'release_date为空或无效，尝试从bangumi-data中获取日期')
            
            bangumi_data_id = bgm_data.find_bangumi_id(
                title=item.title,
                ori_title=item.ori_title,
                release_date=release_date,
                season=item.season
            )
                
            if bangumi_data_id:
                logger.info(f'通过 bangumi-data 匹配到番剧 ID: {bangumi_data_id}')
                # 对于第二季及以上的番剧，通过bangumi-data匹配到的ID可能已经是目标季度的ID
                if item.season > 1:
                    is_season_matched_id = True
                return bangumi_data_id, is_season_matched_id
        except Exception as e:
            logger.error(f'bangumi-data 匹配出错: {e}')
    
    # 如果没有匹配到，使用 bangumi API 搜索
    try:
        # 使用对应用户的bangumi API实例进行搜索
        bgm = get_bangumi_api_for_user(item.user_name)
        if not bgm:
            logger.error(f'无法为用户 {item.user_name} 创建bangumi API实例进行搜索')
            return None, False
        
        premiere_date = None
        if item.release_date and len(item.release_date) >= 8:
            premiere_date = item.release_date[:10]
        
        bgm_data = bgm.bgm_search(title=item.title, ori_title=item.ori_title, premiere_date=premiere_date)
        if not bgm_data:
            logger.error(f'bgm: 未查询到番剧信息，跳过\nbgm: {item.title=} {item.ori_title=} {premiere_date=}')
            return None, False
        
        # API搜索得到的ID不视为特定季度的ID
        return bgm_data[0]['id'], False
    except Exception as e:
        logger.error(f'bgm API搜索出错: {e}')
        return None, False


def retry_mark_episode(bgm_api, subject_id, ep_id, max_retries=3):
    """带重试机制的标记剧集方法"""
    for attempt in range(max_retries + 1):
        try:
            mark_status = bgm_api.mark_episode_watched(subject_id=subject_id, ep_id=ep_id)
            if attempt > 0:
                logger.info(f'重试成功，第 {attempt + 1} 次尝试标记成功')
            return mark_status
        except Exception as e:
            if attempt < max_retries:
                delay = 2 ** attempt  # 指数退避: 2, 4, 8秒
                logger.error(f'标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试')
                time.sleep(delay)
                continue
            else:
                logger.error(f'标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}')
                raise e


# 自定义同步
@app.post("/Custom", status_code=200)
async def custom_sync(item: CustomItem, response: Response, source: str = "custom"):
    try:
        # 如果item中包含source字段，优先使用item的source
        actual_source = item.source if item.source else source
        logger.info(f'接收到同步请求：{item}')

        # 基本验证
        if item.media_type != 'episode':
            logger.error(f'同步类型{item.media_type}不支持，跳过')
            response.status_code = 400
            return {"status": "error", "message": f"同步类型{item.media_type}不支持"}
        
        if not item.title:
            logger.error(f'同步名称为空，跳过')
            response.status_code = 400
            return {"status": "error", "message": "同步名称为空"}
        
        if item.season == 0:
            logger.error(f'不支持SP标记同步，跳过')
            response.status_code = 400
            return {"status": "error", "message": "不支持SP标记同步"}
        
        if item.episode == 0:
            logger.error(f'集数{item.episode}不能为0，跳过')
            response.status_code = 400
            return {"status": "error", "message": f"集数{item.episode}不能为0"}

        # 检查用户权限
        if not check_user_permission(item.user_name):
            response.status_code = 403
            return {"status": "error", "message": "用户无权限同步"}

        # 检查是否包含屏蔽关键词
        if is_title_blocked(item.title, item.ori_title):
            response.status_code = 200
            return {"status": "ignored", "message": "番剧标题包含屏蔽关键词，跳过同步"}

        # 查找番剧ID及其是否为特定季度ID的标记
        subject_id, is_season_matched_id = await find_subject_id(item)
        if not subject_id:
            response.status_code = 404
            return {"status": "error", "message": "未找到匹配的番剧"}

        # 获取对应用户的bangumi API实例
        bgm = get_bangumi_api_for_user(item.user_name)
        if not bgm:
            logger.error(f'无法为用户 {item.user_name} 创建bangumi API实例')
            response.status_code = 500
            return {"status": "error", "message": "bangumi配置错误"}

        # 查询bangumi番剧指定季度指定集数信息
        bgm_se_id, bgm_ep_id = bgm.get_target_season_episode_id(
            subject_id=subject_id, 
            target_season=item.season, 
            target_ep=item.episode,
            is_season_subject_id=is_season_matched_id
        )
        
        if not bgm_ep_id:
            logger.error(f'bgm: {subject_id=} {item.season=} {item.episode=}, 不存在或集数过多，跳过')
            response.status_code = 404
            return {"status": "error", "message": "未找到对应的剧集"}

        logger.debug(f'bgm: 查询到 {item.title} (https://bgm.tv/subject/{bgm_se_id}) '
                    f'S{item.season:02d}E{item.episode:02d} (https://bgm.tv/ep/{bgm_ep_id})')

        # 标记为看过
        mark_status = retry_mark_episode(bgm, bgm_se_id, bgm_ep_id)
        result_message = ""
        
        if mark_status == 0:
            result_message = f'已看过，不再重复标记'
            logger.info(f'bgm: {item.title} S{item.season:02d}E{item.episode:02d} {result_message}')
        elif mark_status == 1:
            result_message = f'已标记为看过'
            logger.info(f'bgm: {item.title} S{item.season:02d}E{item.episode:02d} {result_message} https://bgm.tv/ep/{bgm_ep_id}')
        else:
            result_message = f'已添加到收藏并标记为看过'
            logger.info(f'bgm: {item.title} 已添加到收藏 https://bgm.tv/subject/{bgm_se_id}')
            logger.info(f'bgm: {item.title} S{item.season:02d}E{item.episode:02d} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}')

        # 记录同步成功到数据库
        log_sync_record(
            user_name=item.user_name,
            title=item.title,
            ori_title=item.ori_title,
            season=item.season,
            episode=item.episode,
            subject_id=bgm_se_id,
            episode_id=bgm_ep_id,
            status="success",
            message=result_message,
            source=actual_source
        )

        return {
            "status": "success", 
            "message": result_message,
            "data": {
                "title": item.title,
                "season": item.season,
                "episode": item.episode,
                "subject_id": bgm_se_id,
                "episode_id": bgm_ep_id
            }
        }
    except Exception as e:
        logger.error(f'自定义同步处理出错: {e}')
        
        # 记录同步失败到数据库
        log_sync_record(
            user_name=item.user_name if 'item' in locals() else "unknown",
            title=item.title if 'item' in locals() else "unknown",
            ori_title=item.ori_title if 'item' in locals() else "",
            season=item.season if 'item' in locals() else 0,
            episode=item.episode if 'item' in locals() else 0,
            status="error",
            message=str(e),
            source=actual_source if 'actual_source' in locals() else source
        )
        
        response.status_code = 500
        return {"status": "error", "message": f"处理失败: {str(e)}"}


# 从Plex数据中提取CustomItem
def extract_plex_data(plex_data: Dict[str, Any]) -> CustomItem:
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "originallyAvailableAt" in plex_data["Metadata"] and plex_data["Metadata"]["originallyAvailableAt"]:
        release_date = plex_data["Metadata"]["originallyAvailableAt"]
    else:
        logger.debug(f'未找到originallyAvailableAt字段，将尝试从bangumi-data获取日期信息')

    # 重新组装数据
    return CustomItem(
        media_type=plex_data["Metadata"]["type"],
        title=plex_data["Metadata"]["grandparentTitle"],
        ori_title=plex_data["Metadata"]["originalTitle"],
        season=plex_data["Metadata"]["parentIndex"],
        episode=plex_data["Metadata"]["index"],
        release_date=release_date,
        user_name=plex_data["Account"]["title"]
    )


# 从Emby数据中提取CustomItem
def extract_emby_data(emby_data: Dict[str, Any]) -> CustomItem:
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "PremiereDate" in emby_data["Item"]:
        release_date = emby_data["Item"]["PremiereDate"][:10]
    else:
        logger.debug(f'未找到PremiereDate字段，将尝试从bangumi-data获取日期信息')
    
    # 重新组装数据
    return CustomItem(
        media_type=emby_data["Item"]["Type"].lower(),
        title=emby_data["Item"]["SeriesName"],
        ori_title=" ",
        season=emby_data["Item"]["ParentIndexNumber"],
        episode=emby_data["Item"]["IndexNumber"],
        release_date=release_date,
        user_name=emby_data["User"]["Name"]
    )


# 从Jellyfin数据中提取CustomItem
def extract_jellyfin_data(jellyfin_data: Dict[str, Any]) -> CustomItem:
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "release_date" in jellyfin_data and jellyfin_data["release_date"]:
        release_date = jellyfin_data["release_date"]
    else:
        logger.debug(f'未找到release_date字段，将尝试从bangumi-data获取日期信息')

    # 重新组装数据
    return CustomItem(
        media_type=jellyfin_data["media_type"].lower(),
        title=jellyfin_data["title"],
        ori_title=jellyfin_data["ori_title"],
        season=jellyfin_data["season"],
        episode=jellyfin_data["episode"],
        release_date=release_date,
        user_name=jellyfin_data["user_name"]
    )


# Plex同步
@app.post("/Plex")
async def plex_sync(plex_request: Request):
    try:
        json_str = await plex_request.body()
        plex_data = json.loads(extract_plex_json(json_str))

        # 检查同步类型是否为看过
        if plex_data["event"] != 'media.scrobble':
            logger.debug(f'事件类型{plex_data["event"]}无需同步，跳过')
            return

        logger.debug(f'接收到Plex同步请求：{plex_data["event"]} {plex_data["Account"]["title"]} '
                    f'S{plex_data["Metadata"]["parentIndex"]:02d}E{plex_data["Metadata"]["index"]:02d} '
                    f'{plex_data["Metadata"]["grandparentTitle"]}')

        # 提取数据并调用自定义同步
        custom_item = extract_plex_data(plex_data)
        logger.debug(f'Plex重新组装JSON报文：{custom_item}')
        
        # 直接传入source参数
        return await custom_sync(custom_item, Response(), source="plex")
    except Exception as e:
        logger.error(f'Plex同步处理出错: {e}')
        return {"status": "error", "message": f"处理失败: {str(e)}"}


# Emby同步
@app.post("/Emby")
async def emby_sync(emby_request: Request):
    try:
        # 获取请求内容
        body = await emby_request.body()
        body_str = body.decode('utf-8')
        
        # 检查内容格式并进行相应处理
        if body_str.startswith('{') and body_str.endswith('}'):
            try:
                # 尝试作为JSON解析
                emby_data = json.loads(body_str)
            except json.JSONDecodeError:
                # 如果JSON解析失败，尝试作为Python字典字符串解析
                try:
                    import ast
                    emby_data = ast.literal_eval(body_str)
                except (SyntaxError, ValueError) as e:
                    logger.error(f'无法解析Emby请求数据: {e}')
                    return {"status": "error", "message": f"数据格式错误: {str(e)}"}
        else:
            logger.error(f'Emby请求数据格式无效: {body_str[:100]}...')
            return {"status": "error", "message": "无效的请求格式"}
        
        # 记录接收到的数据
        logger.debug(f'接收到Emby同步请求：{emby_data}')

        # 验证必要字段是否存在
        required_fields = ["Event", "Item", "User"]
        for field in required_fields:
            if field not in emby_data:
                logger.error(f'Emby请求缺少必要字段: {field}')
                return {"status": "error", "message": f"请求缺少必要字段: {field}"}

        # 检查同步类型是否为看过
        if emby_data["Event"] != 'item.markplayed' and emby_data["Event"] != 'playback.stop':
            logger.debug(f'事件类型{emby_data["Event"]}无需同步，跳过')
            return {"status": "ignored", "message": f"事件类型{emby_data['Event']}无需同步"}

        # 检查Item中必要字段
        item_required_fields = ["Type", "SeriesName", "ParentIndexNumber", "IndexNumber"]
        for field in item_required_fields:
            if field not in emby_data["Item"]:
                logger.error(f'Emby Item缺少必要字段: {field}')
                return {"status": "error", "message": f"Item缺少必要字段: {field}"}

        # 如果是播放停止事件,只有播放完成才判断为看过
        if emby_data["Event"] == 'playback.stop':
            if "PlaybackInfo" not in emby_data or "PlayedToCompletion" not in emby_data["PlaybackInfo"]:
                logger.debug(f'播放停止事件缺少PlaybackInfo.PlayedToCompletion字段，跳过')
                return {"status": "ignored", "message": "播放信息不完整"}
                
            if emby_data["PlaybackInfo"]["PlayedToCompletion"] is not True:
                logger.debug(f'{emby_data["Item"]["SeriesName"]} S{emby_data["Item"]["ParentIndexNumber"]:02d}E{emby_data["Item"]["IndexNumber"]:02d}未播放完成，跳过')
                return {"status": "ignored", "message": "未播放完成"}

        # 提取数据并调用自定义同步
        custom_item = extract_emby_data(emby_data)
        logger.debug(f'Emby重新组装JSON报文：{custom_item}')
        return await custom_sync(custom_item, Response(), source="emby")
    except Exception as e:
        logger.error(f'Emby同步处理出错: {e}')
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "message": f"处理失败: {str(e)}"}


# Jellyfin同步
@app.post("/Jellyfin")
async def jellyfin_sync(jellyfin_request: Request):
    try:
        json_str = await jellyfin_request.body()
        jellyfin_data = json.loads(json_str)
        logger.debug(f'接收到Jellyfin同步请求：{jellyfin_data}')

        # 检查事件类型是否为停止播放
        if jellyfin_data["NotificationType"] != 'PlaybackStop':
            logger.debug(f'事件类型{jellyfin_data["NotificationType"]}无需同步，跳过')
            return

        # 检查同步类型是否为看过
        if jellyfin_data["PlayedToCompletion"] == 'False':
            logger.debug(f'是否播完：{jellyfin_data["PlayedToCompletion"]}，无需同步，跳过')
            return

        # 提取数据并调用自定义同步
        custom_item = extract_jellyfin_data(jellyfin_data)
        logger.debug(f'Jellyfin重新组装JSON报文：{custom_item}')
        return await custom_sync(custom_item, Response(), source="jellyfin")
    except Exception as e:
        logger.error(f'Jellyfin同步处理出错: {e}')
        return {"status": "error", "message": f"处理失败: {str(e)}"}


# 手动刷新映射配置缓存
@app.post("/reload-mappings", status_code=200)
async def reload_mappings_endpoint():
    """手动刷新自定义映射配置缓存的API端点"""
    try:
        mappings = reload_custom_mappings()
        return {
            "status": "success",
            "message": "映射配置已重新加载",
            "data": {
                "mappings_count": len(mappings),
                "file_path": _mapping_file_path,
                "last_modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_last_modified_time)) if _last_modified_time else None
            }
        }
    except Exception as e:
        logger.error(f'重新加载映射配置失败: {e}')
        return {
            "status": "error", 
            "message": f"重新加载失败: {str(e)}"
        }


# 获取当前映射配置状态
@app.get("/mappings-status", status_code=200)
async def get_mappings_status():
    """获取当前自定义映射配置状态的API端点"""
    try:
        mappings = load_custom_mappings()
        return {
            "status": "success",
            "data": {
                "mappings_count": len(mappings),
                "file_path": _mapping_file_path,
                "last_modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_last_modified_time)) if _last_modified_time else None,
                "cached": bool(_cached_mappings),
                "mappings": mappings
            }
        }
    except Exception as e:
        logger.error(f'获取映射配置状态失败: {e}')
        return {
            "status": "error",
            "message": f"获取状态失败: {str(e)}"
        }


# 重新加载多账号配置缓存的函数
def reload_multi_account_configs():
    """重新加载多账号配置缓存"""
    global _bangumi_configs_cache, _user_mappings_cache
    _bangumi_configs_cache = None
    _user_mappings_cache = None

# 手动刷新多账号配置缓存
@app.post("/reload-accounts", status_code=200)
async def reload_accounts_endpoint():
    """手动刷新多账号配置缓存的API端点"""
    try:
        reload_multi_account_configs()
        bangumi_configs = get_multi_account_configs()
        user_mappings = get_user_mappings()
        
        return {
            "status": "success",
            "message": "多账号配置已重新加载",
            "data": {
                "bangumi_accounts_count": len(bangumi_configs),
                "user_mappings_count": len(user_mappings),
                "bangumi_accounts": list(bangumi_configs.keys()),
                "user_mappings": user_mappings
            }
        }
    except Exception as e:
        logger.error(f'重新加载多账号配置失败: {e}')
        return {
            "status": "error", 
            "message": f"重新加载失败: {str(e)}"
        }


# =========================== Web管理界面 ===========================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """主页面 - 仪表板"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """配置管理页面"""
    return templates.TemplateResponse("config.html", {"request": request})

@app.get("/records", response_class=HTMLResponse)
async def records_page(request: Request):
    """同步记录页面"""
    return templates.TemplateResponse("records.html", {"request": request})

@app.get("/mappings", response_class=HTMLResponse)
async def mappings_page(request: Request):
    """映射管理页面"""
    return templates.TemplateResponse("mappings.html", {"request": request})

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """日志管理页面"""
    return templates.TemplateResponse("logs.html", {"request": request})



# =========================== API端点 ===========================

@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    try:
        # 强制重新读取配置文件
        configs.update()
        reload_multi_account_configs()
        
        config_data = {
            "bangumi": {
                "username": configs.raw.get('bangumi', 'username', fallback=''),
                "access_token": configs.raw.get('bangumi', 'access_token', fallback=''),
                "private": configs.raw.getboolean('bangumi', 'private', fallback=False),
            },
            "sync": {
                "mode": configs.raw.get('sync', 'mode', fallback='single'),
                "single_username": configs.raw.get('sync', 'single_username', fallback=''),
                "blocked_keywords": configs.raw.get('sync', 'blocked_keywords', fallback=''),
            },
            "dev": {
                "script_proxy": configs.raw.get('dev', 'script_proxy', fallback=''),
                "debug": configs.raw.getboolean('dev', 'debug', fallback=False),
            },
            "bangumi_data": {
                "enabled": configs.raw.getboolean('bangumi-data', 'enabled', fallback=True),
                "use_cache": configs.raw.getboolean('bangumi-data', 'use_cache', fallback=True),
                "cache_ttl_days": configs.raw.getint('bangumi-data', 'cache_ttl_days', fallback=7),
                "data_url": configs.raw.get('bangumi-data', 'data_url', fallback='https://unpkg.com/bangumi-data@0.3/dist/data.json'),
                "local_cache_path": configs.raw.get('bangumi-data', 'local_cache_path', fallback='./bangumi_data_cache.json'),
            }
        }
        
        # 获取多账号配置
        bangumi_configs = get_multi_account_configs()
        
        config_data["multi_accounts"] = bangumi_configs
        
        return {"status": "success", "data": config_data}
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")

@app.post("/api/config")
async def update_config(request: Request):
    """更新配置"""
    try:
        data = await request.json()
        
        # 读取现有配置
        config_path = configs.active_config_path
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8-sig')
        
        # 更新配置
        if "bangumi" in data:
            if not config.has_section('bangumi'):
                config.add_section('bangumi')
            for key, value in data["bangumi"].items():
                if key == "private":
                    config.set('bangumi', key, str(value).lower())
                else:
                    config.set('bangumi', key, str(value))
        
        if "sync" in data:
            if not config.has_section('sync'):
                config.add_section('sync')
            for key, value in data["sync"].items():
                config.set('sync', key, str(value))
        
        if "dev" in data:
            if not config.has_section('dev'):
                config.add_section('dev')
            for key, value in data["dev"].items():
                if key == "debug":
                    config.set('dev', key, str(value).lower())
                else:
                    config.set('dev', key, str(value))
        
        if "bangumi_data" in data:
            if not config.has_section('bangumi-data'):
                config.add_section('bangumi-data')
            for key, value in data["bangumi_data"].items():
                if key in ["enabled", "use_cache"]:
                    config.set('bangumi-data', key, str(value).lower())
                else:
                    config.set('bangumi-data', key, str(value))
        
        # 处理多账号配置
        if "multi_accounts" in data:
            # 先删除所有现有的bangumi-*段
            sections_to_remove = [s for s in config.sections() if s.startswith('bangumi-')]
            for section in sections_to_remove:
                config.remove_section(section)
            
            # 添加新的多账号配置
            account_counter = 1
            for account_name, account_config in data["multi_accounts"].items():
                # 自动生成配置段名称，格式为 bangumi-userN
                section_name = f"bangumi-user{account_counter}"
                if not config.has_section(section_name):
                    config.add_section(section_name)
                
                # 添加账号备注字段
                if account_name and not account_name.startswith('account_'):  # 如果有有效的备注名称，保存到配置中
                    config.set(section_name, 'display_name', account_name)
                
                for key, value in account_config.items():
                    if key == "private":
                        config.set(section_name, key, str(value).lower())
                    else:
                        config.set(section_name, key, str(value))
                
                account_counter += 1
        
        # 清理sync段中的旧用户映射配置
        if config.has_section('sync'):
            # 删除现有的用户映射
            items_to_remove = []
            for key, value in config.items('sync'):
                if key not in ['mode', 'single_username', 'blocked_keywords']:
                    items_to_remove.append(key)
            for key in items_to_remove:
                config.remove_option('sync', key)
        
        # 保存配置文件
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        
        # 重新加载配置
        configs.update()
        reload_multi_account_configs()
        
        # 清除所有函数缓存
        if hasattr(get_bangumi_api, 'cache_clear'):
            get_bangumi_api.cache_clear()
        if hasattr(get_bangumi_data, 'cache_clear'):
            get_bangumi_data.cache_clear()
        
        # 强制重新加载映射配置
        reload_custom_mappings()
        
        return {"status": "success", "message": "配置已更新"}
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")

@app.get("/api/records")
async def get_sync_records(limit: int = 100, offset: int = 0, status: str = None, user_name: str = None, source: str = None):
    """获取同步记录"""
    try:
        conn = sqlite3.connect("sync_records.db")
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = []
        params = []
        
        if status:
            where_conditions.append("status = ?")
            params.append(status)
        
        if user_name:
            where_conditions.append("user_name = ?")
            params.append(user_name)
        
        if source:
            where_conditions.append("source = ?")
            params.append(source)
        
        where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        # 获取总数
        count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        # 获取记录
        query = f"""
            SELECT id, timestamp, user_name, title, ori_title, season, episode, 
                   subject_id, episode_id, status, message, source
            FROM sync_records{where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, params + [limit, offset])
        
        records = []
        for row in cursor.fetchall():
            records.append({
                "id": row[0],
                "timestamp": row[1],
                "user_name": row[2],
                "title": row[3],
                "ori_title": row[4],
                "season": row[5],
                "episode": row[6],
                "subject_id": row[7],
                "episode_id": row[8],
                "status": row[9],
                "message": row[10],
                "source": row[11]
            })
        
        conn.close()
        
        return {
            "status": "success",
            "data": {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset
            }
        }
    except Exception as e:
        logger.error(f"获取同步记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录失败: {str(e)}")

@app.get("/api/stats")
async def get_sync_stats():
    """获取同步统计信息"""
    try:
        conn = sqlite3.connect("sync_records.db")
        cursor = conn.cursor()
        
        # 总同步次数
        cursor.execute("SELECT COUNT(*) FROM sync_records")
        total_syncs = cursor.fetchone()[0]
        
        # 成功同步次数
        cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'success'")
        success_syncs = cursor.fetchone()[0]
        
        # 失败同步次数
        cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'error'")
        error_syncs = cursor.fetchone()[0]
        
        # 今日同步次数
        cursor.execute("SELECT COUNT(*) FROM sync_records WHERE DATE(timestamp) = DATE('now')")
        today_syncs = cursor.fetchone()[0]
        
        # 用户统计
        cursor.execute("SELECT user_name, COUNT(*) FROM sync_records GROUP BY user_name ORDER BY COUNT(*) DESC")
        user_stats = [{"user": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        # 最近7天统计
        cursor.execute("""
            SELECT DATE(timestamp) as date, COUNT(*) as count
            FROM sync_records 
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY date
        """)
        daily_stats = [{"date": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "status": "success",
            "data": {
                "total_syncs": total_syncs,
                "success_syncs": success_syncs,
                "error_syncs": error_syncs,
                "today_syncs": today_syncs,
                "success_rate": round(success_syncs / total_syncs * 100, 2) if total_syncs > 0 else 0,
                "user_stats": user_stats,
                "daily_stats": daily_stats
            }
        }
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")

@app.get("/api/mappings")
async def get_custom_mappings():
    """获取自定义映射"""
    try:
        mappings = load_custom_mappings()
        return {
            "status": "success",
            "data": {
                "mappings": mappings,
                "count": len(mappings)
            }
        }
    except Exception as e:
        logger.error(f"获取自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取自定义映射失败: {str(e)}")

@app.post("/api/mappings")
async def update_custom_mappings(request: Request):
    """更新自定义映射"""
    try:
        data = await request.json()
        mappings = data.get("mappings", {})
        
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
        reload_custom_mappings()
        
        return {"status": "success", "message": "自定义映射已更新"}
    except Exception as e:
        logger.error(f"更新自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新自定义映射失败: {str(e)}")

@app.get("/api/logs")
async def get_logs(level: str = None, search: str = None, limit: str = "100"):
    """获取日志内容"""
    try:
        log_file_path = "log.txt"
        
        if not os.path.exists(log_file_path):
            return {
                "status": "success",
                "data": {
                    "content": "",
                    "stats": {
                        "size": 0,
                        "lines": 0,
                        "modified": None,
                        "errors": 0
                    }
                }
            }
        
        # 获取文件统计信息
        file_stats = os.stat(log_file_path)
        file_size = file_stats.st_size
        file_modified = file_stats.st_mtime
        
        # 读取日志内容
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        # 统计错误数量
        error_count = sum(1 for line in lines if 'ERROR' in line.upper())
        
        # 应用筛选
        if level:
            lines = [line for line in lines if level.upper() in line.upper()]
        
        if search:
            lines = [line for line in lines if search.lower() in line.lower()]
        
        # 限制行数
        if limit != "all":
            try:
                limit_num = int(limit)
                lines = lines[-limit_num:] if len(lines) > limit_num else lines
            except ValueError:
                pass
        
        content = ''.join(lines)
        
        return {
            "status": "success",
            "data": {
                "content": content,
                "stats": {
                    "size": file_size,
                    "lines": len(lines) if not level and not search else len(lines),
                    "modified": file_modified * 1000,  # 转换为毫秒
                    "errors": error_count
                }
            }
        }
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取日志失败: {str(e)}")

@app.post("/api/logs/clear")
async def clear_logs(request: Request):
    """清空日志"""
    try:
        data = await request.json()
        create_backup = data.get("backup", False)
        
        log_file_path = "log.txt"
        
        if not os.path.exists(log_file_path):
            return {"status": "success", "message": "日志文件不存在"}
        
        # 创建备份
        if create_backup:
            backup_path = f"log_backup_{int(time.time())}.txt"
            import shutil
            shutil.copy2(log_file_path, backup_path)
            logger.info(f"日志已备份到: {backup_path}")
        
        # 清空日志文件
        with open(log_file_path, 'w', encoding='utf-8') as f:
            f.write("")
        
        logger.info("日志已清空")
        
        return {
            "status": "success", 
            "message": "日志已清空" + (f"，备份已保存" if create_backup else "")
        }
    except Exception as e:
        logger.error(f"清空日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")

@app.post("/api/config/backup")
async def backup_config():
    """备份当前配置"""
    try:
        # 强制重新读取配置文件
        configs.update()
        
        config_data = {
            "bangumi": {
                "username": configs.raw.get('bangumi', 'username', fallback=''),
                "access_token": configs.raw.get('bangumi', 'access_token', fallback=''),
                "private": configs.raw.getboolean('bangumi', 'private', fallback=False),
            },
            "sync": {
                "mode": configs.raw.get('sync', 'mode', fallback='single'),
                "single_username": configs.raw.get('sync', 'single_username', fallback=''),
                "blocked_keywords": configs.raw.get('sync', 'blocked_keywords', fallback=''),
            },
            "dev": {
                "script_proxy": configs.raw.get('dev', 'script_proxy', fallback=''),
                "debug": configs.raw.getboolean('dev', 'debug', fallback=False),
            },
                        "bangumi_data": {
                "enabled": configs.raw.getboolean('bangumi-data', 'enabled', fallback=True),
                "use_cache": configs.raw.getboolean('bangumi-data', 'use_cache', fallback=True),
                "cache_ttl_days": configs.raw.getint('bangumi-data', 'cache_ttl_days', fallback=7),
                "data_url": configs.raw.get('bangumi-data', 'data_url', fallback='https://unpkg.com/bangumi-data@0.3/dist/data.json'),
                "local_cache_path": configs.raw.get('bangumi-data', 'local_cache_path', fallback='./bangumi_data_cache.json'),
            }
        }

        # 获取多账号配置
        bangumi_configs = get_multi_account_configs()
        
        config_data["multi_accounts"] = bangumi_configs
        
        # 创建备份目录
        backup_dir = "config_backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        # 生成备份文件名
        timestamp = int(time.time())
        backup_filename = f"config_backup_{timestamp}.json"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        # 保存备份文件
        backup_data = {
            "backup_info": {
                "created_at": time.time(),
                "version": "1.0",
                "description": "配置备份文件"
            },
            "config": config_data
        }
        
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"配置已备份到: {backup_path}")
        
        return {
            "status": "success",
            "message": "配置备份成功",
            "data": {
                "filename": backup_filename,
                "path": backup_path,
                "config": config_data
            }
        }
    except Exception as e:
        logger.error(f"备份配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"备份配置失败: {str(e)}")

@app.get("/api/config/backups")
async def get_config_backups():
    """获取配置备份列表"""
    try:
        backup_dir = "config_backups"
        backups = []
        
        if os.path.exists(backup_dir):
            for filename in os.listdir(backup_dir):
                if filename.endswith('.json'):
                    file_path = os.path.join(backup_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            backup_data = json.load(f)
                        
                        file_stats = os.stat(file_path)
                        backup_info = backup_data.get('backup_info', {})
                        
                        backups.append({
                            "filename": filename,
                            "name": filename.replace('.json', '').replace('config_backup_', '配置备份_'),
                            "created_at": backup_info.get('created_at', file_stats.st_mtime) * 1000,
                            "size": file_stats.st_size,
                            "description": backup_info.get('description', '配置备份文件')
                        })
                    except Exception as e:
                        logger.warning(f"读取备份文件失败 {filename}: {e}")
                        continue
        
        # 按创建时间倒序排列
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        
        return {
            "status": "success",
            "data": {
                "backups": backups,
                "count": len(backups)
            }
        }
    except Exception as e:
        logger.error(f"获取配置备份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置备份列表失败: {str(e)}")

@app.get("/api/config/backup/{filename}")
async def get_config_backup(filename: str):
    """获取指定备份文件的内容"""
    try:
        backup_dir = "config_backups"
        backup_path = os.path.join(backup_dir, filename)
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        with open(backup_path, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        return {
            "status": "success",
            "data": backup_data
        }
    except Exception as e:
        logger.error(f"获取备份文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取备份文件失败: {str(e)}")

@app.post("/api/config/restore/{filename}")
async def restore_config(filename: str):
    """恢复配置"""
    try:
        backup_dir = "config_backups"
        backup_path = os.path.join(backup_dir, filename)
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        # 读取备份文件
        with open(backup_path, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        config_data = backup_data.get('config', {})
        
        # 找到配置文件路径
        config_paths = [
            './config.ini',
            '/app/config/config.ini',
            '/app/config.ini'
        ]
        
        config_path = None
        for path in config_paths:
            if os.path.exists(path):
                config_path = path
                break
        
        if not config_path:
            config_path = './config.ini'
        
        # 创建当前配置的备份
        current_backup_path = f"config_backup_before_restore_{int(time.time())}.json"
        current_backup_full_path = os.path.join(backup_dir, current_backup_path)
        
        # 读取当前配置
        configs.update()
        current_config = {
            "bangumi": {
                "username": configs.raw.get('bangumi', 'username', fallback=''),
                "access_token": configs.raw.get('bangumi', 'access_token', fallback=''),
                "private": configs.raw.getboolean('bangumi', 'private', fallback=False),
            },
            "sync": {
                "mode": configs.raw.get('sync', 'mode', fallback='single'),
                "single_username": configs.raw.get('sync', 'single_username', fallback=''),
                "blocked_keywords": configs.raw.get('sync', 'blocked_keywords', fallback=''),
            },
            "dev": {
                "script_proxy": configs.raw.get('dev', 'script_proxy', fallback=''),
                "debug": configs.raw.getboolean('dev', 'debug', fallback=False),
            },
                        "bangumi_data": {
                "enabled": configs.raw.getboolean('bangumi-data', 'enabled', fallback=True),
                "use_cache": configs.raw.getboolean('bangumi-data', 'use_cache', fallback=True),
                "cache_ttl_days": configs.raw.getint('bangumi-data', 'cache_ttl_days', fallback=7),
                "data_url": configs.raw.get('bangumi-data', 'data_url', fallback='https://unpkg.com/bangumi-data@0.3/dist/data.json'),
                "local_cache_path": configs.raw.get('bangumi-data', 'local_cache_path', fallback='./bangumi_data_cache.json'),
            }
        }

        current_backup_data = {
            "backup_info": {
                "created_at": time.time(),
                "version": "1.0",
                "description": "恢复前自动备份"
            },
            "config": current_config
        }
        
        with open(current_backup_full_path, 'w', encoding='utf-8') as f:
            json.dump(current_backup_data, f, ensure_ascii=False, indent=2)
        
        # 恢复配置
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        
        # 清空现有配置
        for section in config.sections():
            config.remove_section(section)
        
        # 应用备份配置
        if "bangumi" in config_data:
            config.add_section('bangumi')
            for key, value in config_data["bangumi"].items():
                if key == "private":
                    config.set('bangumi', key, str(value).lower())
                else:
                    config.set('bangumi', key, str(value))
        
        if "sync" in config_data:
            config.add_section('sync')
            for key, value in config_data["sync"].items():
                config.set('sync', key, str(value))
        
        if "dev" in config_data:
            config.add_section('dev')
            for key, value in config_data["dev"].items():
                if key == "debug":
                    config.set('dev', key, str(value).lower())
                else:
                    config.set('dev', key, str(value))
        
        if "bangumi_data" in config_data:
            config.add_section('bangumi-data')
            for key, value in config_data["bangumi_data"].items():
                if key in ["enabled", "use_cache"]:
                    config.set('bangumi-data', key, str(value).lower())
                else:
                    config.set('bangumi-data', key, str(value))
        
        # 处理多账号配置
        if "multi_accounts" in config_data:
            for account_name, account_config in config_data["multi_accounts"].items():
                config.add_section(account_name)
                for key, value in account_config.items():
                    if key == "private":
                        config.set(account_name, key, str(value).lower())
                    else:
                        config.set(account_name, key, str(value))
        
        # 保存配置文件
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        
        # 重新加载配置
        configs.update()
        reload_multi_account_configs()
        reload_custom_mappings()
        
        # 清除缓存
        if hasattr(get_bangumi_api, 'cache_clear'):
            get_bangumi_api.cache_clear()
        if hasattr(get_bangumi_data, 'cache_clear'):
            get_bangumi_data.cache_clear()
        
        logger.info(f"配置已从 {filename} 恢复，当前配置已备份到 {current_backup_path}")
        
        return {
            "status": "success",
            "message": "配置恢复成功",
            "data": {
                "restored_from": filename,
                "current_backup": current_backup_path
            }
        }
    except Exception as e:
        logger.error(f"恢复配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"恢复配置失败: {str(e)}")

@app.delete("/api/mappings/{title}")
async def delete_custom_mapping(title: str):
    """删除自定义映射"""
    try:
        mappings = load_custom_mappings()
        if title in mappings:
            del mappings[title]
            
            # 更新配置文件
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
            
            if mapping_file_path:
                config_data = {
                    "_comment": "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目",
                    "_format": "番剧名: bangumi_subject_id",
                    "_note": "bangumi_subject_id需要配置第一季的，程序会自动往后找",
                    "mappings": mappings
                }
                
                with open(mapping_file_path, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                
                # 重新加载映射
                reload_custom_mappings()
                
                return {"status": "success", "message": "映射已删除"}
        
        return {"status": "error", "message": "映射不存在"}
    except Exception as e:
        logger.error(f"删除自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除自定义映射失败: {str(e)}")

@app.post("/api/test-sync")
async def test_sync(request: Request):
    """测试同步功能"""
    try:
        data = await request.json()
        
        # 创建测试项目
        test_item = CustomItem(
            media_type="episode",
            title=data.get("title", ""),
            ori_title=data.get("ori_title", ""),
            season=data.get("season", 1),
            episode=data.get("episode", 1),
            release_date=data.get("release_date", ""),
            user_name=data.get("user_name", "test_user"),
            source=data.get("source", "test")  # 支持自定义source
        )
        
        # 执行同步测试
        response = Response()
        result = await custom_sync(test_item, response, source="test")
        
        return result
    except Exception as e:
        logger.error(f"测试同步失败: {e}")
        raise HTTPException(status_code=500, detail=f"测试同步失败: {str(e)}")

@app.post("/api/config/backups/cleanup")
async def cleanup_config_backups(request: Request):
    """清理配置备份文件"""
    try:
        data = await request.json()
        strategy = data.get('strategy', 'recent')
        
        backup_dir = "config_backups"
        if not os.path.exists(backup_dir):
            return {"status": "success", "data": {"deleted_count": 0}, "message": "备份目录不存在"}
        
        # 获取所有备份文件
        backup_files = []
        for filename in os.listdir(backup_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(backup_dir, filename)
                file_stats = os.stat(file_path)
                backup_files.append({
                    "filename": filename,
                    "path": file_path,
                    "mtime": file_stats.st_mtime,
                    "size": file_stats.st_size
                })
        
        # 按修改时间排序
        backup_files.sort(key=lambda x: x['mtime'], reverse=True)
        
        files_to_delete = []
        
        if strategy == 'recent':
            # 保留最近的N个文件
            keep_count = data.get('keep_count', 5)
            files_to_delete = backup_files[keep_count:]
        elif strategy == 'date':
            # 删除N天前的文件
            keep_days = data.get('keep_days', 30)
            cutoff_time = time.time() - (keep_days * 24 * 60 * 60)
            files_to_delete = [f for f in backup_files if f['mtime'] < cutoff_time]
        elif strategy == 'all':
            # 删除所有文件
            files_to_delete = backup_files
        
        # 删除文件
        deleted_count = 0
        for file_info in files_to_delete:
            try:
                os.remove(file_info['path'])
                deleted_count += 1
                logger.info(f"删除备份文件: {file_info['filename']}")
            except Exception as e:
                logger.warning(f"删除备份文件失败 {file_info['filename']}: {e}")
        
        return {
            "status": "success",
            "data": {
                "deleted_count": deleted_count,
                "total_files": len(backup_files),
                "remaining_files": len(backup_files) - deleted_count
            },
            "message": f"成功删除 {deleted_count} 个备份文件"
        }
    except Exception as e:
        logger.error(f"清理备份文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理备份文件失败: {str(e)}")

@app.delete("/api/config/backup/{filename}")
async def delete_config_backup(filename: str):
    """删除指定的配置备份文件"""
    try:
        backup_dir = "config_backups"
        backup_path = os.path.join(backup_dir, filename)
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=404, detail="备份文件不存在")
        
        # 检查文件名安全性，防止路径遍历攻击
        if not filename.endswith('.json') or '/' in filename or '\\' in filename:
            raise HTTPException(status_code=400, detail="无效的文件名")
        
        # 删除文件
        os.remove(backup_path)
        logger.info(f"删除备份文件: {filename}")
        
        return {
            "status": "success",
            "message": f"成功删除备份文件: {filename}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除备份文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除备份文件失败: {str(e)}")


# 配置Uvicorn日志
uvicorn_logging_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "uvicorn": {
            "level": logger.level(),
        },
        "uvicorn.access": {
            "level": "WARNING",
        },
    },
}

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=uvicorn_logging_config
    )

