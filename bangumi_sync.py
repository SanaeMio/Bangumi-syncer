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
async def plex_sync(request: Request):
    json_str = await request.body()
    json_data = json.loads(extract_plex_json(json_str))
    logger.debug(f'接收到Plex同步请求：{json_data["event"]} {json_data["Account"]["title"]} '
                 f'S0{json_data["Metadata"]["grandparentTitle"]} E{json_data["Metadata"]["originalTitle"]} '
                 f'{json_data["Metadata"]["parentIndex"]} {json_data["Metadata"]["index"]}')

    # 检查记录类型是否为单集
    if json_data["event"] != 'media.scrobble':
        logger.debug(f'事件类型{json_data["event"]}无需同步，跳过')
        return

    # 重新组装 JSON 报文
    reorganized_json = {
        "media_type": json_data["Metadata"]["type"],
        "title": json_data["Metadata"]["grandparentTitle"],
        "ori_title": json_data["Metadata"]["originalTitle"],
        "season": json_data["Metadata"]["parentIndex"],
        "episode": json_data["Metadata"]["index"],
        "release_date": json_data["Metadata"]["originallyAvailableAt"],
        "user_name": json_data["Account"]["title"]
    }

    logger.debug(f'重新组装 JSON 报文：{reorganized_json}')

    reorganized_json = CustomItem(**reorganized_json)
    # 重组成自定义标准格式后调用自定义同步
    await custom_sync(reorganized_json)
