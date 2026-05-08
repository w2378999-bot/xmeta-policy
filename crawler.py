"""
X-META 补贴政策爬虫 + Dify 知识库自动更新脚本 v3.0
方案A（直接爬取） + 方案B（搜索引擎API）双引擎 + LLM自动填字段

使用方法：
  python crawler.py                          # 双引擎爬取+LLM填字段+保存本地（推荐）
  python crawler.py search                   # 仅运行搜索引擎模块
  python crawler.py crawl                    # 仅运行直接爬取模块
  python crawler.py upload 待审核_xxx.txt    # 审核完后上传Dify
"""

import requests
import json
import time
import hashlib
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

# ============================================================
# 配置区（只需改这里）
# ============================================================

import os

# ── API Key 配置 ──
# 本地运行：直接在下面填写（或设置环境变量）
# GitHub Actions：从 Secrets 自动读取，不需要改这里
DIFY_API_KEY    = os.environ.get("DIFY_API_KEY",    "dataset-你的知识库APIKey填这里")
SERPER_API_KEY  = os.environ.get("SERPER_API_KEY",  "你的SerperKey填这里")
LLM_API_KEY     = os.environ.get("LLM_API_KEY",     "你的通义千问Key填这里")

DIFY_DATASET_ID = "37b890bd-a667-4188-a02f-2ee75124fccb"
DIFY_BASE_URL   = "https://api.dify.ai/v1"

# 全自动模式：LLM过滤→填字段→直接上传
AUTO_MODE = True

# ---- 方案B：Serper 搜索API ----
# 已注册：serper.dev，注册即送2500次免费调用
# 填入Key后把 SERPER_ENABLED 改为 True 即可
# SERPER_API_KEY 已在上方从环境变量读取
SERPER_SEARCH_URL = "https://google.serper.dev/search"
SERPER_ENABLED = True   # GitHub Actions 自动启用

# ---- LLM自动填字段 ----
# 推荐通义千问Qwen-Plus（国内直连，约¥0.004/千token，每条政策约¥0.01）
# 申请地址：https://dashscope.aliyun.com → 开通DashScope → 获取API Key
# 也可以用 OpenAI/Claude，把 LLM_BASE_URL 和 LLM_MODEL 换掉即可
# LLM_API_KEY 已在上方从环境变量读取
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = "qwen-plus-2025-07-28"
LLM_ENABLED = True   # GitHub Actions 自动启用

CACHE_FILE = "crawled_cache.json"
LOG_FILE = "crawler_log.txt"

# ============================================================
# 方案B：搜索关键词矩阵（覆盖爬虫访问不到的来源）
# ============================================================

SEARCH_QUERIES = [
    # ===== 国家级 =====
    {"query": "元宇宙 补贴 申报 2026 site:.gov.cn", "city": "国家级", "dept": "科创科委口"},
    {"query": "VR XR 沉浸式 补贴 申报 工信部 2026", "city": "国家级", "dept": "科创科委口"},
    {"query": "元宇宙典型案例 申报 工业和信息化部 2026", "city": "国家级", "dept": "科创科委口"},
    {"query": "数字文旅 沉浸式 补贴 文化和旅游部 2026", "city": "国家级", "dept": "文旅局口"},
    {"query": "数字创意 文化产业 补贴 申报 2026 site:.gov.cn", "city": "国家级", "dept": "文旅局口"},
    # ===== 江苏省（原爬虫521被拦）=====
    {"query": "江苏省 文化旅游 数字文旅 补贴 申报 2026", "city": "江苏省", "dept": "文旅局口"},
    {"query": "江苏省 元宇宙 VR 沉浸式 产业扶持 2026", "city": "江苏省", "dept": "科创科委口"},
    {"query": "南京 数字文旅 VR 沉浸式 补贴 2026", "city": "南京", "dept": "文旅局口"},
    # ===== 黑龙江/哈尔滨（原爬虫403）=====
    {"query": "哈尔滨 VR AR 冰雪 沉浸式 补贴 奖励 2026", "city": "哈尔滨", "dept": "文旅局口"},
    {"query": "黑龙江省 文化旅游 数字 补贴 申报 2026", "city": "黑龙江省", "dept": "文旅局口"},
    {"query": "哈尔滨 元宇宙 数字经济 扶持 2026", "city": "哈尔滨", "dept": "科创科委口"},
    # ===== 上海补充 =====
    {"query": "上海 元宇宙新赛道 申报 2026 site:sh.gov.cn", "city": "上海", "dept": "科创科委口"},
    {"query": "上海市科委 元宇宙 VR XR 项目申报 2026", "city": "上海", "dept": "科创科委口"},
    {"query": "上海 数字文旅 文旅元宇宙 补贴 2026", "city": "上海", "dept": "文旅局口"},
    {"query": "上海 服务消费 文创 沉浸式 补贴 申报 2026", "city": "上海", "dept": "商委口"},
    # ===== 深圳补充 =====
    {"query": "深圳 数字创意 VR 游戏 补贴 扶持 2026", "city": "深圳", "dept": "文旅口"},
    {"query": "深圳 人工智能 元宇宙 示范场景 补贴 2026", "city": "深圳", "dept": "科创科委口"},
    {"query": "深圳 工信局 XR 沉浸式 软件信息 补贴 2026", "city": "深圳", "dept": "科创科委口"},
    # ===== 广东省 =====
    {"query": "广东省 元宇宙 数字文娱 VR 产业补贴 2026", "city": "广东省", "dept": "科创科委口"},
    {"query": "广东省 数字文旅 文化创意 补贴 申报 2026", "city": "广东省", "dept": "文旅局口"},
    # ===== 全国发现新政策 =====
    {"query": "VR体验 元宇宙 文旅融合 政府补贴 申报 2026", "city": "全国", "dept": "综合"},
    {"query": "沉浸式体验 数字文娱 新型业态 补贴 奖励 2026 site:.gov.cn", "city": "全国", "dept": "综合"},
    {"query": "全感VR 沉浸式游戏 补贴 申报 2026", "city": "全国", "dept": "综合"},
    {"query": "消费新业态 沉浸式 元宇宙 新场景 补贴 申报 2026", "city": "全国", "dept": "商委口"},

    # ===== 新发现城市（本次搜索结果扩展）=====
    {"query": "苏州 元宇宙 VR 沉浸式 数字文旅 补贴 申报 2026", "city": "苏州", "dept": "科创科委口"},
    {"query": "苏州 人工智能 数字创意 产业扶持 2026", "city": "苏州", "dept": "科创科委口"},
    {"query": "武汉 元宇宙 文旅 VR 沉浸式 补贴 2026", "city": "武汉", "dept": "文旅局口"},
    {"query": "武汉 数字文旅 元宇宙 申报 扶持 2026 site:wuhan.gov.cn", "city": "武汉", "dept": "文旅局口"},
    {"query": "济南 消费新业态 沉浸式 元宇宙 补贴 2026", "city": "济南", "dept": "商委口"},
    {"query": "济南 VR 数字文旅 文化创意 补贴 申报 2026", "city": "济南", "dept": "文旅局口"},
]

