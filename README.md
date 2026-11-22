# 地方志引文核对

地方志 OCR 页稿换成校样后，用来自动核对引文位置的小系统：Flask 网页 + SQLite
存储历版页稿与每版引文核对结果。

## 运行

```bash
docker compose up --build        # 打开 http://localhost:8000
```

本地开发（Python 3.11+）：

```bash
pip install -r requirements.txt
DATABASE_PATH=./data/app.db python -m app.main
pip install -r requirements-dev.txt
python -m pytest tests/          # 自测
```

## 使用

1. **首版**：上传一页纯文本（UTF-8，≤1200 字）和引用 CSV
   （`引用号,起始下标,结束下标`，下标从 0 起算、右端不含，首行可为表头）。
2. **上传校样**：只传新页稿即可。每条引用以它**最近一次成功定位的页稿**为源稿，
   与新校样做整页比对（同一对稿件只比对一次，多条引用共用）：
   - 增、删、替换一个字符各记一次改动；
   - **所有**改动最少的对齐都把该引文逐字对到校样中同一段连续位置 → 登记新位置；
   - 否则标为**待核对**，显示最近一次定位成功的版号和位置。
3. 后续版本也可以再附 CSV：用于登记新引用或人工修正既有引用（下标按新稿计）。
4. 新版页稿与核对结果在一个事务里一起发布；任何一步失败都回滚，
   继续提供上一版。历史版按版号查询。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 最新版网页（含上传表单、各版导航） |
| GET | `/pages/<版号>` | 指定版网页 |
| POST | `/pages` | 表单上传（`text` 文件、`citations` CSV 文件可省） |
| POST | `/api/pages` | JSON 发布：`{"text": "...", "citations_csv": "..."}` |
| GET | `/api/pages/latest` | 最新版 JSON |
| GET | `/api/pages/<版号>` | 指定版 JSON |
| GET | `/api/versions` | 版号列表 |

JSON 中每条引用：`status`（`located`/`pending`）、`start`/`end`、
`located_version`、`excerpt`。`pending` 时 `start`/`end` 是最近一次定位成功的
版号（`located_version`）中的位置。

## 判定方法（`app/align.py`）

对源稿、新稿各算一张完整 Levenshtein DP 表（前缀、后缀方向各一）。源稿第 k 个
字符在“所有最优对齐”中的去向由两张表推出：能逐字匹配到新稿第 j 字，当且仅当
`d[k][j] + d2[k+1][j+1] == D`（D 为两稿最小编辑距离）；同理可判定它是否可能在
某种最优对齐里被删除或替换。一条引文的每个字符都唯一匹配且落点连续，才登记新
位置。重复句删其一、引文内有增删改等情况，落点不唯一，自动转待核对。

## 未完成项 / 说明

- 下标按 Unicode 码点计（与 Python 字符串一致）；未做全半角、繁简、
  组合字符等规范化，录入前需先统一文本。
- 无登录/权限控制，仅限内网资料员使用；生产部署请自行加认证并设置
  `SECRET_KEY` 环境变量。
- 整页比对为 O(字数²) 纯 Python 实现，1200 字上限内约 1 秒；若放宽字数
  上限需换更快的实现（如 C 扩展或分块比对）。
- 并发上传依赖 SQLite 单写者 + busy_timeout，未做显式排队；多资料员同时
  上传时后到者可能需重试。
- 待核对条目目前只展示、不能在网页上直接改判；改判途径是下一版随 CSV
  重新登记。
