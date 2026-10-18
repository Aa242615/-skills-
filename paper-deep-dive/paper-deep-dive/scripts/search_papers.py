#!/usr/bin/env python3
"""多源文献检索：解析论文、查引用脉络、核验引用真伪。

为什么需要它：LLM 凭记忆写引用是本类任务最大的翻车点——生成的标题、作者、
年份看起来完美，实际根本不存在。本脚本把检索与核验变成可执行步骤：每条结果
都带 DOI/arXiv 链接，且是刚从 API 拿回来的，不是回忆出来的。

多源级联策略（重要）：
  Semantic Scholar 无 key 时全局限流约 1 请求/秒，实测经常直接 429。
  因此默认顺序是 OpenAlex / Crossref（不限流、可靠）先上，S2 只用于它独有的
  「引用语境原句」——判断后续工作是"真在用"还是"related work 里凑数"，
  目前只有 S2 能给出这个信息。
  有 S2 API key 时设环境变量 S2_API_KEY，限流会宽松很多。

用法:
  python search_papers.py resolve "1706.03762"
  python search_papers.py refs    "1706.03762" --limit 15
  python search_papers.py cites   "1706.03762" --limit 15 --influential
  python search_papers.py verify  "10.1109/icassp39728.2021.9413901" "1706.03762"
  python search_papers.py related "Swin Transformer"

id 可以是：DOI / arXiv ID / Semantic Scholar paperId / OpenAlex W-id / 论文标题。

依赖: pip install requests
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit("缺少依赖，请先运行:  pip install requests")

UA = {"User-Agent": "paper-deep-dive/1.0 (literature verification)"}
TIMEOUT = 25
S2_KEY = os.environ.get("S2_API_KEY", "").strip()
_LAST_S2 = [0.0]


def _get(url: str, params: dict | None = None, retries: int = 3,
         slow: bool = False) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        except Exception as e:
            print(f"  ! 网络错误: {e}", file=sys.stderr)
            return None
        if r.status_code == 429:
            if attempt == retries - 1:
                return None
            ra = r.headers.get("Retry-After")
            wait = float(ra) if (ra or "").replace(".", "").isdigit() else 3 * (attempt + 1)
            wait = min(wait, 20)
            print(f"  … 限流，等待 {wait:.0f}s 后重试", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            print(f"  ! HTTP {r.status_code}", file=sys.stderr)
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def s2_get(path: str, params: dict | None = None) -> dict | None:
    """S2 免费限流严；有 key 就带上。默认每次调用间隔 3 秒，宁可慢也不要 429。"""
    gap = time.time() - _LAST_S2[0]
    need = 0.2 if S2_KEY else 3.0
    if gap < need:
        time.sleep(need - gap)
    _LAST_S2[0] = time.time()
    p = dict(params or {})
    hdr_url = f"https://api.semanticscholar.org/graph/v1/{path}"
    if S2_KEY:
        # requests 的 params 之外单独加 header
        try:
            r = requests.get(hdr_url, params=p, timeout=TIMEOUT,
                             headers={**UA, "x-api-key": S2_KEY})
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception as e:
            print(f"  ! S2 网络错误: {e}", file=sys.stderr)
            return None
    return _get(hdr_url, p, retries=2)


def openalex_get(path: str = "works", params: dict | None = None) -> dict | None:
    return _get(f"https://api.openalex.org/{path}", params)


def crossref_get(doi: str) -> dict | None:
    d = _get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    return (d or {}).get("message")


# ---------------------------------------------------------------- 解析

def norm_id(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s


def is_arxiv(s: str) -> bool:
    return bool(re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", s))


def s2_id(raw: str) -> str:
    s = norm_id(raw)
    if is_arxiv(s):
        return f"ARXIV:{re.sub(r'v\d+$', '', s)}"
    if s.upper().startswith(("ARXIV:", "DOI:", "CORPUSID:", "MAG:", "PMID:", "PMCID:")):
        return s
    if s.startswith("10."):
        return f"DOI:{s}"
    return s


S2_FIELDS = ("paperId,title,year,venue,publicationVenue,authors,externalIds,"
             "citationCount,influentialCitationCount,referenceCount,abstract,"
             "publicationTypes,openAccessPdf")


def s2_resolve(raw: str, quiet: bool = False) -> dict | None:
    d = s2_get(f"paper/{urllib.parse.quote(s2_id(raw), safe=':')}", {"fields": S2_FIELDS})
    if d and d.get("paperId"):
        return d
    r = s2_get("paper/search", {"query": raw, "limit": 3, "fields": S2_FIELDS})
    if r and r.get("data"):
        return r["data"][0]
    if not quiet:
        sys.stderr.write("  ! Semantic Scholar 未命中或被限流\n")
    return None


OA_SELECT = ("id,doi,display_name,publication_year,cited_by_count,cited_by_api_url,"
             "referenced_works_count,authorships,primary_location,type")


def openalex_resolve(raw: str) -> dict | None:
    s = norm_id(raw)
    if s.startswith("W") or s.startswith("10."):
        key = s if s.startswith("W") else f"doi:{s}"
        d = openalex_get(f"works/{urllib.parse.quote(key, safe=':')}",
                         {"select": OA_SELECT})
        if d and d.get("id"):
            return d
    if is_arxiv(s):
        r = openalex_get("works", {"filter": f"locations.landing_page_url:arxiv.org/abs/{s}",
                                   "per-page": 1, "select": OA_SELECT})
        if r and r.get("results"):
            return r["results"][0]
    r = openalex_get("works", {"filter": f"title.search:{s}", "per-page": 3,
                               "select": OA_SELECT})
    if r and r.get("results"):
        return r["results"][0]
    return None


def crossref_resolve(raw: str) -> dict | None:
    s = norm_id(raw)
    if not s.startswith("10."):
        r = _get("https://api.crossref.org/works",
                 {"query.bibliographic": s, "rows": 1,
                  "select": "DOI,title,author,issued,container-title,is-referenced-by-count"})
        items = ((r or {}).get("message") or {}).get("items") or []
        return items[0] if items else None
    m = crossref_get(s)
    return m or None


# ---------------------------------------------------------------- 取值

def title_of(rec: dict) -> str:
    t = rec.get("title") or rec.get("display_name")
    if isinstance(t, list):
        t = t[0] if t else None
    return t or "(无标题)"


def year_of(rec: dict) -> object:
    y = rec.get("year") or rec.get("publication_year")
    if y:
        return y
    issued = (rec.get("issued") or {}).get("date-parts") or []
    return issued[0][0] if issued and issued[0] else "?"


def venue_of(rec: dict) -> str:
    v = rec.get("venue")
    if v:
        return v
    ct = rec.get("container-title")
    if isinstance(ct, list) and ct:
        return ct[0]
    if isinstance(ct, str) and ct:
        return ct
    pv = rec.get("publicationVenue") or {}
    if pv.get("name"):
        return pv["name"]
    loc = rec.get("primary_location") or {}
    return ((loc.get("source") or {}).get("display_name")) or ""


def authors_of(rec: dict) -> str:
    names: list[str] = []
    if rec.get("authors"):
        names = [x.get("name", "") for x in rec["authors"]]
    elif rec.get("authorships"):
        names = [(x.get("author") or {}).get("display_name", "") for x in rec["authorships"]]
    elif rec.get("author"):
        for a in rec["author"]:
            n = " ".join(filter(None, [a.get("given"), a.get("family")]))
            names.append(n or a.get("name", ""))
    names = [n for n in names if n]
    if not names:
        return ""
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def link_of(rec: dict) -> str:
    if rec.get("doi"):
        return rec["doi"]
    if rec.get("DOI"):
        return f"https://doi.org/{rec['DOI']}"
    ext = rec.get("externalIds") or {}
    if ext.get("DOI"):
        return f"https://doi.org/{ext['DOI']}"
    if ext.get("ArXiv"):
        return f"https://arxiv.org/abs/{ext['ArXiv']}"
    if rec.get("paperId"):
        return f"https://www.semanticscholar.org/paper/{rec['paperId']}"
    return rec.get("id", "")


def cited_by(rec: dict) -> object:
    for k in ("citationCount", "cited_by_count", "is-referenced-by-count"):
        if rec.get(k) is not None:
            return rec[k]
    return "?"


# ---------------------------------------------------------------- 命令

def cmd_resolve(args) -> None:
    raw = args.id
    print(f"# 解析：{raw}\n")
    hits = []
    oa = openalex_resolve(raw)
    if oa:
        hits.append(("OpenAlex", oa))
    cr = crossref_resolve(raw)
    if cr and title_of(cr) != "(无标题)":
        hits.append(("Crossref", cr))
    s2 = s2_resolve(raw, quiet=True)
    if s2:
        hits.append(("Semantic Scholar", s2))
    if not hits:
        print("三个源都没有命中。请改用更完整的标题，或直接给 DOI / arXiv ID 再试。")
        sys.exit(2)
    for src, rec in hits:
        print(f"## {src}")
        for k, v in [("标题", title_of(rec)), ("作者", authors_of(rec)),
                     ("年份", year_of(rec)), ("发表处", venue_of(rec)),
                     ("被引", cited_by(rec)), ("链接", link_of(rec))]:
            print(f"- {k}: {v}")
        if rec.get("influentialCitationCount") is not None:
            print(f"- 有影响力的引用: {rec['influentialCitationCount']}")
        ab = rec.get("abstract") or ""
        if ab:
            print(f"- 摘要（截断）: {re.sub(chr(10), ' ', ab)[:300]}…")
        print()
    print("提示：以上标题与年份请以实际返回为准，不要凭记忆改写。"
          "预先印制的「arXiv 年份」常与正式发表年份不同，封面写正式发表年份。")


def _emit(rows: list[dict], tag: str, limit: int) -> None:
    for i, row in enumerate(rows[:limit], 1):
        rec = row.get("citingPaper") or row.get("citedPaper") or row
        print(f"\n**[{tag}{i}] {title_of(rec)}**")
        print(f"- 作者: {authors_of(rec)}")
        print(f"- 年份: {year_of(rec)}　发表处: {venue_of(rec)}")
        print(f"- 链接: {link_of(rec)}")
        c = cited_by(rec)
        if c != "?":
            print(f"- 被引: {c}")
        if row.get("intents"):
            print(f"- 引用意图（API 判定）: {', '.join(row['intents'])}")
        for c_ in (row.get("contexts") or [])[:2]:
            print(f"- 引用语境原句: “{re.sub(r'[[:space:]]+', ' ', c_).strip()[:300]}”")


def _s2_ctx_map(paper_id: str, limit: int, influential: bool) -> dict:
    """单独取 S2 的 contexts，失败就返回空表——不阻塞主流程。"""
    params = {"fields": "title,externalIds,contexts,intents,isInfluential,"
                        "year,venue,authors,citationCount",
              "limit": min(limit, 100)}
    if influential:
        params["filter"] = "isInfluential:true"
    r = s2_get(f"paper/{paper_id}/citations", params)
    if not r or not r.get("data"):
        return {}
    out = {}
    for row in r["data"]:
        cp = row.get("citingPaper") or {}
        key = (cp.get("externalIds") or {}).get("DOI") or cp.get("paperId")
        if key:
            out[key] = row
    return out


def cmd_refs(args) -> None:
    oa = openalex_resolve(args.id)
    print(f"# 本文引用了什么（前驱）\n")
    if oa and oa.get("referenced_works"):
        print(f"论文: {title_of(oa)} ({year_of(oa)})　源: OpenAlex")
        print(f"总参考文献数: {oa.get('referenced_works_count')}")
        refs = oa["referenced_works"][: min(args.limit, 50)]
        (print("OpenAlex 只给 W-id，逐条取详情较慢，这里只列前 "
               f"{len(refs)} 条：\n") if False else None)
        printed = 0
        for wid in refs:
            w = openalex_get(f"works/{urllib.parse.quote(wid.split('/')[-1], safe=':')}",
                             {"select": OA_SELECT})
            if not w:
                continue
            printed += 1
            print(f"{printed}. [{title_of(w)}]({link_of(w)}) — {authors_of(w)} · "
                  f"{year_of(w)} · {venue_of(w)} · 被引 {cited_by(w)}")
            if printed >= args.limit:
                break
        if printed:
            print("\n提示：这只是参考文献表的一部分。真正构成本文基础的那 5–8 条，"
                  "要看正文里被反复提到的——结合论文原文的引用语境来挑。")
            return
    print("OpenAlex 未给出参考文献，改用 Semantic Scholar（可能触发限流）：\n")
    s2 = s2_resolve(args.id)
    if not s2:
        print("两个源都没拿到。如实在产物里写「未能取到参考文献列表」，不要编造。")
        sys.exit(2)
    print(f"论文: {title_of(s2)} ({year_of(s2)})　总参考文献数: {s2.get('referenceCount')}\n")
    r = s2_get(f"paper/{s2['paperId']}/references",
               {"fields": "title,year,venue,authors,externalIds,citationCount,abstract",
                "limit": min(args.limit, 100)})
    if not r or not r.get("data"):
        print("S2 也没拿到（限流）。可以设 S2_API_KEY 环境变量后重试，"
              "或改用 OpenAlex 的参考文献列表（上面若已打印则已够用）。")
        sys.exit(2)
    rows = sorted(r["data"],
                  key=lambda x: (x.get("citedPaper") or {}).get("citationCount") or 0,
                  reverse=True)
    _emit(rows, "前驱", args.limit)


def cmd_cites(args) -> None:
    oa = openalex_resolve(args.id)
    s2 = None
    print("# 谁引用了本文（后续）\n")
    print(f"检索时间: {time.strftime('%Y-%m-%d %H:%M')}")
    if oa:
        print(f"论文: {title_of(oa)} ({year_of(oa)})")
        print(f"总被引: {cited_by(oa)}（源: OpenAlex）")
        url = oa.get("cited_by_api_url")
        if url:
            params = {"per-page": min(args.limit, 100),
                      "select": "id,doi,display_name,publication_year,authorships,"
                                "primary_location,cited_by_count,type"}
            if args.influential:
                params["filter"] = "is_retracted:false"
            r = openalex_get("works", {**params, "filter": "cites:" + oa["id"].split("/")[-1]})
            items = (r or {}).get("results") or []
            if args.sort == "citations":
                items = sorted(items, key=lambda w: w.get("cited_by_count") or 0, reverse=True)
            else:
                items = sorted(items, key=lambda w: w.get("publication_year") or 0, reverse=True)
            ctx = {}
            if not args.no_context:
                s2 = s2_resolve(args.id, quiet=True)
                if s2:
                    ctx = _s2_ctx_map(s2["paperId"], args.limit, args.influential)
                    print(f"引用语境原句: {'已获取' if ctx else '未获取（S2 限流）'}")
            print(f"取回 {len(items)} 条，按"
                  f"{'被引量' if args.sort == 'citations' else '年份'}排序。\n")
            for i, w in enumerate(items[: args.limit], 1):
                print(f"\n**[后续{i}] {title_of(w)}**")
                print(f"- 作者: {authors_of(w)}")
                print(f"- 年份: {year_of(w)}　发表处: {venue_of(w)}")
                print(f"- 链接: {link_of(w)}")
                print(f"- 被引: {cited_by(w)}")
                row = ctx.get((w.get("doi") or "").replace("https://doi.org/", "")) or \
                      ctx.get((w.get("id") or "").split("/")[-1])
                if row:
                    for c_ in (row.get("contexts") or [])[:2]:
                        print(f"- 引用语境原句: “{re.sub(r'[[:space:]]+', ' ', c_).strip()[:300]}”")
                    if row.get("intents"):
                        print(f"- 引用意图: {', '.join(row['intents'])}")
            print(f"\nS2 预估真被引: 见 resolve 结果的「有影响力的引用」。")
            print("\n整理产物 D 时请分四类：直接改进 / 应用到新领域 / 复现或质疑 / 仅被提及。"
                  "\n**带引用语境原句的才是真在用它；语境为空的要降级为「仅被提及」。**")
            return
    print("OpenAlex 未给出该论文，改用 Semantic Scholar：\n")
    s2 = s2 or s2_resolve(args.id)
    if not s2:
        print("两个源都没拿到。该文可能过新或过冷门——"
              "此时应如实写「暂未检索到后续引用」，绝不要编造。")
        sys.exit(2)
    print(f"论文: {title_of(s2)} ({year_of(s2)})\n总被引: {s2.get('citationCount')}　"
          f"有影响力的引用: {s2.get('influentialCitationCount')}\n")
    params = {"fields": "title,year,venue,authors,externalIds,contexts,intents,"
                        "citationCount,isInfluential",
              "limit": min(args.limit, 100)}
    if args.influential:
        params["filter"] = "isInfluential:true"
    r = s2_get(f"paper/{s2['paperId']}/citations", params)
    if not r or not r.get("data"):
        print("S2 未返回。设 S2_API_KEY 环境变量可显著提高成功率。")
        sys.exit(2)
    data = sorted(r["data"],
                  key=lambda x: ((x.get("citingPaper") or {}).get("citationCount") or 0)
                  if args.sort == "citations" else
                  ((x.get("citingPaper") or {}).get("year") or 0),
                  reverse=True)
    _emit(data, "后续", args.limit)


def cmd_verify(args) -> None:
    """核验引用是否真实存在。产物交付前跑一遍，防止编造。

    用 Crossref 优先（不限流、DOI 权威），再 OpenAlex，最后 S2。
    """
    print("# 引用核验\n")
    print("| 引用（输入） | 状态 | 核实后的标题 | 年份 | 发表处 | 链接 | 核验源 |")
    print("|---|---|---|---|---|---|---|")
    bad = []
    for raw in args.ids:
        rec, src = None, ""
        cr = crossref_resolve(raw)
        if cr and title_of(cr) != "(无标题)":
            rec, src = cr, "Crossref"
        if rec is None:
            oa = openalex_resolve(raw)
            if oa:
                rec, src = oa, "OpenAlex"
        if rec is None:
            s2 = s2_resolve(raw, quiet=True)
            if s2:
                rec, src = s2, "S2"
        if rec is None:
            bad.append(raw)
            print(f"| {raw} | ❌ 查无实据 | — | — | — | — | — |")
            continue
        t = re.sub(r"\|", "/", str(title_of(rec)))
        print(f"| {raw} | ✅ 存在 | {t} | {year_of(rec)} | {venue_of(rec)} "
              f"| {link_of(rec)} | {src} |")
    print(f"\n共 {len(args.ids)} 条，{len(bad)} 条查无实据。")
    if bad:
        print("\n**查无实据的必须从产物中删除。**宁可少写两条，"
              "也不能让用户拿着假引用去汇报——他也没读过那篇，不会发现。")
        sys.exit(1)
    print("\n全部核实存在。注意：核验的是「这篇文献存在」，"
          "不代表你写的「它说了什么」准确——摘要结论仍需逐条核对。")


def cmd_related(args) -> None:
    print(f"# 相关论文检索：{args.query}\n")
    r = s2_get("paper/search", {"query": args.query, "limit": args.limit,
                                "fields": "title,year,venue,authors,externalIds,"
                                          "citationCount,abstract"})
    data = (r or {}).get("data") or []
    if data:
        for i, p in enumerate(data, 1):
            print(f"{i}. [{title_of(p)}]({link_of(p)}) — {authors_of(p)} · "
                  f"{year_of(p)} · {venue_of(p)} · 被引 {p.get('citationCount')}")
            if p.get("abstract"):
                print(f"   {re.sub(r'[[:space:]]+', ' ', p['abstract'])[:200]}…")
        return
    print("Semantic Scholar 未返回（限流），改用 OpenAlex：\n")
    oa = openalex_get("works", {"search": args.query, "per-page": args.limit,
                                "select": OA_SELECT})
    items = (oa or {}).get("results") or []
    if not items:
        print("两个源都没有结果。换个更短的关键词再试。")
        sys.exit(2)
    for i, w in enumerate(items, 1):
        print(f"{i}. [{title_of(w)}]({link_of(w)}) — {authors_of(w)} · {year_of(w)} "
              f"· {venue_of(w)} · 被引 {cited_by(w)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="解析论文元信息（三源交叉）")
    p.add_argument("id")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("refs", help="本文引用了什么")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("cites", help="谁引用了本文")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--influential", action="store_true",
                   help="只取有影响力的引用（滤掉 related work 里凑数的）")
    p.add_argument("--no-context", action="store_true",
                   help="跳过 S2 语境查询（更快，但拿不到引用语境原句）")
    p.add_argument("--sort", choices=["year", "citations"], default="year")
    p.set_defaults(func=cmd_cites)

    p = sub.add_parser("verify", help="批量核验引用是否真实存在")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("related", help="按标题/关键词找相关论文")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_related)

    args = ap.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
