import json

import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel

from utils.configs import configs, MyLogger
from utils.bangumi_api import BangumiApi
from utils.data_util import extract_plex_json

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


# 自定义同步
@app.post("/Custom")
async def custom_sync(item: CustomItem):
    logger.info(f'接收到同步请求：{item}')

    # 检查记录类型是否为单集
    if item.media_type != 'episode':
        logger.error(f'同步类型{item.media_type}不支持，跳过')
        return
    # 检查标题不能为空
    if not item.title:
        logger.error(f'同步名称为空，跳过')
        return
    # 检查季度是否为整数或为0
    if item.season == 0:
        logger.error(f'不支持SP标记同步，跳过')
        return
    # 检查集数不能为0
    if item.episode == 0:
        logger.error(f'集数{item.episode}不能为0，跳过')
        return

    # 根据同步模式判断是否跳过其它用户
    mode = configs.raw.get('sync', 'mode', fallback='single')
    if mode == 'single':
        single_username = configs.raw.get('sync', 'single_username', fallback='')
        if single_username:
            if item.user_name != single_username:
                logger.debug(f'非配置同步用户，跳过')
                return
        else:
            logger.error(f'未设置同步用户single_username，请检查config.ini配置')
            return

    bgm = BangumiApi(
        username=configs.raw.get('bangumi', 'username', fallback=''),
        access_token=configs.raw.get('bangumi', 'access_token', fallback=''),
        private=configs.raw.getboolean('bangumi', 'private', fallback=False),
        http_proxy=configs.raw.get('bangumi', 'script_proxy', fallback=''))

    # 尝试查询bangumi番剧基础信息
    bgm_data = bgm.bgm_search(title=item.title, ori_title=item.ori_title, premiere_date=item.release_date[:10])
    if not bgm_data:
        logger.error(f'bgm: 未查询到番剧信息，跳过\nbgm: {item.title=} {item.ori_title=} {item.release_date[:10]=}')
        return
    bgm_data = bgm_data[0]
    subject_id = bgm_data['id']
    # 尝试查询bangumi番剧指定季度指定集数信息
    bgm_se_id, bgm_ep_id = bgm.get_target_season_episode_id(
        subject_id=subject_id, target_season=item.season, target_ep=item.episode)
    if not bgm_ep_id:
        logger.error(f'bgm: {subject_id=} {item.season=} {item.episode=}, 不存在或集数过多，跳过')
        return

    logger.debug(f'bgm: 查询到 {bgm_data["name"]} (https://bgm.tv/subject/{bgm_se_id}) '
                 f'S0{item.season}E{item.episode} (https://bgm.tv/ep/{bgm_ep_id})')

    mark_status = bgm.mark_episode_watched(subject_id=bgm_se_id, ep_id=bgm_ep_id)
    if mark_status == 0:
        logger.info(f'bgm: {item.title} S0{item.season}E{item.episode} 已看过，不再重复标记')
    elif mark_status == 1:
        logger.info(f'bgm: {item.title} S0{item.season}E{item.episode} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}')
    else:
        logger.info(f'bgm: {bgm_data["name"]} 已添加到收藏 https://bgm.tv/subject/{bgm_se_id}')
        logger.info(f'bgm: {item.title} S0{item.season}E{item.episode} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}')

    return


# Plex同步
@app.post("/Plex")
async def plex_sync(plex_request: Request):
    json_str = await plex_request.body()
    plex_data = json.loads(extract_plex_json(json_str))
    logger.debug(f'接收到Plex同步请求：{plex_data["event"]} {plex_data["Account"]["title"]} '
                 f'S0{plex_data["Metadata"]["grandparentTitle"]} E{plex_data["Metadata"]["originalTitle"]} '
                 f'{plex_data["Metadata"]["parentIndex"]} {plex_data["Metadata"]["index"]}')

    # 检查同步类型是否为看过
    if plex_data["event"] != 'media.scrobble':
        logger.debug(f'事件类型{plex_data["event"]}无需同步，跳过')
        return

    # 重新组装 JSON 报文
    plex_json = {
        "media_type": plex_data["Metadata"]["type"],
        "title": plex_data["Metadata"]["grandparentTitle"],
        "ori_title": plex_data["Metadata"]["originalTitle"],
        "season": plex_data["Metadata"]["parentIndex"],
        "episode": plex_data["Metadata"]["index"],
        "release_date": plex_data["Metadata"]["originallyAvailableAt"],
        "user_name": plex_data["Account"]["title"]
    }

    logger.debug(f'重新组装 JSON 报文：{plex_json}')

    plex_json = CustomItem(**plex_json)
    # 重组成自定义标准格式后调用自定义同步
    await custom_sync(plex_json)


# Emby同步
@app.post("/Emby")
async def emby_sync(emby_data: dict):
    logger.debug(f'接收到Emby同步请求：{emby_data}')

    # 检查同步类型是否为看过
    if emby_data["Event"] != 'item.markplayed':
        logger.debug(f'事件类型{emby_data["Event"]}无需同步，跳过')
        return

    # 重新组装 JSON 报文
    emby_json = {
        "media_type": emby_data["Item"]["Type"].lower(),
        "title": emby_data["Item"]["SeriesName"],
        "ori_title": " ",
        "season": emby_data["Item"]["ParentIndexNumber"],
        "episode": emby_data["Item"]["IndexNumber"],
        "release_date": emby_data["Item"]["PremiereDate"][:10],
        "user_name": emby_data["User"]["Name"]
    }

    logger.debug(f'重新组装 JSON 报文：{emby_json}')

    emby_json = CustomItem(**emby_json)
    # 重组成自定义标准格式后调用自定义同步
    await custom_sync(emby_json)


@app.post("/Jellyfin")
async def jellyfin_sync(jellyfin_request: Request):
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

    # 重新组装 JSON 报文
    jellyfin_json = {
        "media_type": jellyfin_data["media_type"].lower(),
        "title": jellyfin_data["title"],
        "ori_title": jellyfin_data["ori_title"],
        "season": jellyfin_data["season"],
        "episode": jellyfin_data["episode"],
        "release_date": jellyfin_data["release_date"],
        "user_name": jellyfin_data["user_name"]
    }

    logger.debug(f'重新组装 JSON 报文：{jellyfin_json}')

    jellyfin_json = CustomItem(**jellyfin_json)
    # 重组成自定义标准格式后调用自定义同步
    await custom_sync(jellyfin_json)
