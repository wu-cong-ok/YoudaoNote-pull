import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Tuple

MARKDOWN_SUFFIX = ".md"

import re

_ordered_counters = {}

def _reset_ordered_counters():
    _ordered_counters.clear()

def _normalize_blank_lines(text):
    return re.sub(r'\n{3,}', '\n\n', text)


class XmlElementConvert(object):
    """
    XML Element 转换规则
    """

    @staticmethod
    def _parse_inline_styles(element):
        raw_text = ""
        styles = []
        for child in list(element):
            tag = child.tag.split("}")[-1]
            if tag == "text":
                raw_text = child.text or ""
            elif tag == "inline-styles":
                for style_elem in list(child):
                    style_tag = style_elem.tag.split("}")[-1]
                    from_val = to_val = value = None
                    for sub in list(style_elem):
                        sub_tag = sub.tag.split("}")[-1]
                        if sub_tag == "from":
                            from_val = int(sub.text) if sub.text else 0
                        elif sub_tag == "to":
                            to_val = int(sub.text) if sub.text else 0
                        elif sub_tag == "value":
                            value = sub.text
                    if from_val is not None and to_val is not None and value == "true":
                        styles.append((from_val, to_val, style_tag))
        if not raw_text:
            return ""
        # Build a list of (pos, insert_text) operations, insert in reverse order
        inserts = []
        for from_pos, to_pos, style_tag in styles:
            if style_tag in ("bold", "b"):
                inserts.append((to_pos, "**"))
                inserts.append((from_pos, "**"))
            elif style_tag in ("italic", "i", "em"):
                inserts.append((to_pos, "*"))
                inserts.append((from_pos, "*"))
            elif style_tag in ("strike", "s", "del", "line-through"):
                inserts.append((to_pos, "~~"))
                inserts.append((from_pos, "~~"))
            elif style_tag in ("a", "link", "href"):
                url = ""
                for style_elem in list(element):
                    for se in list(style_elem):
                        for sub in list(se):
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag in ("href", "url", "link"):
                                url = sub.text or ""
                if url:
                    inserts.append((to_pos, "]({})".format(url)))
                    inserts.append((from_pos, "[{}".format(raw_text[from_pos:to_pos])))
        # Sort by position descending to avoid offset issues
        inserts.sort(key=lambda x: -x[0])
        chars = list(raw_text)
        for pos, ins in inserts:
            if 0 <= pos <= len(chars):
                chars.insert(pos, ins)
        return "".join(chars)

    @staticmethod
    def _parse_rich_text(element):
        tag = element.tag.split("}")[-1]
        children = list(element)

        if "text" in tag and not children:
            return element.text or ""

        if not children:
            return element.text or ""

        # Use inline-styles approach for para-like elements
        if tag in ("para", "p", "div", "span"):
            return XmlElementConvert._parse_inline_styles(element)

        parts = []
        for child in children:
            child_tag = child.tag.split("}")[-1]
            child_text = XmlElementConvert._parse_rich_text(child)
            if not child_text:
                continue
            if child_tag in ("strong", "b"):
                parts.append("**{}**".format(child_text))
            elif child_tag in ("em", "i"):
                parts.append("*{}*".format(child_text))
            elif child_tag in ("strike", "s", "del", "line-through"):
                parts.append("~~{}~~".format(child_text))
            elif child_tag in ("a", "link"):
                url = ""
                for sub in list(child):
                    sub_tag = sub.tag.split("}")[-1]
                    if sub_tag in ("href", "url", "link"):
                        url = sub.text or ""
                if url:
                    parts.append("[{text}]({url})".format(text=child_text, url=url))
                else:
                    parts.append(child_text)
            else:
                parts.append(child_text)
        return "".join(parts)

    @staticmethod
    def convert_para_func(**kwargs):
        element = kwargs.get("element")
        return XmlElementConvert._parse_rich_text(element)

    @staticmethod
    def convert_catalogue_func(**kwargs):
        element = kwargs.get("element")
        text_el = element.find("{http://note.youdao.com}text")
        indent_el = element.find(".//{http://note.youdao.com}indent")
        text = text_el.text.strip() if text_el is not None and text_el.text else ""
        indent = int(indent_el.text) if indent_el is not None and indent_el.text else 0
        prefix = "  " * indent + "- "
        anchor = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', text.strip())
        anchor = re.sub(r'\s+', '-', anchor).strip('-')
        return prefix + "[{text}](#{anchor})".format(text=text, anchor=anchor)

    @staticmethod
    def convert_heading_func(**kwargs):
        """标题"""
        level = kwargs.get("element").attrib.get("level", 0)
        level = 1 if level in (["a", "b"]) else level
        text = kwargs.get("text")
        # 补丁：确保层级为数字且在 1-6 之间
        try:
            h_level = int(level)
        except:
            h_level = 1
        h_level = max(1, min(6, h_level))
        return " ".join(["#" * h_level, text.strip()]) if text else text

    @staticmethod
    def convert_image_func(**kwargs):
        """图片"""
        image_url = XmlElementConvert.get_text_by_key(
            list(kwargs.get("element")), "source"
        )
        return "![{text}]({image_url})".format(
            text=kwargs.get("text"), image_url=image_url
        )

    @staticmethod
    def convert_attach_func(**kwargs):
        """附件"""
        element = kwargs.get("element")
        filename = XmlElementConvert.get_text_by_key(list(element), "filename")
        resource_url = XmlElementConvert.get_text_by_key(list(element), "resource")
        return "[{text}]({resource_url})".format(
            text=filename, resource_url=resource_url
        )

    @staticmethod
    def convert_code_func(**kwargs):
        """代码块"""
        language = XmlElementConvert.get_text_by_key(
            list(kwargs.get("element")), "language"
        )
        return "```{language}\n{code}```".format(
            language=language, code=kwargs.get("text")
        )

    @staticmethod
    def convert_todo_func(**kwargs):
        """
        修复：XML 待办事项状态识别
        """
        element = kwargs.get("element")
        state = element.attrib.get("state", "todo")
        checkbox = "- [x]" if state == "done" else "- [ ]"
        return "{checkbox} {text}".format(checkbox=checkbox, text=kwargs.get("text"))

    @staticmethod
    def convert_quote_func(**kwargs):
        """引用"""
        return "> {text}".format(text=kwargs.get("text"))

    @staticmethod
    def convert_horizontal_line_func(**kwargs):
        """分割线"""
        return "---"

    @staticmethod
    def convert_list_item_func(**kwargs):
        """列表"""
        list_id = kwargs.get("element").attrib["list-id"]
        is_ordered = kwargs.get("list_item").get(list_id)
        text = kwargs.get("text")
        if is_ordered == "unordered":
            # 补丁：使用空格缩进提高 Obsidian 兼容性
            return "- {text}".format(text=text)
        elif is_ordered == "ordered":
            num = _ordered_counters.get(list_id, 0) + 1
            _ordered_counters[list_id] = num
            return "{}. {text}".format(num, text=text)

    @staticmethod
    def convert_table_func(**kwargs):
        """
        表格转换 (完整保留原版逻辑)
        """
        element = kwargs.get("element")
        content = XmlElementConvert.get_text_by_key(element, "content")

        table_data_str = f""
        table_data = json.loads(content)
        table_data_len = len(table_data["widths"])
        table_data_arr = []
        table_data_line = []

        for cells in table_data["cells"]:
            values = cells.get("value")
            if values is None:
                values = ""
            cell_value = XmlElementConvert._encode_string_to_md(values)
            table_data_line.append(cell_value)
            if len(table_data_line) == table_data_len:
                table_data_arr.append(table_data_line)
                table_data_line = []

        if len(table_data_arr) == 1:
            table_data_arr.insert(0, [ch for ch in (" " * table_data_len)])
            table_data_arr.insert(1, [ch for ch in ("-" * table_data_len)])
        elif len(table_data_arr) > 1:
            table_data_arr.insert(1, [ch for ch in ("-" * table_data_len)])

        for table_line in table_data_arr:
            table_data_str += "|"
            for table_data in table_line:
                table_data_str += f" %s |" % table_data
            table_data_str += "\n"

        return table_data_str

    @staticmethod
    def get_text_by_key(element_children, key="text"):
        for sub_element in element_children:
            if key in sub_element.tag:
                return sub_element.text if sub_element.text else ""
        return ""

    @staticmethod
    def _encode_string_to_md(original_text):
        """完整保留原版 20 行转义逻辑"""
        if len(original_text) <= 0 or original_text == " ":
            return original_text

        original_text = original_text.replace("\\", "\\\\")
        original_text = original_text.replace("*", "\\*")
        original_text = original_text.replace("_", "\\_")
        original_text = original_text.replace("#", "\\#")
        original_text = original_text.replace("&", "&amp;")
        original_text = original_text.replace("<", "&lt;")
        original_text = original_text.replace(">", "&gt;")
        original_text = original_text.replace("“", "&quot;")
        original_text = original_text.replace("‘", "&apos;")
        original_text = original_text.replace("\t", "&emsp;")
        original_text = original_text.replace("\r\n", "<br>")
        original_text = original_text.replace("\n\r", "<br>")
        original_text = original_text.replace("\r", "<br>")
        original_text = original_text.replace("\n", "<br>")

        return original_text