# ============================================================
# 目标网站（基于你提供的真实URL，共21个）
# ============================================================

TARGET_SITES = [

    # ===== 国家级 =====
    {
        "city": "国家级", "department": "科创科委口",
        "name": "工业和信息化部",
        "url": "https://www.miit.gov.cn/xwfb/zxzc/index.html",
        "keywords": ["元宇宙", "VR", "XR", "数字文娱", "沉浸式", "虚拟现实", "人工智能"],
        "list_selector": ".uni-main .xw-item a, .list-content li a, ul.zxzc-list li a",
        "title_selector": ".arti-title, h1",
        "content_selector": ".arti-content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "国家级", "department": "文旅局口",
        "name": "文化和旅游部政策法规库",
        "url": "https://zwgk.mct.gov.cn/zcfgk/",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "文化创意", "数字文化"],
        "list_selector": ".zcfg-list li a, .list li a, table td a",
        "title_selector": "h1, .article-title",
        "content_selector": ".article-content, .TRS_Editor, #zoom",
        "encoding": "utf-8",
    },

    # ===== 省级 =====
    {
        "city": "广东省", "department": "科创科委口",
        "name": "广东省工业和信息化厅",
        "url": "https://gdii.gd.gov.cn/zcgh3227/index.html",
        "keywords": ["元宇宙", "VR", "XR", "数字文娱", "人工智能", "沉浸式", "补贴"],
        "list_selector": ".list-box li a, .article-list li a, ul.list li a",
        "title_selector": "h1, .article-title",
        "content_selector": ".article-content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "广东省", "department": "文旅局口",
        "name": "广东省文化和旅游厅",
        "url": "https://whly.gd.gov.cn/open_new_newzfwj/index.html",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "文化创意", "补贴"],
        "list_selector": ".list li a, .news-list li a, table td a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "江苏省", "department": "文旅局口",
        "name": "江苏省文化和旅游厅（文化市场）",
        "url": "http://wlt.jiangsu.gov.cn/col/col91900/index.html",
        # 关键词放宽：省级政策标题少用VR，用更通用词触发
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "数字文化", "补贴",
                     "文化产业", "扶持", "资助", "数字创意", "游戏", "互联网"],
        # 江苏文旅厅页面结构：政策列表在 .field-items 或普通 li>a 里
        "list_selector": ".field-items li a, .view-content li a, .views-row a, li a",
        "title_selector": "h1, .field-name-title, .page-header",
        "content_selector": ".field-name-body, .field-items .field-item, .content, #content",
        "encoding": "utf-8",
    },
    {
        "city": "江苏省", "department": "文旅局口",
        "name": "江苏省文化和旅游厅（旅游工作）",
        "url": "http://wlt.jiangsu.gov.cn/col/col91903/index.html",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "数字文化", "补贴",
                     "文化产业", "扶持", "资助", "数字创意", "游戏", "互联网"],
        "list_selector": ".field-items li a, .view-content li a, .views-row a, li a",
        "title_selector": "h1, .field-name-title, .page-header",
        "content_selector": ".field-name-body, .field-items .field-item, .content, #content",
        "encoding": "utf-8",
    },
    {
        "city": "黑龙江省", "department": "文旅局口",
        "name": "黑龙江省文化和旅游厅",
        "url": "https://wlt.hlj.gov.cn/wlt/c114178/zfxxgk.shtml?tab=gfxwj",
        "keywords": ["VR", "AR", "数字文旅", "沉浸式", "冰雪", "元宇宙", "补贴", "奖励", "扶持", "新经济", "数字", "科技", "人工智能"],
        "list_selector": ".list li a, .article-list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "utf-8",
    },

    # ===== 上海市 =====
    {
        "city": "上海", "department": "科创科委口",
        "name": "上海市科委（项目申报）",
        "url": "https://stcsm.sh.gov.cn/zwgk/kyjhxm/xmsb/",
        "keywords": ["元宇宙", "VR", "XR", "沉浸式", "数字文旅", "人工智能", "补贴", "资助", "专项", "项目申报", "数字", "科技创新", "新赛道", "文创"],
        # 诊断显示链接格式：/zwgk/kyjhxm/xmsb/20260424/xxx.html，用通用a标签搜索
        "list_selector": "a[href*='/xmsb/2'], a[href*='/zcwj/2'], .list li a, .news-list li a, ul li a",
        "title_selector": "h1, .article-title, .tit",
        "content_selector": ".article-content, .TRS_Editor, #content, .con",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "科创科委口",
        "name": "上海市科委（科技政策）",
        "url": "https://stcsm.sh.gov.cn/zwgk/kjzc/zcwj/",
        "keywords": ["元宇宙", "VR", "XR", "沉浸式", "数字", "人工智能", "补贴", "专项", "扶持", "资助", "科技", "文创", "新赛道", "数字文娱"],
        "list_selector": "a[href*='/zcwj/2'], a[href*='/kjzc/2'], .list li a, ul li a",
        "title_selector": "h1, .article-title, .tit",
        "content_selector": ".article-content, .TRS_Editor, #content, .con",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "商委口",
        "name": "上海市商务委（国家文件）",
        "url": "https://sww.sh.gov.cn/gjqtzc/index.html",
        "keywords": ["元宇宙", "文创", "服务消费", "沉浸式", "VR", "数字文化", "补贴"],
        "list_selector": ".list li a, .news-list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".TRS_Editor, .article-content, #content",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "商委口",
        "name": "上海市商务委（本市文件）",
        "url": "https://sww.sh.gov.cn/dwmygl/index.html",
        "keywords": ["元宇宙", "文创", "服务消费", "沉浸式", "VR", "补贴", "申报"],
        "list_selector": ".list li a, .news-list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".TRS_Editor, .article-content",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "文旅局口",
        "name": "上海市文旅局（沪文旅发）",
        "url": "https://whlyj.sh.gov.cn/zw-hwlf/index.html",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "文创", "数字文娱", "补贴"],
        "list_selector": ".list li a, .article-list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".article-content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "文旅局口",
        "name": "上海市文旅局（近期信息公开）",
        "url": "https://whlyj.sh.gov.cn/jqxxgk/index.html",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "文创", "补贴", "申报"],
        "list_selector": ".list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".article-content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "科创科委口",
        "name": "上海市经信委（政策法规）",
        "url": "https://www.sheitc.sh.gov.cn/zcfg/",
        "keywords": ["元宇宙", "VR", "XR", "数字文娱", "沉浸式", "人工智能", "补贴"],
        "list_selector": ".list li a, .zcfg-list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".article-content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "上海", "department": "科创科委口",
        "name": "上海市经信委（最新公开信息）",
        "url": "https://sheitc.sh.gov.cn/zxgkxx/index.html",
        "keywords": ["元宇宙", "VR", "XR", "数字", "沉浸式", "补贴", "申报"],
        "list_selector": ".list li a, ul li a",
        "title_selector": "h1",
        "content_selector": ".article-content, .TRS_Editor",
        "encoding": "utf-8",
    },

    # ===== 深圳市 =====
    {
        "city": "深圳", "department": "商委口",
        "name": "深圳市政府（政策法规）",
        "url": "https://www.sz.gov.cn/cn/xxgk/zfxxgj/zcfg/",
        "keywords": ["元宇宙", "VR", "数字文娱", "文化产业", "沉浸式", "人工智能", "补贴"],
        "list_selector": ".list-box li a, .news-list li a, ul li a, table td a",
        "title_selector": "h1, .article-title",
        "content_selector": ".article-content, .TRS_Editor, #content",
        "encoding": "utf-8",
    },
    {
        "city": "深圳", "department": "商委口",
        "name": "深圳市商务局（通知公告）",
        "url": "https://commerce.sz.gov.cn/xxgk/qt/tzgg_1/",
        "keywords": ["元宇宙", "VR", "文创", "数字文娱", "展会", "补贴", "申报"],
        "list_selector": ".list li a, ul li a, .news-list li a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "utf-8",
    },
    {
        "city": "深圳", "department": "科创科委口",
        "name": "深圳市工信局（通知公告）",
        "url": "http://gxj.sz.gov.cn/xxgk/xxgkml/qt/tzgg/",
        "keywords": ["元宇宙", "VR", "XR", "数字文娱", "人工智能", "沉浸式", "补贴"],
        "list_selector": ".list li a, ul li a, .news-list li a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "gbk",
    },
    {
        "city": "深圳", "department": "文旅口",
        "name": "深圳市文广旅体局",
        "url": "https://wtl.sz.gov.cn/xxgk/zcfgjzcjd/whscsp/index.html",
        "keywords": ["元宇宙", "VR", "数字文旅", "文化产业", "沉浸式", "数字创意", "补贴"],
        "list_selector": ".list li a, ul li a, .article-list li a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "utf-8",
    },

    # ===== 南京市 =====
    {
        "city": "南京", "department": "商委口",
        "name": "南京市商务局",
        "url": "https://swj.nanjing.gov.cn/njsswj/?id=xxgk_224",
        "keywords": ["元宇宙", "VR", "首发", "文创", "数字", "沉浸式", "补贴"],
        "list_selector": ".list li a, ul li a, .news-list li a, table td a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor, #content",
        "encoding": "utf-8",
    },
    {
        "city": "南京", "department": "文旅局口",
        "name": "南京市文化和旅游局",
        "url": "https://wlj.nanjing.gov.cn/njswhgdxwcbj/?id=xxgk_224",
        "keywords": ["元宇宙", "VR", "数字文旅", "沉浸式", "AR", "数字技术", "补贴"],
        "list_selector": ".list li a, ul li a, .news-list li a",
        "title_selector": "h1",
        "content_selector": ".content, .TRS_Editor",
        "encoding": "utf-8",
    },

    # ===== 哈尔滨市 =====
    {
        "city": "哈尔滨", "department": "文旅局口",
        "name": "哈尔滨市政府（政策文件）",
        "url": "https://www.harbin.gov.cn/haerbin/c104531/zfxxgk_zc.shtml",
        "keywords": ["VR", "AR", "冰雪", "沉浸式", "数字", "元宇宙", "补贴", "奖励"],
        "list_selector": ".list li a, ul li a, .news-list li a, table td a, .zfxxgk-list li a",
        "title_selector": "h1, .article-title, .con_title",
        "content_selector": ".TRS_Editor, .article-content, #content, .con_content",
        "encoding": "gbk",  # 诊断显示乱码，改为GBK
    },
]

