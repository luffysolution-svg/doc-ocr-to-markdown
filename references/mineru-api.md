# MinerU 云端 OCR API

MinerU 官方 API 可对 PDF / 图片做 OCR，同时输出文字、LaTeX 公式、表格结构和插图切分。需要 Token（读环境变量 `MINERU_API_TOKEN`）。

## 接口契约

### 1. 申请上传链接

```http
POST https://mineru.net/api/v4/file-urls/batch
Authorization: Bearer $MINERU_API_TOKEN
Content-Type: application/json
```

```json
{
  "model_version": "vlm",
  "enable_formula": true,
  "enable_table": true,
  "language": "ch",
  "files": [
    {"name": "01.pdf", "data_id": "ch01", "is_ocr": true}
  ]
}
```

参数要点：

- `model_version`：用 `vlm`，公式和版面识别质量明显更好。
- `enable_formula` / `enable_table`：必须开，否则不给公式 LaTeX 和表格结构。
- `language`：中文文档用 `ch`。
- `files[]` 里的 `is_ocr`、`page_ranges` 属于**每个文件**；`model_version`、`enable_formula`、`enable_table`、`language` 属于**顶层**。放错位置会静默忽略。
- `data_id` 是自己分配的标识，用于回传时对应文件，建议用有意义的名字。

返回 `data.batch_id` 和 `data.file_urls`（数组，顺序与 `files` 一致）。

### 2. 上传文件

对 `file_urls` 里每个 URL 做 `PUT`，body 是**原始二进制**：

```python
with open(path,'rb') as f:
    requests.put(url, data=f, timeout=300)
```

- **不要**带 `Authorization`
- **不要**用 `multipart/form-data`
- 按 `files` 的顺序对应，别弄错

上传完成会自动创建解析任务，不需要再调接口提交。

### 3. 查询结果

```http
GET https://mineru.net/api/v4/extract-results/batch/{batch_id}
Authorization: Bearer $MINERU_API_TOKEN
```

`data.extract_result[]` 每项含 `data_id`、`file_name`、`state`、`err_msg`、`extract_progress`、`full_zip_url`。

`state` 取值：`waiting-file` → `pending` → `running` → `done` / `failed`。`running` 时 `extract_progress` 给出 `extracted_pages` / `total_pages`。

轮询间隔 10–20 秒。几百页约十几分钟。

### 4. 下载结果

`state == "done"` 后下 `full_zip_url`。**这里有个坑，见下节。**

## 结果 zip 结构

```text
<uuid>_content_list.json      # 主要数据源：每块含 type/text/bbox/page_idx
<uuid>_content_list_v2.json   # 更细的结构（标题层级、行内公式分离）
<uuid>_model.json             # 模型原始输出
<uuid>_origin.pdf             # 回传的原 PDF
full.md                       # 拼接好的 Markdown（可参考，但结构信息少）
layout.json                   # 版面分析
images/                       # 切出的插图
```

### content_list.json 的块类型

| type | 关键字段 | 说明 |
| --- | --- | --- |
| `text` | `text`, `text_level`, `bbox` | 正文。`text_level` 有值表示是标题 |
| `equation` | `text`, `text_format: "latex"` | 公式，已是 LaTeX，独立公式自带 `$$...$$` |
| `image` | `img_path`, `image_caption`, `image_footnote`, `bbox` | 插图 |
| `chart` | `img_path`, `chart_caption`, `chart_footnote`, `bbox` | 图表（曲线图等），字段名与 image 不同 |
| `table` | `table_body`(HTML), `table_caption`, `table_footnote` | 表格，body 是 HTML 需转换 |
| `list` | `list_items` | 列表 |
| `header`/`footer`/`page_number`/`page_footnote` | `text` | 页眉页脚页码，应丢弃 |

**注意 `image` 和 `chart` 是两种类型，字段名不同**（`image_caption` vs `chart_caption`）。只处理 `image` 会漏掉所有曲线图。

### bbox 坐标系

**`bbox` 是 0–1000 的归一化坐标** `[x0, y0, x1, y1]`，不是像素、不是 PDF point。换算：

```python
x0 = box[0] / 1000 * page_width
y0 = box[1] / 1000 * page_height
```

用它裁图或和源文件对象做位置比对前，两边都要先统一到同一坐标系。

## 坑：结果下载域名连不上

结果 zip 托管在 `cdn-mineru.openxlab.org.cn`。**如果本机 DNS 被代理的 fake-IP 接管，这个域名会解析成 `198.18.0.62` 之类的保留地址**，表现为 Python `requests` 和 `curl` 都在 TLS 握手阶段 EOF：

```text
ssl.SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]
curl: (35) schannel: failed to receive handshake, SSL/TLS connection failed
```

`https://mineru.net` 本身能通（所以容易误判为"网络正常"），只有这个 CDN 域名不通。

**处理**：用公共 DoH 拿真实 IP，再指定连接目标。保留 TLS 校验，不要 `-k`，不要改系统代理设置。

```bash
# 取真实 IP
curl -s "https://dns.alidns.com/resolve?name=cdn-mineru.openxlab.org.cn&type=A"

# 指定 IP 下载
curl --resolve cdn-mineru.openxlab.org.cn:443:<真实IP> -L -o out.zip "<full_zip_url>"
```

多个 IP 依次重试。`scripts/mineru_ocr.py` 里已实现这套回退逻辑。

其他公开 DoH 端点：`https://cloudflare-dns.com/dns-query?name=<域名>&type=A`（需 `Accept: application/dns-json`）。

## 限额

- 单文件通常 200 MB / 600 页以内
- 单批文件数有上限（约 200）
- 超限的任务会被拒绝，按批次拆分

具体数值以官方文档为准：<https://mineru.net/apiManage/docs>

## 本地模型（无 Token 时）

MinerU 也提供本地部署（`pip install mineru`），功能等价但需下载模型、需要 GPU 才快。有 Token 时优先用云端——不用管环境依赖，且版面模型更新更及时。
