# -*- coding: utf-8 -*-
import time
from typing import Optional, List

from lxml import etree

from config import projConfig
from utils import DBHelper
from utils.DataType import ArticleFreeType, SingleSearchData
from utils.LogHelper import medLog
from utils.WebHelper import WebHelper

EFETCH_BATCH = 200


def parse_efetch_xml_basic(xml_str: str) -> List[SingleSearchData]:
    try:
        root = etree.fromstring(xml_str.encode("utf-8"))
    except Exception as e:
        medLog.error(f"解析 efetch XML 失败: {e}")
        return []

    articles = root.xpath("//PubmedArticle")
    results = []
    for article in articles:
        try:
            title_elem = article.xpath(".//ArticleTitle")
            doctitle = "".join(title_elem[0].itertext()).strip() if title_elem else ""

            authors = article.xpath(".//Author")
            author_names = []
            for a in authors:
                last = (a.findtext("LastName") or "").strip()
                fore = (a.findtext("ForeName") or "").strip()
                col = (a.findtext("CollectiveName") or "").strip()
                if last:
                    author_names.append(f"{fore} {last}".strip())
                elif col:
                    author_names.append(col)
            full_author = ", ".join(author_names)
            short_author = (author_names[0] + " et al.") if len(author_names) > 1 else (author_names[0] if author_names else "")

            full_journal = (article.xpath(".//Journal/Title/text()") or [""])[0]
            short_journal = (article.xpath(".//Journal/ISOAbbreviation/text()") or [""])[0]
            pmid = (article.xpath(".//MedlineCitation/PMID/text()") or [""])[0]

            pmc_elem = article.xpath(".//ArticleId[@IdType='pmc']/text()")
            freemark = ArticleFreeType.FreePMCArticle if pmc_elem else ArticleFreeType.NoneFreeArticle

            pub_types = article.xpath(".//PublicationType/text()")
            reviewmark = any("review" in pt.lower() for pt in pub_types)

            results.append(SingleSearchData(
                doctitle=doctitle, short_journal=short_journal,
                full_journal=full_journal, short_author=short_author,
                full_author=full_author, PMID=pmid,
                freemark=freemark, reviewmark=reviewmark,
            ))
        except Exception as e:
            medLog.error(f"解析单篇文献失败: {e}")
    return results


def SaveSearchData(datalist: List[SingleSearchData], dbpath: str) -> None:
    tablename = f'pubmed{projConfig.savetime}'
    for item in datalist:
        try:
            sql = f"INSERT INTO {tablename} (doctitle, full_author, short_author, full_journal, short_journal, PMID, freemark, reviewmark) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            DBHelper.DBWriter(dbpath, sql, (
                item.doctitle, item.full_author, item.short_author,
                item.full_journal, item.short_journal, item.PMID,
                item.freemark.value, item.reviewmark,
            ))
        except Exception as e:
            medLog.error("写入失败: %s" % e)


def spiderpub(keyword: str, year: Optional[int], page_limit: int, resultNum: int) -> None:
    total = min(page_limit * 50, resultNum)
    medLog.info(f"准备通过 E-utilities API 获取 {total} 篇文献")

    all_pmids = []
    for retstart in range(0, total, 500):
        batch_size = min(500, total - retstart)
        medLog.info(f"ESearch: 获取第 {retstart+1}-{retstart+batch_size} 条 PMID")
        _, pmids = WebHelper.ESearch(keyword, year, retmax=batch_size, retstart=retstart)
        all_pmids.extend(pmids)
        if len(pmids) < batch_size:
            break

    medLog.info(f"共获取到 {len(all_pmids)} 个 PMID")
    if not all_pmids:
        return

    datalist = []
    start = time.time()
    for i in range(0, len(all_pmids), EFETCH_BATCH):
        batch = all_pmids[i:i+EFETCH_BATCH]
        medLog.info(f"EFetch: 第 {i+1}-{i+len(batch)} 篇")
        xml_str = WebHelper.EFetch(batch)
        if xml_str:
            datalist.extend(parse_efetch_xml_basic(xml_str))
    medLog.info(f"解析 {len(datalist)} 篇，耗时 {time.time()-start:.2f}s")

    dbpath = "pubmedsql"
    tablename = f"pubmed{projConfig.savetime}"
    txtname = f"{projConfig.pdfSavePath}/pubmed{projConfig.savetime}.txt"

    try:
        DBHelper.DBCreater(dbpath)
        DBHelper.DBTableCreater(dbpath, tablename)
        SaveSearchData(datalist, dbpath)
    except Exception as e:
        medLog.error(f"保存到数据库出错: {e}")

    try:
        with open(txtname, "w", encoding="utf-8") as f:
            for item in datalist:
                f.write(item.to_string() + "\n")
        medLog.info("搜索信息导入到 txt 成功")
    except Exception as e:
        medLog.error(f"导出到 txt 出错: {e}")
