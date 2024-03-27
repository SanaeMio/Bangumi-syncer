from fastapi import FastAPI
from pydantic import BaseModel

from utils.configs import configs, MyLogger
from utils.bangumi_api import BangumiApi

logger = MyLogger()

app = FastAPI()


class CustomItem(BaseModel):
    media_type: str
    title: str
    ori_title: str
    season: int
    episode: int
    release_date: str
    user_name: str


# 自定义同步
@app.post("/Custom")
def custom_sync(item: CustomItem):
    logger.info(f'接收到同步请求：{item}')

    # 检查记录类型是否为单集
    if item.media_type != 'episode':
        logger.error(f'标记记录类型{item.media_type}错误，跳过')
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
                logger.info(f'非配置同步用户，跳过')
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

    logger.debug(f'bgm: 查询到 {bgm_data["name"]} S0{item.season}E{item.episode} '
                 f'https://bgm.tv/subject/{bgm_se_id} https://bgm.tv/ep/{bgm_ep_id}')

    bgm.mark_episode_watched(subject_id=bgm_se_id, ep_id=bgm_ep_id)
    logger.info(f'bgm: 已同步 {item.title} S0{item.season}E{item.episode} https://bgm.tv/ep/{bgm_ep_id}')

    return item

