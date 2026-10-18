#!/usr/bin/env python3
"""从论文 PDF 里抽正文、内嵌图片、整页渲染图。做汇报材料时用它取原始素材。

用法:
  python extract_pdf.py text    paper.pdf [-o out.txt] [--pages 1-10]
  python extract_pdf.py figures paper.pdf [-o figs/] [--min-size 120]
  python extract_pdf.py pages   paper.pdf [-o pages/] [--dpi 300] [--pages 1,3-5]
  python extract_pdf.py info    paper.pdf

依赖: PyMuPDF  (pip install pymupdf)
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("缺少依赖，请先运行:  pip install pymupdf")


def parse_pages(spec: str | None, total: int) -> list[int]:
    """把 '1,3-5' 解析成 [1,3,4,5]（页码从 1 开始，返回 0-based 索引）。"""
    if not spec:
        return list(range(total))
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    # 去重、排序、裁剪到有效范围
    return sorted({p - 1 for p in out if 1 <= p <= total})


def cmd_info(doc: fitz.Document, args) -> None:
    meta = doc.metadata or {}
    print(f"页数        : {doc.page_count}")
    print(f"标题        : {meta.get('title', '') or '(未写入)'}")
    print(f"作者        : {meta.get('author', '') or '(未写入)'}")
    print(f"主题        : {meta.get('subject', '') or '(未写入)'}")
    print(f"创建工具    : {meta.get('creator', '') or '(未写入)'}")
    p = doc[0]
    print(f"首页尺寸    : {p.rect.width:.1f} x {p.rect.height:.1f} pt")
    n_img = sum(len(doc[i].get_images(full=True)) for i in range(doc.page_count))
    print(f"内嵌图片总数: {n_img}")


def cmd_text(doc: fitz.Document, args) -> None:
    pages = parse_pages(args.pages, doc.page_count)
    chunks = []
    for i in pages:
        page = doc[i]
        chunks.append(f"\n\n{'=' * 20} 第 {i + 1} 页 {'=' * 20}\n")
        chunks.append(page.get_text("text"))
    text = "".join(chunks)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入 {args.out}（{len(text)} 字符，{len(pages)} 页）")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)


def cmd_figures(doc: fitz.Document, args) -> None:
    """抽取内嵌位图。矢量图（大量论文框架图是矢量绘制的）抽不出来，
    这种情况改用 pages 模式渲染整页再裁剪。"""
    outdir = args.out or "figs"
    os.makedirs(outdir, exist_ok=True)
    seen: set[int] = set()
    saved, skipped = 0, 0
    for pno in range(doc.page_count):
        for idx, info in enumerate(doc[pno].get_images(full=True)):
            xref = info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception as e:  # 损坏或不受支持的颜色空间
                print(f"  ! 跳过 xref={xref}: {e}")
                continue
            if pix.n - pix.alpha >= 4:          # CMYK / 带遮罩，转 RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if pix.width < args.min_size or pix.height < args.min_size:
                skipped += 1
                continue
            name = f"p{pno + 1:03d}_x{xref}_{pix.width}x{pix.height}.png"
            pix.save(os.path.join(outdir, name))
            saved += 1
            print(f"  {name}")
    print(f"\n已抽取 {saved} 张到 {outdir}/（因过小跳过 {skipped} 张）")
    if saved == 0:
        print("提示：一张都没抽到，说明论文里的图多为矢量绘制。"
              "请改用 pages 模式渲染整页（--dpi 300）后再裁剪。")


def cmd_pages(doc: fitz.Document, args) -> None:
    outdir = args.out or "pages"
    os.makedirs(outdir, exist_ok=True)
    pages = parse_pages(args.pages, doc.page_count)
    if args.dpi < 150:
        print(f"警告：--dpi {args.dpi} 偏低，投影或打印会发虚，建议 ≥ 200。")
    zoom = args.dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i in pages:
        out = os.path.join(outdir, f"page_{i + 1:03d}.png")
        doc[i].get_pixmap(matrix=mat, alpha=False).save(out)
        print(f"  {out}")
    print(f"\n已渲染 {len(pages)} 页到 {outdir}/（{args.dpi} dpi）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("pdf", help="PDF 路径")
        p.add_argument("-o", "--out", help="输出文件或目录")
        p.add_argument("--pages", help="页码范围，如 '1,3-5'（默认全部）")

    common(sub.add_parser("info", help="打印元信息"))
    common(sub.add_parser("text", help="抽正文（带页码分隔）"))
    p_fig = sub.add_parser("figures", help="抽内嵌图片")
    common(p_fig)
    p_fig.add_argument("--min-size", type=int, default=120,
                       help="小于此边长的图片丢弃（默认 120px，可滤掉公式小图标）")
    p_pg = sub.add_parser("pages", help="按 dpi 渲染整页为 PNG")
    common(p_pg)
    p_pg.add_argument("--dpi", type=int, default=300, help="渲染精度，默认 300")

    args = ap.parse_args()
    if not os.path.isfile(args.pdf):
        sys.exit(f"找不到文件: {args.pdf}")
    doc = fitz.open(args.pdf)
    try:
        {"info": cmd_info, "text": cmd_text,
         "figures": cmd_figures, "pages": cmd_pages}[args.cmd](doc, args)
    finally:
        doc.close()


if __name__ == "__main__":
    main()
