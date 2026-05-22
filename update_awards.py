"""
X-META 奖项季度自动更新脚本 v1.0
每季度1日（1/4/7/10月）由 GitHub Actions 触发

两阶段：
  阶段一：核查已有27个奖项官网，更新 deadline / certainty / deadline_estimated
  阶段二：Serper 搜索发现新奖项，生成待审核文件（不自动写入）

使用方法：
  python update_awards.py          # 完整运行（两阶段）
  python update_awards.py check    # 仅核查已有奖项
  python update_awards.py discover # 仅发现新奖项
"""

import json
import os
import re
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================
SERPER_API_KEY  = os.environ.get("SERPER_API_KEY", "")
LLM_API_KEY     = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL    = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL       = "qwen-plus-2025-07-28"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
AWARDS_FILE     = "awards.json"
LOG_FILE        = "update_awards_log.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 新奖项发现用的搜索词
DISCOVER_QUERIES = [
    {"query": "VR XR 沉浸式 奖项 申报 2026", "category": "综合"},
    {"query": "元宇宙 虚拟现实 大赛 奖项 报名 2026", "category": "综合"},
    {"query": "数字文旅 沉浸式体验 奖项 评选 2026", "category": "内容创作"},
    {"query": "XR VR 国际电影节 沉浸式 竞赛单元 2026", "category": "内容创作"},
    {"query": "元宇宙 数字创意 企业 榜单 评选 2026", "category": "企业榜单"},
    {"query": "LBE 大空间 VR 游戏 奖项 行业评选 2026", "category": "商业内容"},
    {"query": "虚拟现实 XR 国际奖项 award 2026 申报", "category": "综合"},
]

# ============================================================
# 工具函数
# ============================================================
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def fetch_page(url, timeout=20):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            verify=True, allow_redirects=True)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.exceptions.SSLError:
        try:
            import urllib3; urllib3.disable_warnings()
            resp = requests.get(url, headers=HEADERS, timeout=timeout,
                                verify=False, allow_redirects=True)
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            log(f"  ❌ SSL降级失败: {str(e)[:60]}")
            return None
    except Exception as e:
        log(f"  ❌ 请求失败: {str(e)[:60]}")
        return None

def call_llm(prompt, max_tokens=800):
    if not LLM_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": LLM_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": max_tokens},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = re.sub(r"```[a-zA-Z]*", "", text).strip().rstrip("`").strip()
            return text
        log(f"  ⚠️  LLM错误: {resp.status_code}")
        return None
    except Exception as e:
        log(f"  ⚠️  LLM调用失败: {e}")
        return None

def serper_search(query, count=8):
    if not SERPER_API_KEY:
        return []
    try:
        resp = requests.post(
            SERPER_SEARCH_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": count, "gl": "cn", "hl": "zh-cn"},
            timeout=15,
        )
        if resp.status_code == 200:
            return [{"url": r.get("link",""), "title": r.get("title",""),
                     "snippet": r.get("snippet","")}
                    for r in resp.json().get("organic", [])]
        return []
    except Exception as e:
        log(f"  ❌ Serper失败: {e}")
        return []

# ============================================================
# 阶段一：核查已有奖项
# ============================================================

CHECK_PROMPT = """你是奖项信息核查专家。

已知奖项基本信息：
名称：{name}
主办方：{organizer}
历史截止日期参考：{deadline}
官网：{url}

以下是从官网抓取的页面内容（前2000字）：
{content}

请判断：
1. 2026年该奖项是否有明确的报名通知？
2. 如果有，截止日期是什么？
3. certainty应该是什么（confirmed=有明确2026年通知 / predicted=仅有历史信息无2026通知 / draft=征求意见阶段）？

只输出JSON，不要解释：
{{"has_2026_notice": true或false, "deadline_2026": "YYYY-MM-DD或空字符串", "certainty": "confirmed/predicted/draft", "evidence": "一句话说明判断依据"}}"""

