# -*- coding: utf-8 -*-
import time
from typing import List

from lxml import etree

from config import projConfig
from utils.DBHelper import DBSaveInfo, DBFetchAllPMID
from utils.DataType import SingleDocInfo, Abstract
from utils.ExcelHelper import ExcelHelper
from utils.LogHelper import medLog
from utils.WebHelper import WebHelper

EFETCH_BATCH = projConfig.InfoBatchSize


def parse_efetch_xml_detail(xml_str: str) -> List[SingleDocInfo]:
    try:
        root = etree.fromstring(xml_str.encode("utf-8"))
    except Exception as e:
        medLog.error(f"解析 efetch XML 失败: {e}")
        return []

    articles = root.xpath("//PubmedArticle")
    results = []
    for article in articles:
        try:
            pmid = (article.xpath(".//MedlineCitation/PMID/text()") or [""])[0]

            pmc_elem = article.xpath(".//ArticleId[@IdType='pmc']/text()")
            pmcid = ""
            if pmc_elem:
                pmcid = pmc_elem[0] if pmc_elem[0].startswith("PMC") else f"PMC{pmc_elem[0]}"

            doi_elem = article.xpath(".//ArticleId[@IdType='doi']/text()") or article.xpath(".//ELocationID[@EIdType='doi']/text()")
            doi = doi_elem[0].strip() if doi_elem else ""

            abstract_parts = article.xpath(".//Abstract/AbstractText")
            bg = meth = res = conc = reg = ""
            plain = ""
            if abstract_parts:
                for part in abstract_parts:
                    label = (part.get("Label") or "").upper()
                    text = "".join(part.itertext()).strip()
                    if not text:
                        continue
                    if "BACKGROUND" in label or "INTRODUCTION" in label or "OBJECTIVE" in label:
                        bg = f"{label}: {text}"
                    elif "METHOD" in label:
                        meth = f"{label}: {text}"
                    elif "RESULT" in label or "FINDING" in label:
                        res = f"{label}: {text}"
                    elif "CONCLUSION" in label:
                        conc = f"{label}: {text}"
                    elif "REGISTRATION" in label or "TRIAL" in label:
                        reg = f"{label}: {text}"
                    else:
                        plain += text + " "
                if not any([bg, meth, res, conc]):
                    plain = " ".join("".join(p.itertext()).strip() for p in abstract_parts)

            keywords = article.xpath(".//Keyword/text()")
            keyword_str = ", ".join(kw.strip() for kw in keywords)

            affi_elems = article.xpath(".//AffiliationInfo/Affiliation/text()")
            affiliations, seen = [], set()
            for affi in affi_elems:
                affi = affi.strip()
                if affi and affi not in seen:
                    seen.add(affi)
                    affiliations.append(f"{len(affiliations)+1}.{affi}")

            results.append(SingleDocInfo(
                PMCID=pmcid, doi=doi,
                abstract=Abstract(background=bg, methods=meth, results=res,
                                  conclusions=conc, registration=reg,
                                  keywords=keyword_str, abstract=plain.strip()),
                affiliations=affiliations, keyword=keyword_str, PMID=pmid,
            ))
        except Exception as e:
            medLog.error(f"解析单篇详情失败: {e}")
    return results


def geteachinfo(dbpath):
    tablename = "pubmed%s" % projConfig.savetime
    PMID_list = DBFetchAllPMID(dbpath, tablename)
    if not PMID_list:
        medLog.error("数据库读取出错，内容为空")
        return

    start = time.time()
    medLog.info(f"开始获取 {len(PMID_list)} 篇文献详情 (批量: {EFETCH_BATCH})")

    for i in range(0, len(PMID_list), EFETCH_BATCH):
        batch = PMID_list[i:i+EFETCH_BATCH]
        medLog.info(f"EFetch 详情: 第 {i+1}-{i+len(batch)} 篇")
        xml_str = WebHelper.EFetch([item.PMID for item in batch])
        if not xml_str:
            medLog.error("EFetch 批次失败，跳过")
            continue
        for doc in parse_efetch_xml_detail(xml_str):
            DBSaveInfo(doc, dbpath)

    medLog.info(f"geteachinfo 完成，耗时 {time.time()-start:.2f}s")
    ExcelHelper.PD_To_excel(dbpath)
