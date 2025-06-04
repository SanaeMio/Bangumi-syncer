import json
from typing import Dict, Optional, Any
import uvicorn
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from functools import lru_cache

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


# 创建BangumiApi实例的函数，使用缓存减少重复初始化
@lru_cache(maxsize=1)
def get_bangumi_api() -> BangumiApi:
    return BangumiApi(
        username=configs.raw.get('bangumi', 'username', fallback=''),
        access_token=configs.raw.get('bangumi', 'access_token', fallback=''),
        private=configs.raw.getboolean('bangumi', 'private', fallback=False),
        http_proxy=configs.raw.get('dev', 'script_proxy', fallback='')
    )


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
    return True


# 查找番剧ID
async def find_subject_id(item: CustomItem) -> tuple[Optional[str], bool]:
    """根据标题和日期查找番剧ID
    
    Returns:
        tuple: (subject_id, is_season_matched_id)
            subject_id: 番剧ID
            is_season_matched_id: 对于第二季及以上，该值为True表示ID可能已经是指定季度的ID
    """
    # 获取自定义映射
    mapping_item = item.title
    mapping_subject_id = configs.raw.get('bangumi-mapping', mapping_item, fallback='')
    
    if mapping_subject_id:
        logger.debug(f'匹配到自定义映射：{mapping_item}={mapping_subject_id}')
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
        bgm = get_bangumi_api()
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

        # 查找番剧ID及其是否为特定季度ID的标记
        subject_id, is_season_matched_id = await find_subject_id(item)
        if not subject_id:
            response.status_code = 404
            return {"status": "error", "message": "未找到匹配的番剧"}

        # 查询bangumi番剧指定季度指定集数信息
        bgm = get_bangumi_api()
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
        mark_status = bgm.mark_episode_watched(subject_id=bgm_se_id, ep_id=bgm_ep_id)
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