def check_single_award(award):
    """核查单个奖项，返回更新后的字段dict或None（无变化）"""
    name = award['name']
    url  = award.get('url', '')

    if not url:
        log(f"  ⏭  {name[:30]} — 无URL，跳过")
        return None

    log(f"  🔍 核查: {name[:40]}")
    html = fetch_page(url)
    if not html:
        log(f"  ❌ 页面获取失败")
        return None

    # 提取正文
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)[:2000]

    prompt = CHECK_PROMPT.format(
        name=name,
        organizer=award.get('organizer', ''),
        deadline=award.get('deadline', ''),
        url=url,
        content=text,
    )
    result_text = call_llm(prompt)
    if not result_text:
        log(f"  ⚠️  LLM无响应，保持原值")
        return None

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        log(f"  ⚠️  LLM返回解析失败")
        return None

    has_notice  = result.get('has_2026_notice', False)
    new_deadline = result.get('deadline_2026', '').strip()
    new_certainty = result.get('certainty', 'predicted')
    evidence    = result.get('evidence', '')

    log(f"  {'✅' if has_notice else '⚠️ '} {evidence[:60]}")

    updates = {}

    # 更新certainty
    if new_certainty != award.get('certainty', 'confirmed'):
        updates['certainty'] = new_certainty
        log(f"  📋 certainty: {award.get('certainty')} → {new_certainty}")

    # 更新deadline
    if has_notice and new_deadline and new_deadline != award.get('deadline', ''):
        updates['deadline'] = new_deadline
        updates['deadline_estimated'] = False
        updates.pop('deadline_note', None)
        log(f"  📅 deadline: {award.get('deadline')} → {new_deadline}")
    elif not has_notice:
        # 无2026通知，标记为推测
        if not award.get('deadline_estimated'):
            updates['deadline_estimated'] = True
            updates['deadline_note'] = "以下截止日期为基于历史信息推测，2026年官方通知暂未发布"
            updates['certainty'] = 'predicted'

    return updates if updates else None


def run_check(awards):
    """阶段一主流程：核查所有奖项"""
    log(f"\n{'─'*55}")
    log(f"📡 阶段一：核查已有 {len(awards)} 个奖项")
    log(f"{'─'*55}")

    changed = 0
    for i, award in enumerate(awards, 1):
        log(f"\n[{i}/{len(awards)}] {award['name'][:45]}")
        updates = check_single_award(award)
        if updates:
            award.update(updates)
            changed += 1
        time.sleep(2)  # 礼貌延迟

    log(f"\n✅ 阶段一完成：{changed}/{len(awards)} 个奖项有更新")
    return awards

# ============================================================
# 阶段二：发现新奖项
# ============================================================

DISCOVER_PROMPT = """你是VR/XR行业奖项评估专家。

【X-META全感VR项目背景】
线下全感VR沉浸式游戏体验空间（场馆），行业标签：元宇宙、数字文旅、沉浸式体验、VR/XR。

【已收录奖项列表（勿重复）】
{existing_names}

【待评估搜索结果】
标题：{title}
摘要：{snippet}
URL：{url}

判断：这是否是一个X-META可以申报的、尚未收录的VR/XR/元宇宙/沉浸式相关奖项或评选？

只输出JSON：
{{"is_award": true或false, "reason": "一句话", "name": "奖项正式名称", "organizer": "主办方", "category": "综合/内容创作/企业榜单/商业内容/技术", "region": "国内/国际", "relevance": "高/中/低"}}"""