class JsonConvert(object):
    """
    json 转换规则
    """

    def _get_common_text(self, content: dict) -> str:
        all_text = ""
        five_contents = content.get("5")
        if five_contents:
            seven_contents = five_contents[0].get("7")
            if not seven_contents:
                return all_text
            for seven_content in seven_contents:
                text = seven_content.get("8")
                text_attrs = seven_content.get("9")
                if text and text_attrs:
                    text = self._convert_text_attribute(text, text_attrs)
                all_text += text if text else ""
        return all_text

    def _convert_text_attribute(self, text: str, text_attrs: list):
        if isinstance(text_attrs, list) and text_attrs and text:
            for attr in text_attrs:
                # 2 代表属性类型: b(粗体), i(斜体), d/s(删除线)
                if attr["2"] == "b":
                    text = f"**{text}**"
                elif attr["2"] == "i":
                    text = f"*{text}*"
                elif attr["2"] in ("s", "d"):
                    text = f"~~{text}~~"
        return text

    def convert_text_func(self, content) -> str:
        """修复：处理外链、内链，防止文本因解析失败变空白"""
        all_text = ""
        one_five_contents = content.get("5")
        if one_five_contents:
            for one_five_content in one_five_contents:
                two_five_contents = one_five_content.get("5")
                text_type = one_five_content.get("6")
                seven_contents = one_five_content.get("7")

                if seven_contents and not two_five_contents:
                    text = ""
                    for seven_content in seven_contents:
                        raw = seven_content.get("8")
                        text_attrs = seven_content.get("9")
                        if raw and text_attrs:
                            raw = self._convert_text_attribute(raw, text_attrs)
                        text += raw if raw else ""
                
                # 修复核心：识别 li (外链) 和 nli (可能出现的内链)
                elif (text_type == "li" or text_type == "nli") and two_five_contents:
                    source_text = self._get_common_text(one_five_content)
                    four_contents = one_five_content.get("4")
                    if four_contents:
                        # 尝试获取外链 hf 或内链相关的 id/rid 键
                        url = four_contents.get("hf") or four_contents.get("id") or four_contents.get("rid")
                        if url:
                            text = f"[{source_text}]({url})"
                        else:
                            text = source_text # 兜底：保留文字
                    else:
                        text = source_text # 兜底：保留文字
                else:
                    # 尝试保留该节点的所有可见文字，防止空白
                    text = self._get_common_text(one_five_content)
                
                if text:
                    all_text += text
        return all_text

    def convert_h_func(self, content) -> str:
        type_name = content.get("4").get("l")
        text = self._get_common_text(content=content)
        if text and type_name:
            if type_name.startswith('h'):
                level = int(type_name.replace("h", ""))
                text = " ".join(["#" * int(level), text.strip()])
        return text

    def convert_im_func(self, content):
        image_url = content["4"]["u"]
        return "![]({image_url})".format(image_url=image_url)

    def convert_a_func(self, content):
        fn = content["4"]["fn"]
        fl = content["4"]["re"]
        return "[{text}]({resource_url})".format(text=fn, resource_url=fl)

    def convert_cd_func(self, content):
        language = content.get("4").get("la")
        codes: list = content.get("5")
        code_block = ""
        for code in codes:
            text = self._get_common_text(code)
            code_block += text + "\n"
        return "```{language}\n{code_block}```".format(
            language=language, code_block=code_block
        )

    def convert_la_func(self, content):
        lines: list = content.get("5")
        highlight_block = ""
        for line in lines:
            text = self._get_common_text(line)
            highlight_block += text + "\n"
        return "```\n{highlight_block}```".format(highlight_block=highlight_block)

    def convert_q_func(self, content):
        q_text_list = content["5"]
        text = ""
        for q_text_dict in q_text_list:
            q_text = self._get_common_text(q_text_dict)
            q_text = q_text.replace("\n", "")
            text += "> {q_text}\n".format(q_text=q_text)
        return text

    def convert_l_func(self, content):
        text = self._get_common_text(content=content)
        is_ordered = content.get("4").get("lt")
        level = content.get("4").get("ll", 1)
        indent = "    " * (level - 1)
        if is_ordered == "unordered":
            return indent + "- {text}".format(text=text)
        elif is_ordered == "ordered":
            li = content.get("4").get("li")
            key = (li, level)
            num = _ordered_counters.get(key, 0) + 1
            _ordered_counters[key] = num
            return indent + "{}. {text}".format(num, text=text)

    def convert_todo_func(self, content):
        """
        修复：JSON 待办事项识别 ls 状态
        """
        text = self._get_common_text(content=content)
        ls = content.get("4", {}).get("ls", "todo")
        checkbox = "- [x]" if ls == "done" else "- [ ]"
        return "{checkbox} {text}".format(checkbox=checkbox, text=text)

    def convert_t_func(self, content):
        tr_list = content["5"]
        table_lines = ""
        for index, tc in enumerate(tr_list):
            table_content_list = tc["5"]
            table_content_len = len(table_content_list)
            if index == 1:
                table_line = "| -- " * table_content_len + "|\n| "
            else:
                table_line = "| "
            for table_content in table_content_list:
                try:
                    cell_parts = []
                    # 遍历 cell 的所有子元素
                    for child in table_content.get("5", []):
                        # 遍历 child 的所有子元素（文本/链接）
                        for sub in child.get("5", []):
                            # 普通文本（type=2）
                            if sub.get("7"):
                                table_text = sub["7"][0].get("8", " ")
                                cell_parts.append(table_text)
                            # 超链接（type=li）
                            elif sub.get("6") == "li":
                                link_text = self._get_common_text(sub)
                                if link_text:
                                    cell_parts.append(link_text)
                    table_text = " ".join(cell_parts).strip() or " "
                except Exception:
                    table_text = " "
                table_line = table_line + table_text + " | "
            table_lines = table_lines + table_line.rstrip() + "\n"
        return table_lines