# ============================================================
# 工具函数
# ============================================================

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def content_hash(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def contains_keywords(text, keywords):
    return any(kw in text for kw in keywords)

def get_base_url(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

# ============================================================
# 爬虫核心
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}

def fetch_page(url, encoding=None, timeout=20):
    """请求页面，自动处理SSL问题"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            verify=True, allow_redirects=True)
        resp.encoding = encoding or resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.exceptions.SSLError:
        try:
            import urllib3
            urllib3.disable_warnings()
            resp = requests.get(url, headers=HEADERS, timeout=timeout,
                                verify=False, allow_redirects=True)
            resp.encoding = encoding or resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            log(f"  ❌ SSL降级也失败: {str(e)[:80]}")
            return None
    except Exception as e:
        log(f"  ❌ 请求失败: {type(e).__name__}: {str(e)[:80]}")
        return None

def get_article_links(site):
    """从列表页获取文章链接，多选择器+兜底策略"""
    html = fetch_page(site["url"], encoding=site.get("encoding"))
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    base = get_base_url(site["url"])
    links = []
    seen = set()

    # 尝试配置的选择器
    for sel in site["list_selector"].split(","):
        for a in soup.select(sel.strip()):
            href = a.get("href", "").strip()
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 5 or href in seen:
                continue
            seen.add(href)
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = base + href
            else:
                full_url = urljoin(site["url"], href)
            links.append({"url": full_url, "title": title})

    # 兜底：如果选择器没有结果，搜索全页面同域名链接
    if not links:
        log(f"  ⚠️  选择器无结果，尝试全页搜索...")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            title = a.get_text(strip=True)
            if len(title) < 8 or len(title) > 80:
                continue
            if any(s in href for s in ["javascript", "mailto", "#"]):
                continue
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = base + href
            else:
                full_url = urljoin(site["url"], href)
            if urlparse(full_url).netloc == urlparse(site["url"]).netloc:
                if full_url not in seen:
                    seen.add(full_url)
                    links.append({"url": full_url, "title": title})

    return links[:30]

def parse_article(url, site):
    """解析单篇文章，提取标题和正文"""
    html = fetch_page(url, encoding=site.get("encoding"))
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 提取标题
    title = ""
    for sel in site["title_selector"].split(","):
        el = soup.select_one(sel.strip())
        if el:
            title = el.get_text(strip=True)
            break
    if not title:
        # 尝试从正文第一个有意义的段落提取标题
        for sel in [".con-title", "#title", ".news-title", "h2", "h3"]:
            el = soup.select_one(sel)
            if el and 8 < len(el.get_text(strip=True)) < 100:
                title = el.get_text(strip=True)
                break
    if not title:
        # 最后从页面title标签提取，但去掉网站名后缀（如「-深圳市文化广电旅游体育局网站」）
        tag = soup.find("title")
        if tag:
            raw_title = tag.get_text(strip=True)
            # 去掉常见的网站名后缀
            for suffix in ["-深圳市文化广电旅游体育局网站", "-上海市文化和旅游局",
                           "-上海市科学技术委员会", "-广东省工业和信息化厅",
                           " - 工业和信息化部", "- 文化和旅游部", "_"]:
                if suffix in raw_title:
                    raw_title = raw_title.split(suffix)[0].strip()
            title = raw_title

    # 提取正文
    content = ""
    for sel in site["content_selector"].split(","):
        el = soup.select_one(sel.strip())
        if el and len(el.get_text(strip=True)) > 100:
            content = el.get_text(separator="\n", strip=True)
            break

    if not title or not content or len(content) < 100:
        return None

    content = re.sub(r'\n{3,}', '\n\n', content)
    return {"title": title, "content": content, "url": url}

def structure_policy(raw, site):
    """生成知识库格式的chunk"""
    # 尝试从正文提取申报截止时间
    deadline = "待人工确认"
    for pattern in [
        r'申报时间[：:]\s*([^\n]{5,30})',
        r'截止时间[：:]\s*([^\n]{5,30})',
        r'(\d{4}年\d{1,2}月\d{1,2}日).*?截止',
    ]:
        m = re.search(pattern, raw["content"])
        if m:
            deadline = m.group(1).strip()
            break

    return "\n".join([
        f"【政策名称】{raw['title']}",
        f"【城市/区域】{site['city']} · 待确认区域",
        f"【归口部门】{site['department']}",
        f"【补贴类型】待人工确认",
        f"【补贴金额/力度】待人工确认",
        f"【政策内容】{raw['content'][:600]}{'...(已截断)' if len(raw['content']) > 600 else ''}",
        f"【申报条件】待人工确认",
        f"【与全感VR匹配度】待人工审核",
        f"【匹配说明】待人工审核",
        f"【申报入口】{raw['url']}",
        f"【申报截止时间】{deadline}",
        f"【政策有效期】待人工确认",
        f"【标签】{','.join(site['keywords'][:4])}",
        f"【数据来源】{site['name']}",
        f"【爬取时间】{datetime.now().strftime('%Y-%m-%d')}",
        f"【状态】⚠️ 待人工审核，核实后再入正式知识库",
    ])

# ============================================================
# Dify API
# ============================================================

def dify_headers():
    return {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }

def add_document_to_dify(title, content):
    """向知识库添加新文档（Dify Cloud 最新API）"""
    url = f"{DIFY_BASE_URL}/datasets/{DIFY_DATASET_ID}/document/create-by-text"
    payload = {
        "name": title[:100],
        "text": content,
        "indexing_technique": "high_quality",
        "doc_form": "text_model",
        "doc_language": "Chinese",
    }
    try:
        resp = requests.post(url, headers=dify_headers(), json=payload, timeout=30)
        if resp.status_code == 200:
            doc_id = resp.json().get("document", {}).get("id", "unknown")
            log(f"  ✅ 上传成功，文档ID: {doc_id}")
            return True
        elif resp.status_code == 403:
            log(f"  ⏳ 触发频率限制，等待30秒后重试...")
            time.sleep(30)
            # 重试一次
            try:
                resp2 = requests.post(url, headers=dify_headers(), json=payload, timeout=30)
                if resp2.status_code == 200:
                    log(f"  ✅ 重试成功")
                    return True
                log(f"  ❌ 重试失败: {resp2.status_code} {resp2.text[:100]}")
                return False
            except Exception as e2:
                log(f"  ❌ 重试异常: {e2}")
                return False
        log(f"  ❌ 上传失败: {resp.status_code} {resp.text[:150]}")
        return False
    except Exception as e:
        log(f"  ❌ 上传异常: {e}")
        return False

# ============================================================
# 本地保存
# ============================================================

def save_for_review(new_policies):
    if not new_policies:
        return None
    fname = f"待审核_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"# X-META 补贴政策爬取结果\n")
        f.write(f"# 爬取时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# 共 {len(new_policies)} 条新政策待审核\n")
        f.write(f"# 说明：填写所有「待人工确认」字段后，运行上传命令\n\n")
        for i, p in enumerate(new_policies, 1):
            f.write(f"{'='*60}\n# 第{i}条 | {p['city']} | 来源：{p['site']}\n{'='*60}\n")
            f.write(p["chunk"])
            f.write("\n\n")
    log(f"📄 已保存：{fname}")
    return fname


def interactive_review_and_upload(new_policies):
    """
    交互式审核+上传：逐条展示摘要，用户快速确认后直接上传到Dify。
    无需手动运行 upload 命令，审核完即上传。

    操作说明：
      y / Enter = 上传这条
      n         = 跳过这条（不上传，但不影响缓存）
      s         = 标记为不相关（跳过且写入黑名单，下次不再出现）
      q         = 退出交互，剩余条目保存到文件待后续处理
    """
    if not new_policies:
        return

    print("\n" + "="*60)
    print("📋 交互式审核模式")
    print("   y/Enter=上传  n=跳过  s=标记不相关  q=退出")
    print("="*60)

    uploaded = 0
    skipped = 0
    blacklist_file = "url_blacklist.txt"

    # 加载黑名单
    blacklist = set()
    if os.path.exists(blacklist_file):
        with open(blacklist_file, "r") as f:
            blacklist = set(f.read().splitlines())

    remaining = []  # 没处理完的留到文件

    for i, p in enumerate(new_policies, 1):
        # 提取摘要信息
        chunk = p["chunk"]
        lines = {l.split("】")[0].lstrip("【"): l.split("】")[1].strip()
                 for l in chunk.split("\n") if "】" in l}

        print(f"\n[{i}/{len(new_policies)}] {p['city']} | {p['site']}")
        print(f"  📌 {lines.get('政策名称', p['title'])[:60]}")
        print(f"  💰 {lines.get('补贴金额/力度', '?')} | {lines.get('补贴类型', '?')}")
        print(f"  🎯 匹配度：{lines.get('与全感VR匹配度', '?')} — {lines.get('匹配说明', '')[:50]}")
        if lines.get('flag', '').strip():
            print(f"  ⚠️  {lines.get('flag', '')[:60]}")
        print(f"  🔗 {p['url'][:70]}")

        try:
            choice = input("  操作 (y/n/s/q): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            choice = "q"

        if choice in ("y", ""):
            title = lines.get("政策名称", p["title"])[:80]
            if add_document_to_dify(title=title, content=chunk):
                uploaded += 1
                print(f"  ✅ 已上传")
            else:
                print(f"  ❌ 上传失败，已保存到文件")
                remaining.append(p)
            time.sleep(5)  # 免费版限流，5秒间隔
        elif choice == "s":
            with open(blacklist_file, "a") as f:
                f.write(p["url"] + "\n")
            blacklist.add(p["url"])
            skipped += 1
            print(f"  🚫 已标记为不相关")
        elif choice == "q":
            remaining.extend(new_policies[i-1:])
            print(f"  ⏸  退出交互，剩余 {len(remaining)} 条保存到文件")
            break
        else:  # n
            remaining.append(p)
            skipped += 1
            print(f"  ⏭  跳过")

    print(f"\n{'='*60}")
    print(f"📊 审核完成：上传 {uploaded} 条 | 跳过 {skipped} 条 | 待处理 {len(remaining)} 条")

    if remaining:
        fname = save_for_review(remaining)
        print(f"📄 未处理的条目已保存到：{fname}")
        print(f"   后续可运行：python crawler.py upload {fname}")

    return uploaded


# ============================================================
# 方案B：必应搜索引擎模块
# ============================================================

def serper_search(query, count=10):
    """调用 Serper API（谷歌搜索），返回结果列表"""
    if not SERPER_ENABLED or not SERPER_API_KEY or SERPER_API_KEY == "你的SerperAPIKey填这里":
        return []
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "num": count,
        "gl": "cn",    # 地区：中国
        "hl": "zh-cn", # 语言：中文
    }
    try:
        resp = requests.post(SERPER_SEARCH_URL, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for item in data.get("organic", []):
                results.append({
                    "url": item.get("link", ""),
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results
        log(f"  ❌ Serper API错误: {resp.status_code} {resp.text[:100]}")
        return []
    except Exception as e:
        log(f"  ❌ Serper API请求失败: {e}")
        return []

def run_search_engine(cache):
    """方案B主流程：遍历搜索关键词矩阵，发现新政策URL"""
    if not SERPER_ENABLED:
        log("⚠️  Serper API未启用，跳过搜索引擎模块")
        log("   → 启用方式：填入 SERPER_API_KEY，将 SERPER_ENABLED 改为 True")
        return []

    log(f"\n🔍 方案B：搜索引擎模块启动（共{len(SEARCH_QUERIES)}个查询）")
    new_policies = []
    seen_urls = set()  # 跨query去重，防止同一URL被多个查询重复处理
    all_keywords = ["元宇宙", "VR", "XR", "沉浸式", "数字文旅", "数字创意",
                    "补贴", "资助", "扶持", "奖励", "申报", "文化产业", "人工智能"]

    for i, sq in enumerate(SEARCH_QUERIES, 1):
        log(f"  [{i}/{len(SEARCH_QUERIES)}] 搜索：{sq['query'][:50]}...")
        results = serper_search(sq["query"])
        time.sleep(0.5)   # 礼貌性延迟

        for r in results:
            url = r["url"]
            title = r["title"]

            if url in cache:
                continue
            if url in seen_urls:  # 跨query去重
                continue
            seen_urls.add(url)
            # 只处理政府网站
            if not any(d in url for d in [".gov.cn", ".gov.com", "gov.cn"]):
                cache[url] = "non_gov"
                continue
            # 关键词过滤（标题+摘要）
            combined = title + " " + r.get("snippet", "")
            if not contains_keywords(combined, all_keywords):
                cache[url] = "filtered"
                continue

            log(f"    ✨ 发现：{title[:50]}")
            time.sleep(1.5)

            # 构造一个伪site对象给parse_article用
            pseudo_site = {
                "city": sq["city"],
                "department": sq["dept"],
                "name": f"搜索发现（{sq['city']}）",
                "keywords": all_keywords,
                "title_selector": "h1, .article-title, .con-title, .tit",
                "content_selector": ".article-content, .TRS_Editor, #content, .content, .con",
                "encoding": None,
            }
            article = parse_article(url, pseudo_site)
            if not article:
                cache[url] = "parse_failed"
                continue

            chash = content_hash(article["content"][:500])
            if chash in cache.values():
                log(f"    ⏭️  内容重复，跳过")
                cache[url] = f"dup_{chash}"
                continue

            chunk = structure_policy_with_llm(article, pseudo_site)
            if chunk is None:
                cache[url] = "filtered_by_llm"
                continue
            new_policies.append({
                "title": article["title"],
                "url": url,
                "chunk": chunk,
                "site": f"Serper搜索（{sq['city']}）",
                "city": sq["city"],
                "source": "search_engine",
            })
            cache[url] = chash
            save_cache(cache)

    log(f"  🔍 搜索引擎模块完成，新发现 {len(new_policies)} 条")
    return new_policies


# ============================================================
# LLM自动填字段模块
# ============================================================

# ---- 第一层：过滤Prompt ----
LLM_FILTER_PROMPT = """判断以下政策文本是否应收录到企业补贴申报知识库。

【收录标准】必须同时满足：
1. 是企业/机构可主动申请的补贴、资助、奖励、减免类政策
   排除：政府内部规划、纯行动方案（无具体申报入口）、新闻报道、人员任命
   注意：「若干措施」「实施方案」如包含具体补贴条款也可收录
2. 申报主体包含企业
3. 与以下领域相关：元宇宙/VR/XR/沉浸式体验/数字文旅/数字创意/游戏/文化创意/人工智能应用

政策标题：{title}
政策内容（前500字）：{content}

只输出JSON：{{"include": true或false, "reason": "一句话原因", "policy_type": "补贴资金/荣誉资源/租金减免/研发资助/综合措施/不相关"}}"""

# ---- 第二层：填字段Prompt ----
LLM_FILL_PROMPT = """你是政府补贴政策分析专家。

【X-META全感VR项目背景】
- 产品：线下全感VR沉浸式游戏体验空间（场馆），可加盟连锁
- 技术：VR/XR硬件+软件，沉浸式互动，可结合AI场景生成
- 行业标签：元宇宙、数字文旅、数字创意、沉浸式体验、文化娱乐、新型业态
- 申报主体：各城市加盟商（在当地注册的企业）或X-META总部
- 重要：我们是线下实体场馆，不是纯软件公司，也不是影视/短剧制作方

【匹配度判断标准】
高度匹配：明确支持VR/XR/沉浸式体验/元宇宙场景，申报条件基本能满足
条件匹配：政策相关但需满足特定条件（注册地/营收规模/团队人数/专利要求）
参考价值：间接相关，申报门槛较高，或属于政策框架文件需关注后续通知

【填写规则】
- 补贴金额不确定时写「按项目评估」，不要猜数字
- 政策要求「注册在XX区」必须在conditions里明确指出
- 政策要求「软件著作权/专利」必须在flag里标注「需确认是否持有」
- 三年规划/行动方案类：relevance最高填「条件匹配」，flag注明「框架文件，需关注后续申报通知」
- policy_name去掉「政策咨询」「来源：XX」等无关后缀

政策标题：{title}
政策全文：{content}

只输出JSON，不要解释或Markdown：
{{"policy_name": "政策正式名称", "city": "城市或省份", "district": "具体区域（不确定填市级）", "department": "归口部门（商委口/科创科委口/文旅局口/区级综合口）", "subsidy_type": "补贴类型", "amount": "补贴金额或力度", "conditions": "核心申报条件2~3条，用；分隔", "relevance": "高度匹配/条件匹配/参考价值", "relevance_reason": "具体说明为何匹配或需要什么额外条件", "deadline": "申报截止时间", "valid_period": "政策有效期", "tags": "3~5个标签逗号分隔", "flag": "需人工特别关注的点，没有则填空字符串"}}"""

def call_llm(prompt, max_tokens=600):
    """底层LLM调用"""
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": max_tokens},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = re.sub(r"```[a-zA-Z]*", "", text).strip().rstrip("`").strip()
            return text
        log(f"  ⚠️  LLM API错误: {resp.status_code} {resp.text[:80]}")
        return None
    except Exception as e:
        log(f"  ⚠️  LLM调用失败: {e}")
        return None

def llm_filter(title, content_text):
    """第一层：快速过滤，判断是否值得收录"""
    if not LLM_ENABLED or not LLM_API_KEY or LLM_API_KEY == "你的LLM_APIKey填这里":
        return True, "LLM未启用"
    prompt = LLM_FILTER_PROMPT.format(title=title, content=content_text[:500])
    text = call_llm(prompt, max_tokens=150)
    if not text:
        return True, "LLM调用失败，默认收录"
    try:
        data = json.loads(text)
        return data.get("include", True), data.get("reason", "")
    except json.JSONDecodeError:
        return True, "解析失败，默认收录"

def llm_fill_fields(content_text, title):
    """第二层：填写结构化字段"""
    if not LLM_ENABLED or not LLM_API_KEY or LLM_API_KEY == "你的LLM_APIKey填这里":
        return None
    prompt = LLM_FILL_PROMPT.format(title=title, content=content_text[:2500])
    text = call_llm(prompt, max_tokens=800)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log(f"  ⚠️  填字段JSON解析失败，退回规则提取")
        return None

def structure_policy_with_llm(raw, site):
    """生成知识库格式chunk，两层LLM处理：先过滤再填字段"""
    # 第一层：过滤判断
    if LLM_ENABLED:
        should_include, filter_reason = llm_filter(raw["title"], raw["content"])
        if not should_include:
            log(f"  🚫 LLM过滤：{filter_reason}")
            return None  # 返回None表示这条不要
        log(f"  ✅ 通过过滤：{filter_reason[:40]}")

    # 第二层：填字段
    llm_data = None
    if LLM_ENABLED:
        log(f"  🤖 LLM填字段中...")
        llm_data = llm_fill_fields(raw["content"], raw["title"])

    if llm_data:
        # LLM填好了，直接用
        log(f"  ✅ LLM自动填字段成功（匹配度：{llm_data.get('relevance','?')}）")
        flag = llm_data.get('flag', '').strip()
        chunk = "\n".join([
            f"【政策名称】{llm_data.get('policy_name', raw['title'])}",
            f"【城市/区域】{llm_data.get('city', site['city'])} · {llm_data.get('district','待确认')}",
            f"【归口部门】{llm_data.get('department', site['department'])}",
            f"【补贴类型】{llm_data.get('subsidy_type','待确认')}",
            f"【补贴金额/力度】{llm_data.get('amount','待确认')}",
            f"【政策内容】{raw['content'][:600]}{'...(已截断)' if len(raw['content']) > 600 else ''}",
            f"【申报条件】{llm_data.get('conditions','待确认')}",
            f"【与全感VR匹配度】{llm_data.get('relevance','待确认')}",
            f"【匹配说明】{llm_data.get('relevance_reason','')}",
            f"【申报入口】{raw['url']}",
            f"【申报截止时间】{llm_data.get('deadline','待确认')}",
            f"【政策有效期】{llm_data.get('valid_period','待确认')}",
            f"【标签】{llm_data.get('tags', ','.join(site.get('keywords',[][:4])))}",
            f"【数据来源】{site['name']}",
            f"【爬取时间】{datetime.now().strftime('%Y-%m-%d')}",
            f"【状态】✅ LLM自动审核通过{(' | ⚠️ 注意：' + flag) if flag else ''}",
        ])
        return chunk
    else:
        # LLM不可用，退回规则提取
        return structure_policy(raw, site)


# ============================================================
# 主流程
# ============================================================

def run_crawler(auto_upload=False, mode="both", interactive=False):
    """
    mode: "both"=双引擎, "crawl"=仅直接爬取, "search"=仅搜索引擎
    """
    log("=" * 55)
    log(f"🚀 X-META 补贴政策爬虫 v3.0 | 双引擎模式")
    log(f"   方案A（直接爬取）: {len(TARGET_SITES)} 个来源")
    log(f"   方案B（搜索引擎）: {len(SEARCH_QUERIES)} 个查询 | {'已启用 (Serper)' if SERPER_ENABLED else '未启用（填入SERPER_API_KEY后生效）'}")
    log(f"   LLM自动填字段: {'已启用 (' + LLM_MODEL + ')' if LLM_ENABLED else '未启用（填入LLM_API_KEY后生效）'}")
    log(f"   模式：{'自动上传' if auto_upload else '本地审核（推荐）'}")
    log("=" * 55)

    cache = load_cache()
    new_policies = []
    total_checked = 0

    # ---- 方案A：直接爬取 ----
    if mode in ("both", "crawl"):
        log(f"\n{'─'*55}")
        log(f"📡 方案A：直接爬取模块（{len(TARGET_SITES)}个来源）")
        log(f"{'─'*55}")

        for site in TARGET_SITES:
            log(f"\n📡 [{site['city']}] {site['name']}")
            links = get_article_links(site)
            log(f"   发现链接：{len(links)} 条")

            site_new = 0
            for link in links:
                total_checked += 1
                url = link["url"]

                if url in cache:
                    continue
                if not contains_keywords(link["title"], site["keywords"]):
                    cache[url] = "filtered"
                    continue

                log(f"   🔍 {link['title'][:45]}...")
                time.sleep(1.5)

                article = parse_article(url, site)
                if not article:
                    cache[url] = "parse_failed"
                    continue
                if not contains_keywords(article["content"], site["keywords"]):
                    cache[url] = "filtered_content"
                    continue

                chash = content_hash(article["content"][:500])
                if chash in cache.values():
                    log(f"   ⏭️  内容重复，跳过")
                    cache[url] = f"dup_{chash}"
                    continue

                chunk = structure_policy_with_llm(article, site)
                if chunk is None:
                    cache[url] = "filtered_by_llm"
                    continue
                new_policies.append({
                    "title": article["title"],
                    "url": url,
                    "chunk": chunk,
                    "site": site["name"],
                    "city": site["city"],
                    "source": "direct_crawl",
                })
                cache[url] = chash
                site_new += 1
                log(f"   ✨ 新政策：{article['title'][:50]}")

            save_cache(cache)

    # ---- 方案B：搜索引擎 ----
    if mode in ("both", "search"):
        search_policies = run_search_engine(cache)
        new_policies.extend(search_policies)
        save_cache(cache)

    log(f"\n{'='*55}")
    log(f"📊 完成：检查 {total_checked} 条链接，发现新政策 {len(new_policies)} 条")
    log(f"   方案A贡献：{sum(1 for p in new_policies if p.get('source')=='direct_crawl')} 条")
    log(f"   方案B贡献：{sum(1 for p in new_policies if p.get('source')=='search_engine')} 条")

    if not new_policies:
        log("✅ 无新政策，知识库无需更新")
        return

    if AUTO_MODE and LLM_ENABLED:
        # 全自动模式：LLM已过滤+填字段，直接上传，无需人工确认
        log(f"\n⬆️  全自动模式：直接上传到 Dify（共{len(new_policies)}条）...")
        success = 0
        failed = []
        for p in new_policies:
            title = p["chunk"].split("\n")[0].replace("【政策名称】", "").strip()[:80]
            if add_document_to_dify(title=title, content=p["chunk"]):
                success += 1
                log(f"  ✅ [{success}/{len(new_policies)}] {title[:50]}")
            else:
                failed.append(p)
                log(f"  ❌ 上传失败：{title[:50]}")
            time.sleep(1.5)
        log(f"\n✅ 上传完成：{success}/{len(new_policies)} 条成功")
        if failed:
            fname = save_for_review(failed)
            log(f"⚠️  {len(failed)} 条上传失败，已保存到 {fname}，可稍后重试：")
            log(f"   python crawler.py upload {fname}")
    elif interactive:
        # 交互模式（AUTO_MODE=False时使用）
        log(f"\n📋 进入交互审核模式，共 {len(new_policies)} 条...")
        interactive_review_and_upload(new_policies)
    else:
        # 纯保存文件模式
        review_file = save_for_review(new_policies)
        log(f"\n📋 下一步：")
        log(f"   1. 打开 {review_file}，确认字段内容")
        log(f"   2. python crawler.py upload {review_file}")

def upload_reviewed_file(filepath):
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在：{filepath}")
        return
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.split("="*60) if "【政策名称】" in b]
    log(f"📤 准备上传 {len(blocks)} 条已审核政策...")

    success = 0
    for i, block in enumerate(blocks, 1):
        title = "政策"
        for line in block.split("\n"):
            if line.startswith("【政策名称】"):
                title = line.replace("【政策名称】", "").strip()
                break
        log(f"  [{i}/{len(blocks)}] {title[:45]}")
        if add_document_to_dify(title=title, content=block):
            success += 1
        time.sleep(5)  # 免费版限流，5秒间隔

    log(f"✅ 完成：{success}/{len(blocks)} 条成功")

# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    # 解析参数
    interactive_mode = "-i" in args or "--interactive" in args
    args = [a for a in args if a not in ("-i", "--interactive")]

    if args and args[0] == "upload":
        # 上传已审核文件
        if len(args) > 1:
            upload_reviewed_file(args[1])
        else:
            print("用法：python crawler.py upload <待审核文件名>")
    elif args and args[0] == "search":
        run_crawler(auto_upload=False, mode="search", interactive=interactive_mode)
    elif args and args[0] == "crawl":
        run_crawler(auto_upload=False, mode="crawl", interactive=interactive_mode)
    else:
        # 默认：双引擎全跑
        # 加 -i 参数进入交互审核模式（边审核边上传）
        run_crawler(auto_upload=False, mode="both", interactive=interactive_mode)

    # 使用说明：
    # python crawler.py          → 全自动模式（LLM审核→直接上传）✅ 推荐
    # python crawler.py -i       → 交互模式（逐条确认后上传，AUTO_MODE需为False）
    # python crawler.py upload 待审核_xxx.txt  → 上传已有文件
    # python crawler.py search   → 仅搜索引擎
    # python crawler.py crawl    → 仅直接爬取
    # 切换回手动审核：把 AUTO_MODE 改为 False
