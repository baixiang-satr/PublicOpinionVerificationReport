"""
ZIP 打包器 — 将通过校验的 staging/template 目录打包为固定 template.zip。

归档中必须保留 template/ 顶层目录；日志、Cookie、调试 HTML、JSON 和浏览器缓存禁止进入。
打包使用临时文件并原子替换，避免产生不完整的成功文件。
"""
pass
