#!/usr/bin/env python3
r"""
巨潮资讯网 - A股 IPO 招股说明书批量下载（2023年至今）。
覆盖沪深主板、创业板、科创板及北交所。
"""

import re
import time
import logging
from datetime import datetime
from pathlib import Path
import requests
import pandas as pd

# ============ 配置 ============
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "pdf"
CSV_PATH = BASE_DIR / "ipo_downloaded.csv"
LOG_FILE = BASE_DIR / "ipo_crawler.log"

URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
REQUEST_TIMEOUT = 20
# ==============================

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8",
)
logger = logging.getLogger(__name__)


def download_pdf(pdf_url: str, filename: str) -> Path | None:
    out_path = OUTPUT_DIR / filename
    if out_path.exists():
        return out_path
    try:
        resp = requests.get(pdf_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        logger.info(f"    下载成功: {filename} ({out_path.stat().st_size} bytes)")
        return out_path
    except Exception as e:
        logger.warning(f"    下载失败 {filename}: {e}")
        return None


def main():
    logger.info("=" * 60)
    logger.info("开始抓取 A股 IPO 招股说明书（2023年至今）")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 获取映射表
    logger.info("获取代码映射表...")
    try:
        map_resp = requests.get(MAP_URL, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
        stock_list = map_resp.json().get("stockList", [])
        logger.info(f"成功加载 {len(stock_list)} 只股票")
    except Exception as e:
        logger.error(f"映射表加载失败: {e}")
        return

    # 读取已下载记录（如果脚本中断，下次运行可跳过）
    existing = set()
    if CSV_PATH.exists():
        try:
            old = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
            existing = set(old["pdf_url"].dropna().astype(str))
        except Exception:
            pass

    all_records = []
    
    # 2. 遍历所有股票
    for idx, stock in enumerate(stock_list, 1):
        code = stock["code"]
        name = stock["zwjc"]
        org_id = stock["orgId"]
        
        column = "sse" if code.startswith(("60", "68")) else "szse"
        stock_param = f"{code},{org_id}"
        
        payload = {
            "tabName": "fulltext",
            "pageSize": 30,
            "pageNum": 1,
            "column": column,
            "stock": stock_param,
            # 兼容沪深"招股说明书"和北交所"公开发行说明书"
            "searchkey": "招股说明书 公开发行说明书", 
            "seDate": "2023-01-01~2026-12-31",
            "category": "",
            "trade": "",
            "seid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }

        try:
            resp = requests.post(URL, headers=HEADERS, data=payload, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            announcements = data.get("announcements") or []
        except Exception as e:
            logger.warning(f"[{idx}/{len(stock_list)}] {code} 查询失败: {e}")
            time.sleep(1)
            continue

        if not announcements:
            # 绝大多数老股票都不会有，直接跳过不记日志
            time.sleep(0.5)
            continue

        logger.info(f"[{idx}/{len(stock_list)}] {code} {name} 发现 {len(announcements)} 条相关文件")

        for ann in announcements:
            title = re.sub(r"<[^>]+>", "", ann.get("announcementTitle", "")) # 去除高亮标签
            # 过滤掉只是包含文字的无关公告（比如“关于招股说明书的更正公告”）
            if "招股说明书" not in title and "公开发行说明书" not in title:
                continue
            # 只要正宗的说明书，排除法律意见书、审计报告等
            if "摘要" in title or "法律意见" in title:
                continue

            adjunct_url = ann.get("adjunctUrl", "")
            if not adjunct_url.endswith(".PDF") and not adjunct_url.endswith(".pdf"):
                # 极少数北交所可能是 HTML，这里保守起见跳过，或者可以自己改成保存 html
                continue

            pdf_url = f"https://static.cninfo.com.cn/{adjunct_url}"
            if pdf_url in existing:
                continue

            safe_title = re.sub(r'[\\/:*?"<>|]', "", title)[:50] # 防止文件名过长
            filename = f"{code}_{name}_{safe_title}.pdf"
            
            local_path = download_pdf(pdf_url, filename)
            
            all_records.append({
                "company_code": code,
                "company_name": name,
                "title": title,
                "publish_date": datetime.fromtimestamp(ann.get("announcementTime")/1000).strftime("%Y-%m-%d"),
                "pdf_url": pdf_url,
                "local_path": str(local_path) if local_path else "",
                "download_status": "ok" if local_path else "failed",
                "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            existing.add(pdf_url)
            time.sleep(1) # 下载文件之间停顿

        time.sleep(0.5) # 查询股票之间停顿，防封 IP

    # 3. 保存结果
    if all_records:
        df_new = pd.DataFrame(all_records)
        if CSV_PATH.exists():
            try:
                df_old = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
                df_all = pd.concat([df_old, df_new], ignore_index=True)
            except Exception:
                df_all = df_new
        else:
            df_all = df_new
        df_all.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        logger.info(f"CSV 已更新：共 {len(df_all)} 条，本次新增 {len(df_new)} 条")
    else:
        logger.info("本次无新增记录")

    logger.info("抓取结束")


if __name__ == "__main__":
    main()