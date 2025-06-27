import json
from typing import Dict, Optional, Any
import uvicorn
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from functools import lru_cache
import os
import time

from utils.configs import configs, MyLogger
from utils.bangumi_api import BangumiApi
from utils.data_util import extract_plex_json
from utils.bangumi_data import BangumiData

logger = MyLogger()

app = FastAPI()


class CustomItem(BaseModel):
    media_type: str
    title: str
    ori_title: str = None
    season: int
    episode: int
    release_date: str
    user_name: str


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
            }
            # 只保存有效的配置（至少有用户名和access_token）
            if config['username'] and config['access_token']:
                bangumi_configs[section_name] = config
                logger.debug(f'加载多账号配置: {section_name}')
    
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
    
    # 从sync段获取用户映射配置
    if configs.raw.has_section('sync'):
        for key, value in configs.raw.items('sync'):
            # 跳过已知的配置项
            if key in ['mode', 'single_username']:
                continue
            # 其他的键值对都视为用户映射
            if value.strip():
                user_mappings[key] = value.strip()
                logger.debug(f'用户映射: {key} -> {value}')
    
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
async def custom_sync(item: CustomItem, response: Response):
    try:
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
        return await custom_sync(custom_item, Response())
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
        result = await custom_sync(custom_item, Response())
        return result
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
        return await custom_sync(custom_item, Response())
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

