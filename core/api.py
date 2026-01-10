import json
import os
import time
import requests
from core.common import get_script_directory

class YoudaoNoteApi(object):
    """
    有道云笔记 API 封装 (资源下载加固版)
    """

    ROOT_ID_URL = "https://note.youdao.com/yws/api/personal/file?method=getByPath&keyfrom=web&cstk={cstk}"
    DIR_MES_URL = "https://note.youdao.com/yws/api/personal/file/{dir_id}?all=true&f=true&len=1000&sort=1&method=listPageByParentId&keyfrom=web&cstk={cstk}"
    # 注入关键指纹参数
    FILE_URL = "https://note.youdao.com/yws/api/personal/sync?method=download&_system=macos&_appName=ynote&_appuser=ffae2effe37ad4a4e8df3e61a237d78f&_deviceId=5ec8ab8f6f2f1fb6&sev=j1&keyfrom=web&cstk={cstk}"

    def __init__(self, cookies_path=None):
        self.session = requests.session()
        self.session.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://note.youdao.com/web/",
            "Connection": "keep-alive"
        }
        self.cookies_path = cookies_path if cookies_path else os.path.join(get_script_directory(), "cookies.json")
        self.cstk = None

    def login_by_cookies(self) -> str:
        try:
            if not os.path.exists(self.cookies_path):
                return f"找不到 cookies.json 文件: {self.cookies_path}"
            with open(self.cookies_path, "rb") as f:
                cookies_dict = json.loads(f.read().decode("utf-8"))
            for cookie in cookies_dict["cookies"]:
                self.session.cookies.set(name=cookie[0], value=cookie[1], domain=cookie[2], path=cookie[3])
                if cookie[0] == "YNOTE_CSTK": self.cstk = cookie[1]
            
            if not self.cstk:
                return "cookies.json 中缺少 YNOTE_CSTK"
            
            # 简单验证一下
            res = self.get_root_dir_info_id()
            if "fileEntry" not in res:
                return f"登录验证失败，请检查 cookies 有效性: {res}"
            return ""
        except Exception as e:
            return str(e)

    def http_post(self, url, data=None):
        return self.session.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})

    def http_get(self, url):
        # 核心修复：为资源下载自动补全 cstk
        if 'cstk=' not in url:
            connector = '&' if '?' in url else '?'
            url = f"{url}{connector}cstk={self.cstk}"
        return self.session.get(url)

    def get_root_dir_info_id(self) -> dict:
        data = {"path": "/", "entire": "true", "purge": "false", "cstk": self.cstk}
        return self.http_post(self.ROOT_ID_URL.format(cstk=self.cstk), data=data).json()

    def get_dir_info_by_id(self, dir_id) -> dict:
        return self.http_get(self.DIR_MES_URL.format(dir_id=dir_id, cstk=self.cstk)).json()

    def get_file_by_id(self, file_id):
        data = {"fileId": file_id, "version": -1, "convert": "true", "editorType": 1, "cstk": self.cstk}
        return self.http_post(self.FILE_URL.format(cstk=self.cstk), data=data)

    def get_resource_by_url(self, url):
        return self.http_get(url)