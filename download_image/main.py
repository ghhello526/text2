"""微信公众号文章图片批量下载工具

从 urls.txt 中读取公众号文章链接，使用 Playwright 渲染页面后
提取文章标题和图片，按文章标题分文件夹保存。
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------- 配置 ----------
URLS_FILE = Path(__file__).parent / "urls.txt"
OUTPUT_DIR = Path(__file__).parent / "images"
TRACK_FILE = Path(__file__).parent / "downloaded.json"
REQUEST_TIMEOUT = 30    # 图片下载超时（秒）
PAGE_TIMEOUT = 60000    # 页面加载超时（毫秒）
DOWNLOAD_DELAY = 0.3    # 每张图片下载间隔（秒）
# --------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def load_downloaded() -> dict[str, str]:
    """加载已下载记录 {url: folder_name}"""
    if TRACK_FILE.exists():
        try:
            with open(TRACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_downloaded(record: dict[str, str]) -> None:
    """保存已下载记录"""
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def is_already_downloaded(url: str, record: dict[str, str]) -> bool:
    """检查该链接是否已下载且文件夹仍存在"""
    folder_name = record.get(url)
    if not folder_name:
        # 兼容旧 URL（可能带有 /?from= 等后缀），尝试前缀匹配
        for saved_url, name in record.items():
            if saved_url.rstrip("/") == url.rstrip("/"):
                folder_name = name
                break
    if folder_name and (OUTPUT_DIR / folder_name).is_dir():
        return True
    return False


def load_urls(path: Path) -> list[str]:
    """从文件读取链接列表，忽略空行和注释行"""
    if not path.exists():
        print(f"[错误] 找不到链接文件: {path}")
        sys.exit(1)

    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def render_page(url: str) -> tuple[str | None, str | None]:
    """使用 Playwright 渲染页面，返回 (HTML, 标题)"""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            print("  正在加载页面...")
            page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")

            # 等待文章内容加载
            try:
                page.wait_for_selector("#js_content", timeout=15000)
            except PWTimeout:
                page.wait_for_selector("body", timeout=5000)

            # 渐进式滚动触发所有图片懒加载
            print("  正在滚动页面触发图片加载...")
            for _ in range(3):  # 多轮滚动确保全部触发
                page.evaluate("""
                    () => new Promise((resolve) => {
                        let totalHeight = 0;
                        const distance = 300;
                        const timer = setInterval(() => {
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            if (totalHeight >= document.body.scrollHeight) {
                                clearInterval(timer);
                                resolve();
                            }
                        }, 150);
                    })
                """)
                page.wait_for_timeout(1500)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)

            # 最终等待所有网络请求完成
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            page.wait_for_timeout(2000)

            html = page.content()

            # 提取标题
            title = page.title().strip() or None
            if not title:
                title = page.evaluate(
                    '() => document.querySelector("#activity-name")?.innerText || ""'
                )

            return html, title

        except Exception as e:
            print(f"  [错误] 页面渲染失败: {e}")
            return None, None
        finally:
            browser.close()


def extract_title(html: str, page_title: str | None = None) -> str:
    """从渲染后的 HTML 提取文章标题"""
    soup = BeautifulSoup(html, "html.parser")

    # 优先从页面 title 获取
    if page_title:
        title = re.sub(r"\s*[-_|—]*\s*(微信公众号|微信公众平台).*$", "", page_title)
        title = title.strip()
        if title:
            return ILLEGAL_CHARS.sub("_", title)

    # 尝试 activity-name
    elem = soup.find(id="activity-name")
    if elem and elem.text.strip():
        return ILLEGAL_CHARS.sub("_", elem.text.strip())[:80]

    # meta og:title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        t = og["content"].strip()
        return ILLEGAL_CHARS.sub("_", t)[:80]

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_image_urls(html: str, page_url: str) -> list[str]:
    """从渲染后的 HTML 提取图片链接，自动跳过封面图"""
    soup = BeautifulSoup(html, "html.parser")
    img_urls = []
    # 标记每张图片是否来自轮播（swiper）容器
    is_swiper_list = []

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src")
        if not src:
            continue

        if src.startswith("data:") or src.startswith("blob:"):
            continue

        cls = " ".join(img.get("class", [])) if img.get("class") else ""
        if any(k in cls.lower() for k in ("avatar", "qrcode", "qr_code", "head_img", "underline-edu")):
            continue
        if "res.wx.qq.com" in src:
            continue

        full_src = urljoin(page_url, src)

        # 检查是否在 swiper 轮播容器中（封面图常见于此）
        in_swiper = False
        for parent in img.parents:
            parent_cls = " ".join(parent.get("class", [])) if parent.get("class") else ""
            if "swiper" in parent_cls.lower():
                in_swiper = True
                break

        img_urls.append(full_src)
        is_swiper_list.append(in_swiper)

    # 去重
    seen = set()
    unique = []
    swiper_flags = []
    for u, s in zip(img_urls, is_swiper_list):
        if u not in seen:
            seen.add(u)
            unique.append(u)
            swiper_flags.append(s)

    # 如果第一张图在 swiper 中且后面还有 swiper 图片，跳过第一张（封面）
    if (
        len(unique) > 1
        and swiper_flags[0]
        and any(swiper_flags[1:])
    ):
        print("  [跳过封面图]")
        unique = unique[1:]

    return unique


def get_extension(url: str, content_type: str | None = None) -> str:
    """推断文件扩展名"""
    path = url.split("?")[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        return ext

    if content_type:
        mapping = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
        }
        for mime, e in mapping.items():
            if mime in content_type:
                return e
    return ".jpg"


def download_image(url: str, save_path: Path, index: int) -> bool:
    """下载单张图片"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        ext = get_extension(url, resp.headers.get("Content-Type"))
        filepath = save_path / f"{index:02d}{ext}"

        with open(filepath, "wb") as f:
            f.write(resp.content)
        return True
    except requests.RequestException as e:
        print(f"    [错误] 第 {index} 张下载失败: {e}")
        return False


