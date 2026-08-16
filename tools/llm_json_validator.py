#!/usr/bin/env python3
"""llm_json_validator.py — LLM 输出 JSON 修复链（移植自 PA_Agent json_validator.py）。

AI Berkshire 买卖闭环 v7：lite 速评 verdict 提取、归一拆写四文件解析，
此前用弱正则，遇到"带围栏/未转义引号/截断/前后废话"的模型输出直接失败。
本模块提供通用修复链：剥围栏 → 引号清洗 → 外层对象扫描 → 分号/引号修复
→ 括号平衡 → 未闭合字符串修补，逐级尝试解析。

错误分类（对齐 PA_Agent）：
  a. 语法错误（可修复）   b. 缺字段   c. 非法值   d. 纯文本（无 JSON）

用法：
    from llm_json_validator import repair_json
    ok, obj, err_class = repair_json(model_text)
"""

import json
import re

# 非标准 Unicode 引号/连字符 → 标准 ASCII
_SMART_QUOTE_MAP = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u2013": "-",
    "\u2014": "-",
}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_LEADING_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*")
_TRAILING_FENCE_RE = re.compile(r"```\s*$")
# 引号结束判定：下一个非空白字符是结构符（, : } ] 或 EOF）才算字符串结束
_STRING_END_CHARS = ",:}]"


def _strip_fences(text: str) -> str:
    """智能引号清洗 + 控制字符清理 + 围栏提取。"""
    t = text.strip()
    if not t:
        return t
    for bad, good in _SMART_QUOTE_MAP.items():
        t = t.replace(bad, good)
    # 去掉除 \t \n \r 外的控制字符
    t = "".join(ch for ch in t if ch >= " " or ch in "\t\n\r")
    # 优先：正文中嵌入的 ```json ... ``` 围栏
    m = _FENCE_RE.search(t)
    if m:
        t = m.group(1).strip()
        return _repair_unescaped_quotes(
            _repair_semicolon_separator(_extract_outer_json_object(t)))
    # 顶部围栏 / 尾部孤立围栏
    if t.startswith("```"):
        t = _LEADING_FENCE_RE.sub("", t, count=1).strip()
    t = _TRAILING_FENCE_RE.sub("", t).strip()
    return _repair_unescaped_quotes(
        _repair_semicolon_separator(_extract_outer_json_object(t)))


def _extract_outer_json_object(text: str) -> str:
    """取第一个顶层 {...} 对象，忽略前后废话/围栏。"""
    start = text.find("{")
    if start < 0:
        return text.strip()
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:].strip()


def _repair_unescaped_quotes(text: str) -> str:
    """修复字符串值内未转义的引号：仅当下一个非空白字符是结构符时视为字符串结束。"""
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
            continue
        if escape:
            escape = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in _STRING_END_CHARS:
                in_string = False
                out.append(ch)
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_semicolon_separator(text: str) -> str:
    """修复结构分隔位误用的分号：'field': 'value'; 后面跟 " 或 } 时替换为逗号。"""
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ";":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in ('"', '}', ']'):
                out.append(",")
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _balance_json_brackets(text: str) -> str:
    """闭合未关闭的 { / [（字符串外）。"""
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            stack.append("{")
        elif ch == "[":
            stack.append("[")
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    closers = "".join("]" if opener == "[" else "}" for opener in reversed(stack))
    return text + closers


def _repair_unclosed_string_before_brace(text: str) -> str:
    """修复长字段中"原始换行后紧跟 }/]"造成的未闭合字符串：在 } 前补引号。"""
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
            continue
        if escape:
            escape = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = False
            out.append(ch)
            i += 1
            continue
        if ch == "\n":
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] in ("}", "]"):
                out.append('"')
                in_string = False
                out.append(ch)
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_eof_truncation(text: str) -> str:
    """修复 EOF 处截断：字符串未闭合（引号数为奇）时在末尾补引号，再平衡括号。"""
    t = text.rstrip()
    # 统计字符串外引号奇偶（粗略：全文引号奇偶即可覆盖绝大多数模型输出）
    if t.count('"') % 2 == 1:
        t += '"'
    return _balance_json_brackets(t)


def classify_error(text: str, exc: json.JSONDecodeError) -> str:
    """错误分类：a 语法 / d 纯文本（更细分 b/c 需 schema，由调用方判断）。"""
    if "{" not in text and "[" not in text:
        return "d"
    return "a"


def repair_json(text: str):
    """主入口：逐级修复尝试解析。

    返回 (ok, obj_or_None, err_class)：
      ok=True  → obj 为解析结果，err_class=None
      ok=False → obj=None，err_class 为 'a'（语法，修复链耗尽）或 'd'（无 JSON 内容）
    """
    if not text or not text.strip():
        return False, None, "d"
    raw = text.strip()

    # 1. 直接解析
    try:
        return True, json.loads(raw), None
    except json.JSONDecodeError as e:
        err_class = classify_error(raw, e)

    # 2. 修复链逐级尝试（每级独立尝试，成功即返回）
    chain = [
        ("strip_fences", _strip_fences),
        ("unclosed_string", _repair_unclosed_string_before_brace),
    ]
    for name, fn in chain:
        try:
            fixed = fn(raw)
        except Exception:
            continue
        try:
            return True, json.loads(fixed), None
        except json.JSONDecodeError:
            pass

    # 3. 组合链：围栏清洗 → 括号平衡
    try:
        fixed = _balance_json_brackets(_strip_fences(raw))
        try:
            return True, json.loads(fixed), None
        except json.JSONDecodeError:
            pass
    except Exception:
        pass

    # 4. 未闭合字符串 → 括号平衡
    try:
        fixed = _balance_json_brackets(_repair_unclosed_string_before_brace(raw))
        return True, json.loads(fixed), None
    except (json.JSONDecodeError, Exception):
        pass

    # 5. EOF 截断修复（字符串截断在文件尾）
    try:
        fixed = _repair_eof_truncation(raw)
        if fixed != raw.rstrip():
            return True, json.loads(fixed), None
    except (json.JSONDecodeError, Exception):
        pass

    return False, None, err_class


def repair_verdict(text: str):
    """lite 速评专用：从模型输出中提取 verdict JSON（含修复链）。

    返回 (ok, verdict_dict_or_None)。verdict_dict 至少含 verdict/reason 键。
    """
    ok, obj, _ = repair_json(text)
    if not ok:
        return False, None
    if isinstance(obj, dict) and "verdict" in obj:
        return True, obj
    # 修复后仍缺字段 → 归类 b（缺字段）
    return False, None
