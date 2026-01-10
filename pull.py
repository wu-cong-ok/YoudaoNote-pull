#!/usr/bin/env python3
# -*- coding: utf-8 -*-
 
import json
import logging
import os
import re
import sys
import time
import traceback
from enum import Enum
from typing import Tuple

from core import log
from core.api import YoudaoNoteApi
from core.common import get_script_directory
from core.covert import YoudaoNoteConvert
from core.image import ImagePull
 
MARKDOWN_SUFFIX = ".md"

class FileType(Enum):
    OTHER = 0
    MARKDOWN = 1
    XML = 2
    JSON = 3

class YoudaoNotePull(object):
    def __init__(self):
        self.root_local_dir = None
        self.youdaonote_api = None
        self.smms_secret_token = None
        self.is_relative_path = None

    def _covert_config(self) -> Tuple[dict, str]:
        config_path = os.path.join(get_script_directory(), "config.json")
        if not os.path.exists(config_path):
            return {}, f"找不到配置文件: {config_path}"
        try:
            with open(config_path, "rb") as f:
                config_dict = json.loads(f.read().decode("utf-8"))
            return config_dict, ""
        except Exception as e:
            return {}, f"解析 config.json 出错: {str(e)}"

    def _check_local_dir(self, local_dir) -> Tuple[str, str]:
        if not local_dir:
            local_dir = os.path.join(get_script_directory(), "youdaonote").replace("\\", "/")
        try:
            if not os.path.exists(local_dir):
                os.makedirs(local_dir, exist_ok=True)
            return os.path.abspath(local_dir).replace("\\", "/"), ""
        except Exception as e:
            return "", f"无法创建本地目录: {str(e)}"

    def get_ydnote_dir_id(self) -> Tuple[str, str]:
        config_dict, error_msg = self._covert_config()
        if error_msg: return "", error_msg
        local_dir, error_msg = self._check_local_dir(config_dict.get("local_dir"))
        if error_msg: return "", error_msg
        self.root_local_dir = local_dir
        self.youdaonote_api = YoudaoNoteApi()
        error_msg = self.youdaonote_api.login_by_cookies()
        if error_msg: return "", error_msg
        self.smms_secret_token = config_dict.get("smms_secret_token")
        self.is_relative_path = config_dict.get("is_relative_path")
        
        root_info = self.youdaonote_api.get_root_dir_info_id()
        root_id = root_info["fileEntry"]["id"]
        target_name = config_dict.get("ydnote_dir")
        if not target_name: return root_id, ""
        
        dir_info = self.youdaonote_api.get_dir_info_by_id(root_id)
        for entry in dir_info.get("entries", []):
            if entry["fileEntry"]["name"] == target_name:
                return entry["fileEntry"]["id"], ""
        return "", f"云端未找到目录: {target_name}"

    def _optimize_file_name(self, name) -> str:
        name = name.replace("\n", "").strip()
        name = re.sub(r'[\\/":\|\*\?#<>]', "_", name)
        return name.strip()

    def pull_dir_by_id_recursively(self, dir_id, local_dir):
        if not os.path.exists(local_dir):
            os.makedirs(local_dir, exist_ok=True)
        dir_info = self.youdaonote_api.get_dir_info_by_id(dir_id)
        for entry in dir_info.get("entries", []):
            file_entry = entry["fileEntry"]
            id, name = file_entry["id"], file_entry["name"]
            if file_entry["dir"]:
                sub_dir = os.path.join(local_dir, self._optimize_file_name(name)).replace("\\", "/")
                self.pull_dir_by_id_recursively(id, sub_dir)
            else:
                self._add_or_update_file(id, name, local_dir, file_entry["modifyTimeForSort"], file_entry["createTimeForSort"])

    def _add_or_update_file(self, file_id, file_name, local_dir, modify_time, create_time):
        clean_name = self._optimize_file_name(file_name)
        suffix = os.path.splitext(clean_name)[1].lower()
        
        # 1. 预判类型
        if suffix == MARKDOWN_SUFFIX:
            file_type = FileType.MARKDOWN
        elif suffix in [".note", ".clip", ""]:
            # 对于 note 文件，先尝试获取内容判断
            res = self.youdaonote_api.get_file_by_id(file_id)
            if res.content[:5] == b"<?xml": file_type = FileType.XML
            elif res.content.startswith(b'{"'): file_type = FileType.JSON
            else: file_type = FileType.OTHER
        else:
            file_type = FileType.OTHER

        # 2. 确定最终的 Markdown 路径
        base_name = os.path.splitext(clean_name)[0].strip()
        final_md_path = os.path.join(local_dir, base_name + MARKDOWN_SUFFIX).replace("\\", "/")
        # 如果是附件（OTHER），则保持原名
        actual_save_path = final_md_path if file_type != FileType.OTHER else os.path.join(local_dir, clean_name).replace("\\", "/")

        # 3. 检查更新
        if os.path.exists(actual_save_path) and modify_time <= os.path.getmtime(actual_save_path):
            return

        try:
            os.makedirs(os.path.dirname(actual_save_path), exist_ok=True)
            
            # 下载原始文件
            response = self.youdaonote_api.get_file_by_id(file_id)
            temp_save_path = os.path.join(local_dir, clean_name).replace("\\", "/")
            with open(temp_save_path, "wb") as f:
                f.write(response.content)

            # 4. 转换逻辑 (转换后 temp_save_path 可能会消失，生成 final_md_path)
            if file_type == FileType.XML:
                try: YoudaoNoteConvert.covert_xml_to_markdown(temp_save_path)
                except: YoudaoNoteConvert.covert_html_to_markdown(temp_save_path)
            elif file_type == FileType.JSON:
                YoudaoNoteConvert.covert_json_to_markdown(temp_save_path)

            # 5. 核心修复：确保后续操作在“确实存在”的文件上进行
            target_path = actual_save_path if os.path.exists(actual_save_path) else temp_save_path
            
            if file_type != FileType.OTHER or suffix == MARKDOWN_SUFFIX:
                if os.path.exists(target_path):
                    ImagePull(self.youdaonote_api, self.smms_secret_token, self.is_relative_path).migration_ydnote_url(target_path)

            if os.path.exists(target_path):
                os.utime(target_path, (create_time, modify_time))
                logging.info(f"同步成功: {target_path}")
        except Exception as e:
            logging.error(f"同步失败 {file_name}: {str(e)}")

if __name__ == "__main__":
    log.init_logging()
    print("🚀 脚本已启动...")
    try:
        puller = YoudaoNotePull()
        dir_id, err = puller.get_ydnote_dir_id()
        if err:
            print(f"❌ 错误: {err}")
            sys.exit(1)
        print("✅ 登录成功，开始同步笔记...")
        puller.pull_dir_by_id_recursively(dir_id, puller.root_local_dir)
        print("✨ 全部同步完成！")
    except Exception as e:
        print(f"💥 运行异常: {str(e)}")
        traceback.print_exc()