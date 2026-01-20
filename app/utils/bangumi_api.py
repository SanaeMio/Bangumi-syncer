import datetime
import difflib
import os
import socket
import time
import warnings

import requests

from ..core.logging import logger

# 使用全局logger实例


class BangumiApi:
    def __init__(
        self,
        username=None,
        access_token=None,
        private=True,
        http_proxy=None,
        ssl_verify=True,
    ):
        self.host = "https://api.bgm.tv/v0"
        self.username = username
        self.access_token = access_token
        self.private = private
        self.http_proxy = http_proxy
        self.ssl_verify = ssl_verify
        self.req = requests.Session()
        self._req_not_auth = requests.Session()

        # 代理失败标记：一旦代理失败，后续请求都直接使用直连
        self._proxy_failed = False

        # 实例级别的缓存字典，避免使用 lru_cache 导致内存泄漏
        self._cache = {
            "search": {},
            "search_old": {},
            "get_subject": {},
            "get_related_subjects": {},
            "get_episodes": {},
        }

        # 如果禁用SSL验证，抑制urllib3的警告
        if not ssl_verify:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")
            from urllib3.exceptions import InsecureRequestWarning

            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
            logger.warning(
                "SSL证书验证已禁用，这会降低安全性。建议仅在代理环境下出现SSL错误时使用。"
            )

        logger.debug(
            f"BangumiApi 初始化 - 代理参数: {http_proxy if http_proxy else '无'}, SSL验证: {ssl_verify}"
        )
        self.init()

    def init(self):
        for r in self.req, self._req_not_auth:
            r.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)",
                }
            )
            if self.access_token:
                r.headers.update({"Authorization": f"Bearer {self.access_token}"})
            if self.http_proxy:
                r.proxies = {"http": self.http_proxy, "https": self.http_proxy}
        self._req_not_auth.headers = {
            k: v for k, v in self._req_not_auth.headers.items() if k != "Authorization"
        }

    def _try_direct_connection(self, method, url, **kwargs):
        """尝试直连（不使用代理）"""
        logger.info(f"🔄 尝试直连: {url}")

        # 创建一个临时的session，不使用代理
        temp_session = requests.Session()
        temp_session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)",
            }
        )

        if self.access_token:
            temp_session.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )

        # 明确设置不使用代理
        temp_session.proxies = {}

        # 移除kwargs中可能存在的代理设置
        kwargs_copy = kwargs.copy()
        if "proxies" in kwargs_copy:
            del kwargs_copy["proxies"]

        # 设置较短的超时时间，避免直连等待过久
        if "timeout" not in kwargs_copy:
            kwargs_copy["timeout"] = 15

        try:
            if method.upper() == "GET":
                res = temp_session.get(url, **kwargs_copy)
            elif method.upper() == "POST":
                res = temp_session.post(url, **kwargs_copy)
            elif method.upper() == "PUT":
                res = temp_session.put(url, **kwargs_copy)
            elif method.upper() == "PATCH":
                res = temp_session.patch(url, **kwargs_copy)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")

            # 检查响应状态
            if res.status_code < 400:
                return res
            else:
                logger.warning(f"⚠️  直连请求返回错误状态码: {res.status_code}")
                return None

        except Exception as e:
            logger.error(f"直连请求失败: {str(e)}")
            raise e
        finally:
            temp_session.close()

    def _diagnose_network_issue(self, url):
        """诊断网络连接问题"""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        logger.info(f"🔍 开始网络诊断 - 目标: {hostname}:{port}")

        # 1. DNS解析测试
        try:
            ip_list = socket.getaddrinfo(
                hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            ips = [ip[4][0] for ip in ip_list]
            logger.info(f"✅ DNS解析成功: {hostname} -> {', '.join(set(ips))}")
        except socket.gaierror as e:
            logger.error(f"❌ DNS解析失败: {e}")
            logger.info("💡 建议检查:")
            logger.info("   1. 网络连接是否正常")
            logger.info("   2. DNS设置是否正确 (可尝试8.8.8.8或114.114.114.114)")
            logger.info("   3. 是否需要配置代理")
            return
        except Exception as e:
            logger.error(f"❌ DNS解析异常: {e}")
            return

        # 2. TCP连接测试
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((ips[0], port))
            sock.close()

            if result == 0:
                logger.info(f"✅ TCP连接成功: {ips[0]}:{port}")
            else:
                logger.error(f"❌ TCP连接失败: {ips[0]}:{port} (错误码: {result})")
                logger.info("💡 建议检查:")
                logger.info("   1. 防火墙设置")
                logger.info("   2. 网络代理配置")
                logger.info("   3. 是否需要VPN或其他网络工具")
        except Exception as e:
            logger.error(f"❌ TCP连接测试异常: {e}")

    def _request_with_retry(self, method, session, url, max_retries=3, **kwargs):
        """带重试机制的请求方法（支持代理失败后直连重试）"""
        dns_error_occurred = False

        # 如果之前代理已经失败过，直接使用直连
        if self.http_proxy and self._proxy_failed:
            logger.info("💡 检测到代理之前已失败，本次请求直接使用直连")
            try:
                return self._try_direct_connection(method, url, **kwargs)
            except Exception as e:
                logger.error(f"直连请求失败: {str(e)}")
                raise e

        for attempt in range(max_retries + 1):
            try:
                # 添加SSL验证配置
                kwargs["verify"] = self.ssl_verify

                if method.upper() == "GET":
                    res = session.get(url, **kwargs)
                elif method.upper() == "POST":
                    res = session.post(url, **kwargs)
                elif method.upper() == "PUT":
                    res = session.put(url, **kwargs)
                elif method.upper() == "PATCH":
                    res = session.patch(url, **kwargs)
                else:
                    raise ValueError(f"不支持的HTTP方法: {method}")

                # 检查是否需要重试的状态码
                if res.status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        delay = 2**attempt  # 指数退避: 2, 4, 8秒
                        logger.error(
                            f"HTTP {res.status_code} 错误，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"HTTP {res.status_code} 错误，已达到最大重试次数 {max_retries}"
                        )
                        # 发送API错误通知
                        try:
                            from .notifier import get_notifier

                            notifier = get_notifier()
                            notifier.send_notification_by_type(
                                "api_error",
                                {
                                    "timestamp": datetime.now().strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    ),
                                    "status_code": res.status_code,
                                    "url": url,
                                    "method": method,
                                    "error_message": f"HTTP {res.status_code} 错误，已达到最大重试次数 {max_retries}",
                                    "retry_count": attempt + 1,
                                },
                            )
                        except Exception as notify_error:
                            logger.error(f"发送API错误通知失败: {notify_error}")
                        raise requests.exceptions.HTTPError(
                            f"HTTP {res.status_code} 错误，已达到最大重试次数"
                        )

                return res

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.RequestException,
            ) as e:
                # 检查是否是DNS解析错误
                if "Failed to resolve" in str(
                    e
                ) or "Temporary failure in name resolution" in str(e):
                    dns_error_occurred = True

                if attempt < max_retries:
                    delay = 2**attempt  # 指数退避: 2, 4, 8秒
                    logger.error(
                        f"请求异常: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"请求异常: {str(e)}，已达到最大重试次数 {max_retries}"
                    )

                    # 如果配置了代理且重试失败，尝试直连
                    if self.http_proxy:
                        logger.warning("⚠️  代理请求失败，尝试抛弃代理直连...")

                        # 尝试直连（不使用代理）
                        try:
                            direct_result = self._try_direct_connection(
                                method, url, **kwargs
                            )
                            if direct_result:
                                # 标记代理已失败，后续请求直接使用直连
                                self._proxy_failed = True
                                logger.info("✅ 直连成功！已成功绕过代理问题")
                                return direct_result
                        except Exception as direct_error:
                            logger.error(f"❌ 直连也失败了: {str(direct_error)}")

                    # 如果是DNS错误，进行网络诊断
                    if dns_error_occurred:
                        logger.warning("⚠️  检测到DNS解析问题，开始网络诊断...")
                        self._diagnose_network_issue(url)

                    raise e

    def _check_auth_error(self, res):
        """统一检查认证错误"""
        if res.status_code == 401:
            error_msg = "Bangumi API 认证失败: access_token可能已过期（有效期1年）或无效，请更新token"
            logger.error(error_msg)

            # 发送API认证失败通知（webhook和邮件）
            try:
                from .notifier import get_notifier

                notifier = get_notifier()
                notifier.send_notification_by_type(
                    "api_auth_error",
                    {
                        "timestamp": datetime.datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "username": self.username,
                        "status_code": res.status_code,
                        "error_message": error_msg,
                    },
                )
            except Exception as notify_error:
                logger.error(f"发送API认证失败通知失败: {notify_error}")

            raise ValueError(error_msg)
        return res

    def get(self, path, params=None):
        logger.debug(
            f"BangumiApi GET请求: {self.host}/{path}, 代理: {self.req.proxies if self.req.proxies else '无'}"
        )
        res = self._request_with_retry(
            "GET", self.req, f"{self.host}/{path}", params=params
        )
        return self._check_auth_error(res)

    def post(self, path, _json, params=None):
        logger.debug(
            f"BangumiApi POST请求: {self.host}/{path}, 代理: {self.req.proxies if self.req.proxies else '无'}"
        )
        res = self._request_with_retry(
            "POST", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)

    def put(self, path, _json, params=None):
        res = self._request_with_retry(
            "PUT", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)

    def patch(self, path, _json, params=None):
        res = self._request_with_retry(
            "PATCH", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)

    def get_me(self):
        res = self.get("me")
        if 400 <= res.status_code < 500:
            # 发送API认证失败通知
            try:
                from .notifier import get_notifier

                notifier = get_notifier()
                notifier.send_notification_by_type(
                    "api_auth_error",
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "username": self.username,
                        "status_code": res.status_code,
                        "error_message": "BangumiApi: 未授权, access_token不正确或未设置",
                    },
                )
            except Exception as notify_error:
                logger.error(f"发送API认证失败通知失败: {notify_error}")
            if os.name == "nt":
                os.startfile("https://next.bgm.tv/demo/access-token")
            raise ValueError("BangumiApi: 未授权, access_token不正确或未设置")
        return res.json()

    def search(self, title, start_date, end_date, limit=5, list_only=True):
        # 使用实例缓存避免内存泄漏
        cache_key = (title, start_date, end_date, limit, list_only)
        if cache_key in self._cache["search"]:
            return self._cache["search"][cache_key]

        res = self._request_with_retry(
            "POST",
            self._req_not_auth,
            f"{self.host}/search/subjects",
            json={
                "keyword": title,
                "filter": {
                    "type": [2],
                    "air_date": [f">={start_date}", f"<{end_date}"],
                    "nsfw": True,
                },
            },
            params={"limit": limit},
        )
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"search API返回非字典类型: {type(res)}, 内容: {res}")
                res = {"data": []}
        except Exception as e:
            logger.error(f"search JSON解析失败: {e}")
            res = {"data": []}

        result = res.get("data", []) if list_only else res
        self._cache["search"][cache_key] = result
        return result

    def search_old(self, title, list_only=True):
        # 使用实例缓存避免内存泄漏
        cache_key = (title, list_only)
        if cache_key in self._cache["search_old"]:
            return self._cache["search_old"][cache_key]

        res = self._request_with_retry(
            "GET",
            self.req,
            f"{self.host[:-2]}/search/subject/{title}",
            params={"type": 2},
        )
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"search_old API返回非字典类型: {type(res)}, 内容: {res}")
                res = {"results": 0, "list": []}
        except Exception as e:
            logger.error(f"search_old JSON解析失败: {e}")
            res = {"results": 0, "list": []}

        result = res.get("list", []) if list_only else res
        self._cache["search_old"][cache_key] = result
        return result

    def get_subject(self, subject_id):
        # 使用实例缓存避免内存泄漏
        if subject_id in self._cache["get_subject"]:
            return self._cache["get_subject"][subject_id]

        res = self.get(f"subjects/{subject_id}")
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"get_subject API返回非字典类型: {type(res)}, 内容: {res}")
                res = {}
        except Exception as e:
            logger.error(f"get_subject JSON解析失败: {e}")
            res = {}

        self._cache["get_subject"][subject_id] = res
        return res

    def get_related_subjects(self, subject_id):
        # 使用实例缓存避免内存泄漏
        if subject_id in self._cache["get_related_subjects"]:
            return self._cache["get_related_subjects"][subject_id]

        res = self.get(f"subjects/{subject_id}/subjects")
        try:
            res = res.json()
            # get_related_subjects 可能返回列表或字典，都是正常的
            if not isinstance(res, (dict, list)):
                logger.error(
                    f"get_related_subjects API返回异常类型: {type(res)}, 内容: {res}"
                )
                res = []
        except Exception as e:
            logger.error(f"get_related_subjects JSON解析失败: {e}")
            res = []

        self._cache["get_related_subjects"][subject_id] = res
        return res

    def get_episodes(self, subject_id, _type=0):
        # 使用实例缓存避免内存泄漏
        cache_key = (subject_id, _type)
        if cache_key in self._cache["get_episodes"]:
            return self._cache["get_episodes"][cache_key]

        res = self.get(
            "episodes",
            params={
                "subject_id": subject_id,
                "type": _type,
            },
        )
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_episodes API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {"data": [], "total": 0}
        except Exception as e:
            logger.error(f"get_episodes JSON解析失败: {e}")
            res = {"data": [], "total": 0}

        self._cache["get_episodes"][cache_key] = res
        return res

    def get_target_season_episode_id(
        self,
        subject_id,
        target_season: int,
        target_ep: int,
        is_season_subject_id: bool = False,
    ):
        season_num = 1
        current_id = subject_id

        if target_season > 5 or (target_ep and target_ep > 99):
            return None, None if target_ep else None

        # 如果已经是目标季数的ID，直接尝试匹配集数
        if is_season_subject_id:
            logger.debug(
                f"直接尝试从指定季度ID匹配集数: {subject_id}, 目标季度: {target_season}, 目标集数: {target_ep}"
            )
            if not target_ep:
                return current_id

            episodes = self.get_episodes(current_id)
            ep_info = episodes.get("data", [])
            logger.debug(ep_info)

            if not ep_info:
                logger.debug(f"未获取到剧集信息: {subject_id}")
                return None, None if target_ep else None

            # 先尝试完全匹配sort字段
            _target_ep = [i for i in ep_info if i.get("sort") == target_ep]

            # 如果完全匹配失败，尝试匹配ep字段
            if not _target_ep:
                _target_ep = [
                    i
                    for i in ep_info
                    if i.get("ep") == target_ep and i.get("ep", 0) <= i.get("sort", 0)
                ]

            if _target_ep:
                return current_id, _target_ep[0]["id"]
            else:
                logger.debug(
                    f"在指定季度ID中未找到匹配的集数: {subject_id}, 目标集数: {target_ep}"
                )
                # 失败后回退到传统方法
                logger.debug("回退到传统方式查找集数")

        if target_season == 1:
            if not target_ep:
                return current_id
            fist_part = True
            while True:
                if not fist_part:
                    current_info = self.get_subject(current_id)
                    if not current_info or current_info.get("platform") != "TV":
                        continue
                episodes = self.get_episodes(current_id)
                ep_info = episodes.get("data", [])
                if not ep_info:
                    logger.debug(f"未获取到剧集信息: {current_id}")
                    # 修复死循环：如果获取不到剧集信息，应该跳出循环而不是继续
                    break
                _target_ep = [i for i in ep_info if i.get("sort") == target_ep]
                if _target_ep:
                    return current_id, _target_ep[0]["id"]
                normal_season = (
                    True
                    if episodes.get("total", 0) > 3 and ep_info[0].get("sort", 0) <= 1
                    else False
                )
                if not fist_part and normal_season:
                    break
                related = self.get_related_subjects(current_id)
                # 处理related可能是列表或字典的情况
                if isinstance(related, list):
                    next_id = [i for i in related if i.get("relation") == "续集"]
                elif isinstance(related, dict):
                    # 如果是字典，可能包含data字段
                    related_list = related.get("data", [])
                    next_id = [i for i in related_list if i.get("relation") == "续集"]
                else:
                    next_id = []
                if not next_id:
                    break
                current_id = next_id[0]["id"]
                fist_part = False
            return None, None if target_ep else None

        while True:
            related = self.get_related_subjects(current_id)
            # 处理related可能是列表或字典的情况
            if isinstance(related, list):
                next_id = [i for i in related if i.get("relation") == "续集"]
            elif isinstance(related, dict):
                # 如果是字典，可能包含data字段
                related_list = related.get("data", [])
                next_id = [i for i in related_list if i.get("relation") == "续集"]
            else:
                next_id = []
            if not next_id:
                break
            current_id = next_id[0]["id"]
            current_info = self.get_subject(current_id)
            if not current_info or current_info.get("platform") != "TV":
                continue
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get("data", [])
            if not ep_info:
                logger.debug(f"未获取到剧集信息: {current_id}")
                # 修复死循环：如果获取不到剧集信息，应该跳出循环而不是继续
                break
            logger.debug(ep_info)
            normal_season = (
                True
                if episodes.get("total", 0) > 3 and ep_info[0].get("sort", 0) <= 1
                else False
            )
            _target_ep = [i for i in ep_info if i.get("sort") == target_ep]
            logger.debug(_target_ep)
            # 兼容存在多季情况下，第一集的sort不为1的场景
            if not _target_ep:
                _target_ep = [
                    i
                    for i in ep_info
                    if i.get("ep") == target_ep and i.get("ep", 0) <= i.get("sort", 0)
                ]
                if (
                    target_ep
                    and _target_ep
                    and "第2部分" not in current_info.get("name_cn", "")
                ):
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
                return current_id, _target_ep[0]["id"]
        return None, None if target_ep else None

    def get_subject_collection(self, subject_id):
        res = self.get(f"users/{self.username}/collections/{subject_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_subject_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_subject_collection JSON解析失败: {e}")
            res = {}
        return res

    def get_ep_collection(self, episode_id):
        res = self.get(f"users/-/collections/-/episodes/{episode_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_ep_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_ep_collection JSON解析失败: {e}")
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
            if data.get("type") == 2:
                return 0
            #  如果条目状态是想看或搁置则调整为在看
            if data.get("type") == 1 or data.get("type") == 4:
                self.change_collection_state(subject_id=subject_id, state=3)

        ep_data = self.get_ep_collection(ep_id)
        logger.debug(ep_data)
        # 如果单集已看过则跳过
        if ep_data.get("type") == 2:
            return 0
        else:
            # 否则直接点单集格子
            self.change_episode_state(ep_id=ep_id, state=2)
            return 1

    def add_collection_subject(self, subject_id, private=None, state=3):
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_collection_state(self, subject_id, private=None, state=3):
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_episode_state(self, ep_id, state=2):
        res = self.put(f"users/-/collections/-/episodes/{ep_id}", _json={"type": state})
        if 333 < res.status_code < 444:
            raise ValueError(f"{res.status_code=} {res.text}")
        return res

    def bgm_search(self, title, ori_title, premiere_date: str, is_movie=False):
        air_date = datetime.datetime.fromisoformat(premiere_date[:10])
        start_date = air_date - datetime.timedelta(days=2)
        end_date = air_date + datetime.timedelta(days=2)
        bgm_data = None
        if ori_title:
            bgm_data = self.search(
                title=ori_title, start_date=start_date, end_date=end_date
            )
        bgm_data = bgm_data or self.search(
            title=title, start_date=start_date, end_date=end_date
        )
        if not bgm_data and is_movie:
            title = ori_title or title
            end_date = air_date + datetime.timedelta(days=200)
            bgm_data = self.search(
                title=title, start_date=start_date, end_date=end_date
            )
        if not bgm_data or (
            bgm_data
            and len(bgm_data) > 0
            and self.title_diff_ratio(
                title=title, ori_title=ori_title, bgm_data=bgm_data[0]
            )
            < 0.5
        ):
            for t in ori_title, title:
                bgm_data = self.search_old(title=t)
                if (
                    bgm_data
                    and len(bgm_data) > 0
                    and self.title_diff_ratio(title, ori_title, bgm_data=bgm_data[0])
                    > 0.5
                ):
                    break
            else:
                bgm_data = None
        if not bgm_data or len(bgm_data) == 0:
            return
        logger.debug(f"{start_date} {end_date} {bgm_data}")
        return bgm_data

    @staticmethod
    def title_diff_ratio(title, ori_title, bgm_data):
        ori_title = ori_title or title
        ratio = max(
            difflib.SequenceMatcher(None, bgm_data["name"], ori_title).quick_ratio(),
            difflib.SequenceMatcher(None, bgm_data["name_cn"], title).quick_ratio(),
            difflib.SequenceMatcher(None, bgm_data["name"], title).quick_ratio(),
        )
        return ratio
