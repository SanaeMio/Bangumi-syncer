import datetime
import difflib
import functools
import os
import time
import requests
import warnings
from ..core.logging import logger

# 使用全局logger实例


class BangumiApi:
    def __init__(self, username=None, access_token=None, private=True, http_proxy=None, ssl_verify=True):
        self.host = 'https://api.bgm.tv/v0'
        self.username = username
        self.access_token = access_token
        self.private = private
        self.http_proxy = http_proxy
        self.ssl_verify = ssl_verify
        self.req = requests.Session()
        self._req_not_auth = requests.Session()
        
        # 如果禁用SSL验证，抑制urllib3的警告
        if not ssl_verify:
            warnings.filterwarnings('ignore', message='Unverified HTTPS request')
            from urllib3.exceptions import InsecureRequestWarning
            warnings.filterwarnings('ignore', category=InsecureRequestWarning)
            logger.warning('SSL证书验证已禁用，这会降低安全性。建议仅在代理环境下出现SSL错误时使用。')
        
        logger.debug(f'BangumiApi 初始化 - 代理参数: {http_proxy if http_proxy else "无"}, SSL验证: {ssl_verify}')
        self.init()

    def init(self):
        for r in self.req, self._req_not_auth:
            r.headers.update({'Accept': 'application/json',
                              'User-Agent': 'SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)'})
            if self.access_token:
                r.headers.update({'Authorization': f'Bearer {self.access_token}'})
            if self.http_proxy:
                r.proxies = {'http': self.http_proxy, 'https': self.http_proxy}
        self._req_not_auth.headers = {k: v for k, v in self._req_not_auth.headers.items() if k != 'Authorization'}

    def _request_with_retry(self, method, session, url, max_retries=3, **kwargs):
        """带重试机制的请求方法"""
        for attempt in range(max_retries + 1):
            try:
                # 添加SSL验证配置
                kwargs['verify'] = self.ssl_verify
                
                if method.upper() == 'GET':
                    res = session.get(url, **kwargs)
                elif method.upper() == 'POST':
                    res = session.post(url, **kwargs)
                elif method.upper() == 'PUT':
                    res = session.put(url, **kwargs)
                elif method.upper() == 'PATCH':
                    res = session.patch(url, **kwargs)
                else:
                    raise ValueError(f"不支持的HTTP方法: {method}")
                
                # 检查是否需要重试的状态码
                if res.status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        delay = 2 ** attempt  # 指数退避: 2, 4, 8秒
                        logger.error(f'HTTP {res.status_code} 错误，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试')
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f'HTTP {res.status_code} 错误，已达到最大重试次数 {max_retries}')
                        raise requests.exceptions.HTTPError(f"HTTP {res.status_code} 错误，已达到最大重试次数")
                
                return res
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException) as e:
                if attempt < max_retries:
                    delay = 2 ** attempt  # 指数退避: 2, 4, 8秒
                    logger.error(f'请求异常: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试')
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f'请求异常: {str(e)}，已达到最大重试次数 {max_retries}')
                    raise e
        
        return res

    def get(self, path, params=None):
        logger.debug(f'BangumiApi GET请求: {self.host}/{path}, 代理: {self.req.proxies if self.req.proxies else "无"}')
        return self._request_with_retry('GET', self.req, f'{self.host}/{path}', params=params)

    def post(self, path, _json, params=None):
        logger.debug(f'BangumiApi POST请求: {self.host}/{path}, 代理: {self.req.proxies if self.req.proxies else "无"}')
        return self._request_with_retry('POST', self.req, f'{self.host}/{path}', json=_json, params=params)

    def put(self, path, _json, params=None):
        return self._request_with_retry('PUT', self.req, f'{self.host}/{path}', json=_json, params=params)

    def patch(self, path, _json, params=None):
        return self._request_with_retry('PATCH', self.req, f'{self.host}/{path}', json=_json, params=params)

    def get_me(self):
        res = self.get('me')
        if 400 <= res.status_code < 500:
            if os.name == 'nt':
                os.startfile('https://next.bgm.tv/demo/access-token')
            raise ValueError('BangumiApi: 未授权, access_token不正确或未设置')
        return res.json()

    @functools.lru_cache
    def search(self, title, start_date, end_date, limit=5, list_only=True):
        res = self._request_with_retry('POST', self._req_not_auth, f'{self.host}/search/subjects',
                                      json={'keyword': title,
                                            'filter': {'type': [2],
                                                       'air_date': [f'>={start_date}',
                                                                    f'<{end_date}'],
                                                       'nsfw': True}},
                                      params={'limit': limit})
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'search API返回非字典类型: {type(res)}, 内容: {res}')
                res = {'data': []}
        except Exception as e:
            logger.error(f'search JSON解析失败: {e}')
            res = {'data': []}
        return res.get('data', []) if list_only else res

    @functools.lru_cache
    def search_old(self, title, list_only=True):
        res = self._request_with_retry('GET', self.req, f'{self.host[:-2]}/search/subject/{title}', params={'type': 2})
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'search_old API返回非字典类型: {type(res)}, 内容: {res}')
                res = {'results': 0, 'list': []}
        except Exception as e:
            logger.error(f'search_old JSON解析失败: {e}')
            res = {'results': 0, 'list': []}
        return res.get('list', []) if list_only else res

    @functools.lru_cache
    def get_subject(self, subject_id):
        res = self.get(f'subjects/{subject_id}')
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'get_subject API返回非字典类型: {type(res)}, 内容: {res}')
                res = {}
        except Exception as e:
            logger.error(f'get_subject JSON解析失败: {e}')
            res = {}
        return res

    @functools.lru_cache
    def get_related_subjects(self, subject_id):
        res = self.get(f'subjects/{subject_id}/subjects')
        try:
            res = res.json()
            # get_related_subjects 可能返回列表或字典，都是正常的
            if not isinstance(res, (dict, list)):
                logger.error(f'get_related_subjects API返回异常类型: {type(res)}, 内容: {res}')
                res = []
        except Exception as e:
            logger.error(f'get_related_subjects JSON解析失败: {e}')
            res = []
        return res

    @functools.lru_cache
    def get_episodes(self, subject_id, _type=0):
        res = self.get('episodes', params={
            'subject_id': subject_id,
            'type': _type,
        })
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'get_episodes API返回非字典类型: {type(res)}, 内容: {res}')
                res = {'data': [], 'total': 0}
        except Exception as e:
            logger.error(f'get_episodes JSON解析失败: {e}')
            res = {'data': [], 'total': 0}
        return res

    def get_target_season_episode_id(self, subject_id, target_season: int, target_ep: int, is_season_subject_id: bool = False):
        season_num = 1
        current_id = subject_id

        if target_season > 5 or (target_ep and target_ep > 99):
            return None, None if target_ep else None
            
        # 如果已经是目标季数的ID，直接尝试匹配集数
        if is_season_subject_id:
            logger.debug(f"直接尝试从指定季度ID匹配集数: {subject_id}, 目标季度: {target_season}, 目标集数: {target_ep}")
            if not target_ep:
                return current_id
            
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get('data', [])
            logger.debug(ep_info)
            
            if not ep_info:
                logger.debug(f"未获取到剧集信息: {subject_id}")
                return None, None if target_ep else None
            
            # 先尝试完全匹配sort字段
            _target_ep = [i for i in ep_info if i.get('sort') == target_ep]
            
            # 如果完全匹配失败，尝试匹配ep字段
            if not _target_ep:
                _target_ep = [i for i in ep_info if i.get('ep') == target_ep and i.get('ep', 0) <= i.get('sort', 0)]
                
            if _target_ep:
                return current_id, _target_ep[0]['id']
            else:
                logger.debug(f"在指定季度ID中未找到匹配的集数: {subject_id}, 目标集数: {target_ep}")
                # 失败后回退到传统方法
                logger.debug("回退到传统方式查找集数")

        if target_season == 1:
            if not target_ep:
                return current_id
            fist_part = True
            while True:
                if not fist_part:
                    current_info = self.get_subject(current_id)
                    if not current_info or current_info.get('platform') != 'TV':
                        continue
                episodes = self.get_episodes(current_id)
                ep_info = episodes.get('data', [])
                if not ep_info:
                    logger.debug(f"未获取到剧集信息: {current_id}")
                    # 修复死循环：如果获取不到剧集信息，应该跳出循环而不是继续
                    break
                _target_ep = [i for i in ep_info if i.get('sort') == target_ep]
                if _target_ep:
                    return current_id, _target_ep[0]['id']
                normal_season = True if episodes.get('total', 0) > 3 and ep_info[0].get('sort', 0) <= 1 else False
                if not fist_part and normal_season:
                    break
                related = self.get_related_subjects(current_id)
                # 处理related可能是列表或字典的情况
                if isinstance(related, list):
                    next_id = [i for i in related if i.get('relation') == '续集']
                elif isinstance(related, dict):
                    # 如果是字典，可能包含data字段
                    related_list = related.get('data', [])
                    next_id = [i for i in related_list if i.get('relation') == '续集']
                else:
                    next_id = []
                if not next_id:
                    break
                current_id = next_id[0]['id']
                fist_part = False
            return None, None if target_ep else None

        while True:
            related = self.get_related_subjects(current_id)
            # 处理related可能是列表或字典的情况
            if isinstance(related, list):
                next_id = [i for i in related if i.get('relation') == '续集']
            elif isinstance(related, dict):
                # 如果是字典，可能包含data字段
                related_list = related.get('data', [])
                next_id = [i for i in related_list if i.get('relation') == '续集']
            else:
                next_id = []
            if not next_id:
                break
            current_id = next_id[0]['id']
            current_info = self.get_subject(current_id)
            if not current_info or current_info.get('platform') != 'TV':
                continue
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get('data', [])
            if not ep_info:
                logger.debug(f"未获取到剧集信息: {current_id}")
                # 修复死循环：如果获取不到剧集信息，应该跳出循环而不是继续
                break
            logger.debug(ep_info)
            normal_season = True if episodes.get('total', 0) > 3 and ep_info[0].get('sort', 0) <= 1 else False
            _target_ep = [i for i in ep_info if i.get('sort') == target_ep]
            logger.debug(_target_ep)
            # 兼容存在多季情况下，第一集的sort不为1的场景
            if not _target_ep:
                _target_ep = [i for i in ep_info if i.get('ep') == target_ep and i.get('ep', 0) <= i.get('sort', 0)]
                if (target_ep and _target_ep
                        and '第2部分' not in current_info.get('name_cn', '')):
                    season_num += 1
                logger.debug(_target_ep)
            ep_found = True if target_ep and _target_ep else False
            if normal_season:
                season_num += 1
            if season_num > target_season:
                break
            if season_num == target_season:
                if not target_ep:
                    return current_id
                if not ep_found:
                    continue
                return current_id, _target_ep[0]['id']
        return None, None if target_ep else None

    def get_subject_collection(self, subject_id):
        res = self.get(f'users/{self.username}/collections/{subject_id}')
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'get_subject_collection API返回非字典类型: {type(res)}, 内容: {res}')
                res = {}
        except Exception as e:
            logger.error(f'get_subject_collection JSON解析失败: {e}')
            res = {}
        return res

    def get_ep_collection(self, episode_id):
        res = self.get(f'users/-/collections/-/episodes/{episode_id}')
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f'get_ep_collection API返回非字典类型: {type(res)}, 内容: {res}')
                res = {}
        except Exception as e:
            logger.error(f'get_ep_collection JSON解析失败: {e}')
            res = {}
        return res

    def mark_episode_watched(self, subject_id, ep_id):
        data = self.get_subject_collection(subject_id)

        # 如果未收藏，则先标记为在看，再点单集格子
        if not data:
            self.add_collection_subject(subject_id=subject_id)
            self.change_episode_state(ep_id=ep_id, state=2)
            return 2
        else:
            # 如果整部番已看过则跳过
            if data.get('type') == 2:
                return 0
            #  如果条目状态是想看或搁置则调整为在看
            if data.get('type') == 1 or data.get('type') == 4:
                self.change_collection_state(subject_id=subject_id, state=3)

        ep_data = self.get_ep_collection(ep_id)
        logger.debug(ep_data)
        # 如果单集已看过则跳过
        if ep_data.get('type') == 2:
            return 0
        else:
            # 否则直接点单集格子
            self.change_episode_state(ep_id=ep_id, state=2)
            return 1

    def add_collection_subject(self, subject_id, private=None, state=3):
        private = self.private if private is None else private
        self.post(f'users/-/collections/{subject_id}',
                  _json={'type': state,
                         'private': bool(private)})

    def change_collection_state(self, subject_id, private=None, state=3):
        private = self.private if private is None else private
        self.post(f'users/-/collections/{subject_id}',
                  _json={'type': state,
                         'private': bool(private)})

    def change_episode_state(self, ep_id, state=2):
        res = self.put(f'users/-/collections/-/episodes/{ep_id}',
                       _json={'type': state})
        if 333 < res.status_code < 444:
            raise ValueError(f'{res.status_code=} {res.text}')
        return res

    def bgm_search(self, title, ori_title, premiere_date: str, is_movie=False):
        air_date = datetime.datetime.fromisoformat(premiere_date[:10])
        start_date = air_date - datetime.timedelta(days=2)
        end_date = air_date + datetime.timedelta(days=2)
        bgm_data = None
        if ori_title:
            bgm_data = self.search(title=ori_title, start_date=start_date, end_date=end_date)
        bgm_data = bgm_data or self.search(title=title, start_date=start_date, end_date=end_date)
        if not bgm_data and is_movie:
            title = ori_title or title
            end_date = air_date + datetime.timedelta(days=200)
            bgm_data = self.search(title=title, start_date=start_date, end_date=end_date)
        if not bgm_data or (bgm_data and len(bgm_data) > 0 and self.title_diff_ratio(
                title=title, ori_title=ori_title, bgm_data=bgm_data[0]) < 0.5):
            for t in ori_title, title:
                bgm_data = self.search_old(title=t)
                if bgm_data and len(bgm_data) > 0 and self.title_diff_ratio(title, ori_title, bgm_data=bgm_data[0]) > 0.5:
                    break
            else:
                bgm_data = None
        if not bgm_data or len(bgm_data) == 0:
            return
        logger.debug(f'{start_date} {end_date} {bgm_data}')
        return bgm_data

    @staticmethod
    def title_diff_ratio(title, ori_title, bgm_data):
        ori_title = ori_title or title
        ratio = max(difflib.SequenceMatcher(None, bgm_data['name'], ori_title).quick_ratio(),
                    difflib.SequenceMatcher(None, bgm_data['name_cn'], title).quick_ratio(),
                    difflib.SequenceMatcher(None, bgm_data['name'], title).quick_ratio())
        return ratio