def run_discover(awards):
    """阶段二主流程：搜索发现新奖项"""
    log(f"\n{'─'*55}")
    log(f"🔍 阶段二：搜索发现新奖项（{len(DISCOVER_QUERIES)} 个查询）")
    log(f"{'─'*55}")

    if not SERPER_API_KEY:
        log("⚠️  SERPER_API_KEY未配置，跳过新奖项发现")
        return []

    existing_names = "\n".join(f"- {a['name']}" for a in awards)
    candidates = []
    seen_urls = set(a.get('url','') for a in awards)
    seen_titles = set()

    for i, q in enumerate(DISCOVER_QUERIES, 1):
        log(f"\n  [{i}/{len(DISCOVER_QUERIES)}] {q['query'][:50]}")
        results = serper_search(q['query'])
        time.sleep(0.8)

        for r in results:
            url = r['url']
            title = r['title']

            if url in seen_urls or title in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(title)

            # 快速关键词过滤，减少LLM调用
            combined = title + r.get('snippet', '')
            kws = ['奖', '大赛', '评选', 'award', 'prize', 'festival',
                   'VR', 'XR', '元宇宙', '沉浸式', '虚拟现实']
            if not any(kw.lower() in combined.lower() for kw in kws):
                continue

            prompt = DISCOVER_PROMPT.format(
                existing_names=existing_names[:1500],
                title=title,
                snippet=r.get('snippet', ''),
                url=url,
            )
            result_text = call_llm(prompt, max_tokens=300)
            if not result_text:
                continue

            try:
                result = json.loads(result_text)
            except json.JSONDecodeError:
                continue

            if result.get('is_award') and result.get('relevance') in ('高', '中'):
                log(f"  ✨ 发现: {result.get('name','?')[:45]} [{result.get('relevance')}]")
                candidates.append({
                    "name":       result.get('name', title),
                    "organizer":  result.get('organizer', ''),
                    "region":     result.get('region', '国内'),
                    "category":   result.get('category', '综合'),
                    "relevance":  result.get('relevance', '中'),
                    "url":        url,
                    "snippet":    r.get('snippet', ''),
                    "query":      q['query'],
                })
            time.sleep(1)

    log(f"\n  🔍 阶段二完成，发现候选新奖项 {len(candidates)} 个")
    return candidates


def save_discover_report(candidates):
    """生成待审核报告，人工确认后手动加入awards.json"""
    if not candidates:
        log("ℹ️  无新奖项候选，无需生成报告")
        return

    fname = f"待审核_新奖项_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"# X-META 新奖项发现报告\n")
        f.write(f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# 共 {len(candidates)} 个候选，请人工核实后加入 awards.json\n\n")

        for i, c in enumerate(candidates, 1):
            f.write(f"{'='*55}\n")
            f.write(f"# 候选 {i} | 相关度：{c['relevance']}\n")
            f.write(f"{'='*55}\n")
            f.write(f"名称：{c['name']}\n")
            f.write(f"主办方：{c['organizer']}\n")
            f.write(f"类别：{c['category']} | 地区：{c['region']}\n")
            f.write(f"URL：{c['url']}\n")
            f.write(f"摘要：{c['snippet']}\n")
            f.write(f"发现来源：{c['query']}\n\n")
            f.write(f"# 加入awards.json时需补充字段：\n")
            f.write(f'# id / prestige / deadline / event_date / desc / prize / how_to_apply / highlight / certainty\n\n')

    log(f"📄 新奖项报告已保存：{fname}")
    return fname

# ============================================================
# 主流程
# ============================================================
def main(mode="both"):
    log("=" * 55)
    log(f"🏆 X-META 奖项季度更新脚本 v1.0")
    log(f"   模式：{mode} | 时间：{datetime.now().strftime('%Y-%m-%d')}")
    log("=" * 55)

    # 读取awards.json
    if not os.path.exists(AWARDS_FILE):
        log(f"❌ 找不到 {AWARDS_FILE}")
        return
    with open(AWARDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    awards = data.get("awards", [])
    log(f"📂 已加载 {len(awards)} 个奖项")

    # 阶段一：核查已有奖项
    if mode in ("both", "check"):
        awards = run_check(awards)
        # 回写awards.json
        data["awards"] = awards
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d")
        with open(AWARDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"\n💾 awards.json 已更新")

    # 阶段二：发现新奖项
    if mode in ("both", "discover"):
        candidates = run_discover(awards)
        save_discover_report(candidates)

    log(f"\n{'='*55}")
    log("✅ 奖项季度更新完成")

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "check":
        main("check")
    elif args and args[0] == "discover":
        main("discover")
    else:
        main("both")