class YoudaoNoteConvert(object):
    """
    入口转换逻辑 (完整保留并注入补丁)
    """

    @staticmethod
    def covert_html_to_markdown(file_path):
        with open(file_path, "rb") as f:
            content_str = f.read().decode("utf-8")
        from markdownify import markdownify as md
        new_content = md(content_str)
        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        os.rename(file_path, new_file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode())

    @staticmethod
    def _covert_xml_to_markdown_content(file_path):
        _reset_ordered_counters()
        element_tree = ET.parse(file_path)
        note_element = element_tree.getroot()

        list_item = {}
        for child in note_element[0]:
            if "list" in child.tag:
                list_item[child.attrib["id"]] = child.attrib["type"]

        body_element = note_element[1]
        processed = []
        for element in list(body_element):
            text = XmlElementConvert.get_text_by_key(list(element))
            tag_raw = element.tag.split("}")[-1]
            name = "todo" if "todo" in tag_raw else tag_raw.replace("-", "_")

            convert_func = getattr(
                XmlElementConvert, "convert_{}_func".format(name), None
            )
            if not convert_func:
                line_content = XmlElementConvert._parse_rich_text(element)
            else:
                line_content = convert_func(text=text, element=element, list_item=list_item)

            if line_content:
                processed.append({
                    "type": name if convert_func else None,
                    "content": line_content.rstrip("\r\n"),
                })

        result = []
        for i, item in enumerate(processed):
            content = item["content"]
            current_type = item["type"]

            # 非列表行：删除行首所有空白字符（含 \xa0 等 Unicode 空白），避免渲染为代码块
            content_stripped = content.lstrip()
            is_list_item = re.match(r'(?:[-*+]|\d+\.)\s', content_stripped) is not None
            if not is_list_item:
                content = content_stripped

            is_bold_line = re.match(r"^\*\*.+\*\*$", content) is not None

            if is_bold_line and result:
                result.append("")

            is_block = re.match(r'^\s*(?:[-*+]|\d+\.)\s', content) or content.startswith("|") or content.startswith("#")
            result.append(content if is_block else content + "  ")

            if i < len(processed) - 1:
                next_type = processed[i + 1]["type"]
                next_content = processed[i + 1]["content"]
                next_is_bold_line = re.match(r"^\*\*.+\*\*$", next_content) is not None

                if current_type == "catalogue" or next_type == "catalogue":
                    continue
                if current_type in ["h", "heading"] or next_type in ["h", "heading"]:
                    result.append("")
                elif not is_bold_line and not next_is_bold_line and \
                     current_type != next_type and \
                     current_type is not None and next_type is not None:
                    result.append("")

        return _normalize_blank_lines("\n".join(result))

    @staticmethod
    def covert_xml_to_markdown(file_path) -> bool:
        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        if os.path.getsize(file_path) == 0:
            os.rename(file_path, new_file_path)
            return False
        new_content = YoudaoNoteConvert._covert_xml_to_markdown_content(file_path)
        os.remove(file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode("utf-8"))
        return True

    @staticmethod
    def _covert_json_to_markdown_content_smart(file_path):
        _reset_ordered_counters()
        new_content_list = []
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                json_data = json.load(f)
            except Exception as e:
                logging.error(e)
                json_data = {}

        json_contents = json_data.get("5", [])
        
        processed = []
        for content in json_contents:
            ctype = content.get("6")
            if ctype and "todo" in ctype:
                line_content = JsonConvert().convert_todo_func(content)
            elif ctype:
                convert_func = getattr(JsonConvert(), "convert_{}_func".format(ctype), None)
                line_content = convert_func(content) if convert_func else JsonConvert().convert_text_func(content)
            else:
                line_content = JsonConvert().convert_text_func(content)
            
            if line_content:
                processed.append({
                    'type': ctype,
                    'content': line_content.rstrip('\r\n')
                })
        
        result = []
        for i, item in enumerate(processed):
            content = item['content']
            current_type = item['type']

            # 非列表行：删除行首所有空白字符（含 \xa0 等 Unicode 空白），避免渲染为代码块
            content_stripped = content.lstrip()
            is_list_item = re.match(r'(?:[-*+]|\d+\.)\s', content_stripped) is not None
            if not is_list_item:
                content = content_stripped

            is_bold_line = re.match(r'^\*\*.+\*\*$', content) is not None

            if is_bold_line and result:
                result.append('')

            no_trailing = re.match(r'^\s*(?:[-*+]|\d+\.)\s', content) is not None or content.startswith('|')
            result.append(content if no_trailing else content + '  ')

            if i < len(processed) - 1:
                next_type = processed[i+1]['type']
                next_content = processed[i+1]['content']
                next_is_bold_line = re.match(r'^\*\*.+\*\*$', next_content) is not None

                if current_type in ['h'] or next_type in ['h']:
                    result.append('')
                elif not is_bold_line and not next_is_bold_line and \
                     current_type != next_type and \
                     current_type is not None and next_type is not None:
                    result.append('')

        return _normalize_blank_lines("\n".join(result))

    @staticmethod
    def covert_json_to_markdown(file_path) -> str:
        base = os.path.splitext(file_path)[0]
        new_file_path = "".join([base, MARKDOWN_SUFFIX])
        if os.path.getsize(file_path) == 0:
            os.rename(file_path, new_file_path)
            return False
        new_content = YoudaoNoteConvert._covert_json_to_markdown_content_smart(file_path)
        with open(new_file_path, "wb") as f:
            f.write(new_content.encode("utf-8"))
        if os.path.exists(file_path):
            os.remove(file_path)
        return new_file_path