def process_article(url: str, record: dict[str, str]) -> None:
    """处理单篇文章，返回是否下载成功"""
    if is_already_downloaded(url, record):
        folder = record.get(url, record.get(url.rstrip("/"), "?"))
        print(f"\n跳过（已下载）: {url}")
        print(f"  已保存至: {OUTPUT_DIR / folder}")
        return

    print(f"\n处理: {url}")

    html, page_title = render_page(url)
    if not html:
        return

    title = extract_title(html, page_title)
    title = ILLEGAL_CHARS.sub("_", title).strip().rstrip(".")
    if len(title) > 80:
        title = title[:80]

    save_dir = OUTPUT_DIR / title
    save_dir.mkdir(parents=True, exist_ok=True)

    img_urls = extract_image_urls(html, url)
    print(f"  标题: {title}")
    print(f"  发现 {len(img_urls)} 张图片")

    if not img_urls:
        print(f"  [警告] 未找到图片，可能页面结构已变更")
        return

    success = 0
    for i, img_url in enumerate(img_urls, 1):
        print(f"  下载 ({i}/{len(img_urls)}): {img_url[:80]}...")
        if download_image(img_url, save_dir, i):
            success += 1
        time.sleep(DOWNLOAD_DELAY)

    print(f"  完成: {success}/{len(img_urls)} 张下载成功 -> {save_dir}")

    # 记录已下载
    if success > 0:
        record[url] = title
        save_downloaded(record)


def main():
    print("=" * 60)
    print("  微信公众号文章图片批量下载工具")
    print("=" * 60)

    urls = load_urls(URLS_FILE)
    record = load_downloaded()
    already = sum(1 for u in urls if is_already_downloaded(u, record))
    print(f"共加载 {len(urls)} 个链接（{already} 个已下载，将跳过）\n")

    for url in urls:
        process_article(url, record)

    print(f"\n全部完成！图片保存在: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()