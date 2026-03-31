import asyncio
import time
import urllib.parse
from typing import Optional, Tuple, List

import aiohttp
import requests
from aiohttp import ClientSession, ClientTimeout
from lxml import etree
from requests.exceptions import HTTPError, ConnectionError, ProxyError, ConnectTimeout

from utils.LogHelper import medLog

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


class WebHelper:
    baseurl = "https://pubmed.ncbi.nlm.nih.gov/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = requests.Session()

    # ======================== E-utilities API ========================

    @classmethod
    def ESearch(cls, keyword: str, year: int = None, retmax: int = 500, retstart: int = 0) -> Tuple[int, List[str]]:
        params = {"db": "pubmed", "term": keyword, "retmax": retmax,
                  "retstart": retstart, "retmode": "json"}
        if year is not None:
            params["datetype"] = "pdat"
            params["reldate"] = year * 365
        try:
            r = requests.get(EUTILS_BASE + "esearch.fcgi", params=params, timeout=30)
            r.raise_for_status()
            result = r.json().get("esearchresult", {})
            return int(result.get("count", 0)), result.get("idlist", [])
        except Exception as e:
            medLog.error(f"ESearch 请求失败: {e}")
            return 0, []

    @classmethod
    def EFetch(cls, pmid_list: List[str]) -> Optional[str]:
        if not pmid_list:
            return None
        try:
            r = requests.post(EUTILS_BASE + "efetch.fcgi",
                              data={"db": "pubmed", "id": ",".join(pmid_list),
                                    "retmode": "xml", "rettype": "abstract"},
                              timeout=60)
            r.raise_for_status()
            return r.text
        except Exception as e:
            medLog.error(f"EFetch 请求失败: {e}")
            return None

    @classmethod
    def GetPDFUrlFromOA(cls, pmcid: str) -> Optional[Tuple[str, str]]:
        """返回 (url, format)，format 为 'pdf' 或 'tgz'。"""
        try:
            r = requests.get("https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi",
                             params={"id": pmcid}, timeout=30)
            r.raise_for_status()
            xml = etree.fromstring(r.content)
            ftp2https = lambda u: u.replace("ftp://ftp.ncbi.nlm.nih.gov",
                                            "https://ftp.ncbi.nlm.nih.gov")
            pdf = xml.xpath("//link[@format='pdf']/@href")
            if pdf:
                return ftp2https(pdf[0]), "pdf"
            tgz = xml.xpath("//link[@format='tgz']/@href")
            if tgz:
                return ftp2https(tgz[0]), "tgz"
            return None
        except Exception as e:
            medLog.debug(f"OA API 查询 {pmcid} 失败: {e}")
            return None

    @classmethod
    def GetSearchResultNum(cls, **kwargs) -> int:
        keyword = kwargs.get("keyword", "")
        year = kwargs.get("year", None)
        count, _ = cls.ESearch(keyword, year, retmax=0)
        return count

    # ======================== 旧版 HTML 方法（保留兼容） ========================

    @classmethod
    def parseParamDcit(cls, **kwargs):
        search_keywords_dict = {}
        if 'keyword' in kwargs and kwargs['keyword']:
            search_keywords_dict['term'] = kwargs.get('keyword')
        if 'year' in kwargs and kwargs['year'] is not None:
            search_keywords_dict['filter'] = f'datesearch.y_{kwargs.get("year")}'
        search_keywords_dict['size'] = 50
        return search_keywords_dict

    @classmethod
    def encodeParam(cls, param: dict) -> str:
        return urllib.parse.urlencode(param)

    @staticmethod
    def __handle_error(e):
        medLog.error("Error occured: %s" % e)

    @classmethod
    def getSearchHtml(cls, parameter: str):
        paramencoded = "?" + parameter
        try:
            return WebHelper.GetHtml(cls.session, paramencoded)
        except Exception as e:
            medLog.error("获取检索页失败: %s\n" % e)

    @classmethod
    def GetHtml(cls, session, paramUrlEncoded: str, baseurl="https://pubmed.ncbi.nlm.nih.gov/") -> Optional[str]:
        request_url = cls.baseurl + paramUrlEncoded
        try:
            response = session.get(request_url, headers=cls.headers)
            response.raise_for_status()
            return response.content.decode("utf-8")
        except (ProxyError, ConnectTimeout, ConnectionError, HTTPError) as e:
            cls.__handle_error(e)
            medLog.error("GetHTML requests Error: %s" % e)
            return None
        except Exception as e:
            medLog.error(f"请求失败: {e}")
            return None

    @classmethod
    async def getSearchHtmlAsync(cls, parameter_list: list[str]) -> list[str]:
        parameter_list_encoded = ["?" + param for param in parameter_list]
        async with aiohttp.ClientSession(timeout=ClientTimeout(15)) as session:
            tasks = [asyncio.create_task(cls.GetHtmlAsync(session, p)) for p in parameter_list_encoded]
            return await asyncio.gather(*tasks)

    @classmethod
    async def GetHtmlAsync(cls, session: ClientSession, paramUrlEncoded: str,
                           baseurl="https://pubmed.ncbi.nlm.nih.gov/") -> Optional[str]:
        request_url = cls.baseurl + paramUrlEncoded
        semaphore = asyncio.Semaphore(5)
        async with semaphore:
            try:
                response = await session.get(request_url, headers=cls.headers)
                content = await response.read()
                return content.decode("utf-8")
            except Exception as e:
                cls.__handle_error(e)
                return None

    @classmethod
    async def GetAllHtmlAsync(cls, PMIDList: list[str]) -> list[str]:
        try:
            async with aiohttp.ClientSession(timeout=ClientTimeout(30)) as session:
                tasks = [asyncio.create_task(cls.GetHtmlAsync(session, PMID)) for PMID in PMIDList]
                return await asyncio.gather(*tasks)
        except Exception as e:
            medLog.critical(" GetAllHtmlAsync:", e)
            raise
