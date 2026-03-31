import io
import random
import re
import tarfile
import time
from pathlib import Path
from typing import Optional, List, Tuple

import requests

from config import projConfig
from utils.DBHelper import DBWriter, DBFetchAllFreePMC
from utils.DataType import TempPMID
from utils.LogHelper import print_error, medLog
from utils.WebHelper import WebHelper


class PDFHelper:
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    @staticmethod
    def handle_error(e):
        print_error("Error occured: %s" % e)

    @classmethod
    def __GetPDFFileName(cls, tempid: TempPMID) -> str:
        return re.sub(r'[< >/\\|:"*?]', ' ', tempid.doctitle)

    @classmethod
    def __GetPDFSavePath(cls, tempid: TempPMID) -> str:
        return f"{projConfig.pdfSavePath}/{cls.__GetPDFFileName(tempid)}.pdf"

    @classmethod
    def __IsPDFExist(cls, tempid: TempPMID) -> bool:
        return Path(cls.__GetPDFSavePath(tempid)).exists()

    # ==================== 按选择下载（GUI 用） ====================

    @classmethod
    def PDFBatchDownloadWithSelection(cls, selected_pmcids: List[str]):
        """根据用户在选择面板中勾选的 PMCID 列表下载 PDF。"""
        tablename = 'pubmed%s' % projConfig.savetime
        dbpath = 'pubmedsql'

        all_items: List[TempPMID] = DBFetchAllFreePMC(dbpath, tablename)
        target = [item for item in all_items
                  if item.PMCID in selected_pmcids and not cls.__IsPDFExist(item)]

        if not target:
            medLog.info("没有需要下载的 PDF")
            return

        cls._download_items(target, dbpath)

    # ==================== 按数量下载（CLI 用） ====================

    @classmethod
    def PDFBatchDownloadEntry(cls, limit):
        tablename = 'pubmed%s' % projConfig.savetime
        dbpath = 'pubmedsql'
        free_list: List[TempPMID] = DBFetchAllFreePMC(dbpath, tablename)
        pmc_list = [item for item in free_list if item.PMCID]

        target = []
        for item in pmc_list:
            if cls.__IsPDFExist(item):
                cls.PDFUpdateDB(item, cls.__GetPDFSavePath(item), dbpath)
                medLog.info(f"PDF 已存在，跳过: {cls.__GetPDFFileName(item)}")
            else:
                target.append(item)
        target = target[:limit]

        if not target:
            medLog.info("没有需要下载的 PDF")
            return

        cls._download_items(target, dbpath)

    # ==================== 下载核心逻辑 ====================

    @classmethod
    def _download_items(cls, items: List[TempPMID], dbpath: str):
        medLog.info(f"查询 {len(items)} 篇文献的 OA 下载链接…")
        url_map = {}
        for item in items:
            result = WebHelper.GetPDFUrlFromOA(item.PMCID)
            if result:
                url_map[item.PMCID] = result
            else:
                medLog.warning(f"{item.PMCID} 无 OA 下载链接，跳过")

        downloadable = [t for t in items if t.PMCID in url_map]
        if not downloadable:
            medLog.warning("没有可下载的 OA 文献")
            return

        medLog.info(f"开始下载 {len(downloadable)} 篇 PDF")
        for item in downloadable:
            url, fmt = url_map[item.PMCID]
            try:
                pdf_bytes = cls._download_sync(url)
                if pdf_bytes is None:
                    continue
                if fmt == "tgz":
                    pdf_bytes = cls._extract_pdf_from_tgz(pdf_bytes, item.PMCID)
                    if pdf_bytes is None:
                        continue
                if cls.PDFSaveFile(pdf_bytes, item):
                    cls.PDFUpdateDB(item, cls.__GetPDFSavePath(item), dbpath)
            except Exception as e:
                medLog.error(f"{item.PMCID} 处理出错: {e}")

    @classmethod
    def _download_sync(cls, url: str) -> Optional[bytes]:
        try:
            r = requests.get(url, headers=cls.headers, timeout=60)
            r.raise_for_status()
            if len(r.content) > 1000:
                medLog.info(f"下载成功 ({len(r.content)} bytes)")
                return r.content
            medLog.error(f"下载内容过小 ({len(r.content)} bytes)")
            return None
        except Exception as e:
            medLog.error(f"下载失败: {e}")
            return None

    @classmethod
    def _extract_pdf_from_tgz(cls, tgz_bytes: bytes, pmcid: str) -> Optional[bytes]:
        try:
            tf = tarfile.open(fileobj=io.BytesIO(tgz_bytes), mode="r:gz")
            for member in tf.getmembers():
                if member.name.lower().endswith(".pdf"):
                    f = tf.extractfile(member)
                    if f:
                        medLog.info(f"{pmcid}: 从 tgz 提取 {member.name}")
                        return f.read()
            return None
        except Exception as e:
            medLog.error(f"{pmcid}: 解压 tgz 失败: {e}")
            return None

    @classmethod
    def PDFSaveFile(cls, content: bytes, tempid: TempPMID) -> bool:
        if not content:
            return False
        try:
            savepath = cls.__GetPDFSavePath(tempid)
            with open(savepath, 'wb') as f:
                f.write(content)
            medLog.info(f"PDF 保存成功: {tempid.PMCID}")
            return True
        except Exception as e:
            medLog.error(f"PDF 保存失败 {tempid.PMCID}: {e}")
            return False

    @classmethod
    def PDFUpdateDB(cls, tempid: TempPMID, savepath: str, dbpath: str) -> bool:
        tablename = 'pubmed%s' % projConfig.savetime
        try:
            DBWriter(dbpath, f"UPDATE {tablename} SET savepath = ? WHERE PMCID = ?",
                     (savepath, tempid.PMCID))
            return True
        except Exception as e:
            medLog.error(f"数据库更新失败: {e}")
            return False

    @classmethod
    def PDFUpdateDBByPMID(cls, pmid: str, savepath: str, dbpath: str) -> bool:
        tablename = 'pubmed%s' % projConfig.savetime
        try:
            DBWriter(dbpath, f"UPDATE {tablename} SET savepath = ? WHERE PMID = ?",
                     (savepath, pmid))
            return True
        except Exception as e:
            medLog.error(f"数据库更新失败: {e}")
            return False

    # ==================== Sci-Hub 下载 ====================

    SCIHUB_FALLBACK_DOMAINS = ["sci-hub.ru", "sci-hub.st", "sci-hub.se"]

    @classmethod
    def SciHubBatchDownload(cls, articles: List[Tuple[str, str, str]]):
        """
        通过 Sci-Hub 批量下载非免费文献。
        articles: [(doi, pmid, doctitle), ...]
        """
        try:
            import cloudscraper
        except ImportError:
            medLog.error("需要安装 cloudscraper: pip install cloudscraper")
            return

        dbpath = "pubmedsql"
        user_domain = projConfig.scihubDomain.strip()
        domains = [user_domain] + [d for d in cls.SCIHUB_FALLBACK_DOMAINS if d != user_domain]

        # 逐个域名尝试建立连接（热身），找到第一个能用的
        scraper, working_domain = cls._warmup_scihub(cloudscraper, domains)
        if not scraper:
            medLog.error("所有 Sci-Hub 域名均不可用，请检查网络或更换域名")
            return

        medLog.info(f"Sci-Hub 使用域名: {working_domain}, 待下载 {len(articles)} 篇")

        success, fail = 0, 0
        for i, (doi, pmid, doctitle) in enumerate(articles):
            medLog.info(f"[{i+1}/{len(articles)}] 尝试下载: {doctitle[:50]}...")
            try:
                pdf_bytes = cls._scihub_download_one(scraper, working_domain, doi)

                if pdf_bytes == "CAPTCHA":
                    medLog.error(f"Sci-Hub 验证码保护已触发，中止下载。请用浏览器访问 https://{working_domain}/ 完成验证后重试")
                    break

                if pdf_bytes is None:
                    fail += 1
                    continue

                tempid = TempPMID(PMCID="", PMID=pmid, doctitle=doctitle)
                savepath = cls._PDFHelper__GetPDFSavePath(tempid)
                if Path(savepath).exists():
                    medLog.info(f"  文件已存在，跳过")
                    success += 1
                    continue

                if cls.PDFSaveFile(pdf_bytes, tempid):
                    cls.PDFUpdateDBByPMID(pmid, savepath, dbpath)
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                medLog.error(f"  处理出错: {e}")
                fail += 1

            if i < len(articles) - 1:
                delay = random.uniform(3, 6)
                medLog.info(f"  等待 {delay:.1f}s...")
                time.sleep(delay)

        medLog.info(f"Sci-Hub 下载完成: 成功 {success}, 失败 {fail}")

    @classmethod
    def _warmup_scihub(cls, cloudscraper_mod, domains: List[str]):
        """
        对各域名做热身，建立 DDoS-Guard session。
        每次 403 时重新创建 scraper 实例（不同浏览器指纹），最多尝试 6 次。
        返回 (scraper, working_domain) 或 (None, None)。
        """
        for d in domains:
            medLog.info(f"连接 Sci-Hub: {d}...")
            for attempt in range(6):
                scraper = cloudscraper_mod.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
                try:
                    r = scraper.get(f"https://{d}/", timeout=15)
                    if r.status_code == 200:
                        medLog.info(f"  {d} 连接成功 (第{attempt+1}次)")
                        return scraper, d
                    medLog.debug(f"  {d} 第{attempt+1}次: {r.status_code}")
                except Exception as e:
                    medLog.debug(f"  {d} 第{attempt+1}次: {type(e).__name__}")
                time.sleep(2)
        return None, None

    @classmethod
    def _scihub_download_one(cls, scraper, domain: str, doi: str) -> Optional[bytes]:
        """从 Sci-Hub 下载单篇 PDF（参考 Zotero 的多重解析策略）。"""
        from lxml import etree

        url = f"https://{domain}/{doi}"
        try:
            r = scraper.get(url, timeout=30)
            if r.status_code == 404:
                medLog.warning(f"  文献未收录 (404)")
                return None
            if r.status_code != 200:
                medLog.debug(f"  {domain}: 返回 {r.status_code}")
                return None

            # 检测验证码页面
            if "robot" in r.text.lower()[:500] or "captcha" in r.text.lower()[:500] or "altcha" in r.text.lower()[:1000]:
                medLog.error(f"  Sci-Hub 触发了验证码保护！请用浏览器访问 https://{domain}/ 完成验证后重试")
                return "CAPTCHA"  # 特殊标记

            # 多重策略提取 PDF URL：
            pdf_url = None
            tree = etree.HTML(r.text)

            # 策略1: Zotero 方式 — #pdf 元素的 src
            pdf_elem = tree.xpath('//*[@id="pdf"]/@src')
            if pdf_elem and pdf_elem[0]:
                pdf_url = pdf_elem[0]

            # 策略2: embed/iframe 的 src
            if not pdf_url:
                for sel in ['//embed/@src', '//iframe/@src']:
                    found = tree.xpath(sel)
                    if found and '.pdf' in found[0].lower():
                        pdf_url = found[0]
                        break

            # 策略3: 正则匹配页面中的 PDF 链接
            if not pdf_url:
                pdf_urls = re.findall(r"""//[^\s"'<>]+?\.pdf""", r.text)
                if pdf_urls:
                    pdf_url = pdf_urls[0]

            if not pdf_url:
                medLog.debug(f"  页面中未找到 PDF 链接")
                return None

            if pdf_url.startswith('//'):
                pdf_url = 'https:' + pdf_url
            elif pdf_url.startswith('/'):
                pdf_url = f'https://{domain}{pdf_url}'

            medLog.info(f"  PDF: ...{pdf_url[-50:]}")

            r2 = scraper.get(pdf_url, timeout=60)
            if r2.status_code != 200:
                medLog.warning(f"  PDF 下载返回 {r2.status_code}")
                return None

            if len(r2.content) < 1000 or r2.content[:4] != b"%PDF":
                medLog.warning(f"  下载内容不是有效 PDF ({len(r2.content)} bytes)")
                return None

            medLog.info(f"  下载成功 ({len(r2.content)} bytes)")
            return r2.content

        except Exception as e:
            medLog.debug(f"  {domain}: {type(e).__name__}")
            return None
