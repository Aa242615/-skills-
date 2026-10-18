#!/usr/bin/env python3
"""检查汇报 PPT 的常见问题：封面要素、字号、图片清晰度、讲稿备注、文本溢出、
元素重叠、每页校徽、图片越界。

用法:
  python check_deck.py deck.pptx
  python check_deck.py deck.pptx --report check.md
  python check_deck.py deck.pptx --expect-title "Attention Is All You Need"

脚本查不出视觉上的拥挤和错位，产出后仍要人工打开看一遍——但脚本能可靠地
抓住"封面漏了会议名""某页字号掉到 12pt""配图只有 80dpi""某页少了校徽"这类硬伤。

检查的硬性规则（与本 skill 的 SKILL.md / references/ppt-layout.md 一致）：
  1. 封面含英文原题、作者、会议/期刊全称+年份；
  2. 正文不低于 16pt、中文显式设置东亚字体；
  3. 图片等效分辨率 ≥ 150dpi、不变形、完整落在画布内（不越界 = 不被裁切）；
  4. 每页（含封面）右上角必须有中南大学校徽（L≥8.5\"、T≤0.7\" 区域内的图片，
     或形状名含 logo/校徽）；
  5. 每页有讲稿备注；
  6. 无文本溢出、无大面积元素重叠。

依赖: python-pptx  (pip install python-pptx)
退出码: 0 = 无 ERROR；1 = 存在 ERROR（可用于 CI 或交付前卡口）
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

try:
    from pptx import Presentation
    from pptx.util import Emu
except ImportError:
    sys.exit("缺少依赖，请先运行:  pip install python-pptx")

MIN_BODY_PT = 16.0        # 内页正文字号下限
MIN_IMAGE_DPI = 150.0     # 图片等效分辨率下限
CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ＀-￯]")

# 校徽位置规则：内页右上角（与样例 PPT 一致：L=10.42" T=0.18" W=2.38" H=0.66"）
# 允许一定容差，防止用户微调位置后误报。封面同样要求右上角有校徽。
LOGO_LEFT_MIN, LOGO_TOP_MAX = 8.0, 0.7     # 校徽必须出现在右上角区域
LOGO_W_MIN, LOGO_W_MAX = 1.0, 4.5
LOGO_H_MIN, LOGO_H_MAX = 0.2, 1.8
LOGO_NAME_HINTS = ("logo", "校徽", "csu")


@dataclass
class SlideReport:
    index: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)


def emu_to_in(v) -> float:
    return Emu(int(v)).inches


def run_size_pt(run):
    """返回 (pt, 是否显式设置)。未设置时继承版式，脚本无法可靠推断。"""
    if run.font.size is not None:
        return run.font.size.pt, True
    return 18.0, False          # 按默认正文 18pt 估算，并标记为未知


def has_ea_font(run) -> bool:
    rPr = run._r.find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")
    if rPr is None:
        return False
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    return (rPr.find(f"{ns}ea") is not None) or (rPr.find(f"{ns}cs") is not None)


def est_lines(text: str, size_pt: float, width_in: float) -> int:
    """估算换行后的行数。中日韩字符按 1.0em、其余按 0.5em 计宽。"""
    if not text.strip():
        return 0
    width_pt = width_in * 72.0
    lines = 0
    for para in text.split("\n"):
        if not para.strip():
            lines += 1
            continue
        w = 0.0
        n = 1
        for ch in para:
            cw = size_pt * (1.0 if CJK.match(ch) else 0.5)
            if w + cw > width_pt:
                n += 1
                w = cw
            else:
                w += cw
        lines += n
    return lines


def check_text_frame(shape, rep: SlideReport, page_h_in: float) -> None:
    tf = shape.text_frame
    text = tf.text
    if not text.strip():
        return
    box_w = emu_to_in(shape.width)
    box_h = emu_to_in(shape.height)
    size, explicit = 18.0, True
    sizes: list[float] = []
    missing_ea = False
    for para in tf.paragraphs:
        for run in para.runs:
            s, expl = run_size_pt(run)
            sizes.append(s)
            if not expl:
                explicit = False
            if run.text.strip() and CJK.search(run.text) and not has_ea_font(run):
                missing_ea = True
    if sizes:
        size = max(sizes)
    head = f"形状「{text[:18].strip()}…」" if len(text) > 18 else f"形状「{text.strip()}」"

    if not explicit:
        rep.infos.append(f"{head} 未显式设置字号，继承版式（请目视确认 ≥ {MIN_BODY_PT:.0f}pt）")
    if missing_ea:
        rep.warnings.append(f"{head} 含中文但未设置东亚字体，可能回落到宋体")

    # 小字号：给标题/脚注留余地，只报真正过小的
    small = [s for s in sizes if s < MIN_BODY_PT]
    if small and len(small) == len(sizes):
        rep.errors.append(
            f"{head} 全部字号 {min(small):.0f}pt 低于下限 {MIN_BODY_PT:.0f}pt，投影看不清")

    need = est_lines(text, size, box_w) * size * 1.35 / 72.0     # 行距按 1.35 估
    if need > box_h * 1.05:
        rep.warnings.append(
            f"{head} 文本可能溢出：估算需 {need:.2f}\" 高，文本框仅 {box_h:.2f}\""
            f"（{est_lines(text, size, box_w)} 行 × {size:.0f}pt）")
    if box_h > page_h_in * 0.95 or emu_to_in(shape.top) + box_h > page_h_in + 0.01:
        rep.errors.append(f"{head} 超出页面下边界（bottom={emu_to_in(shape.top) + box_h:.2f}\"）")


def check_picture(shape, rep: SlideReport, page_w_in: float, page_h_in: float) -> None:
    try:
        img = shape.image
    except Exception:
        return
    w_in, h_in = emu_to_in(shape.width), emu_to_in(shape.height)
    if w_in <= 0 or h_in <= 0:
        return
    dpi_x, dpi_y = img.size[0] / w_in, img.size[1] / h_in
    dpi = min(dpi_x, dpi_y)
    name = getattr(img, "filename", None) or f"{img.size[0]}x{img.size[1]}px"
    if dpi < MIN_IMAGE_DPI:
        rep.errors.append(
            f"图片 {name} 显示尺寸 {w_in:.2f}x{h_in:.2f}\"，等效仅 {dpi:.0f} dpi"
            f"（需 ≥ {MIN_IMAGE_DPI:.0f}）。换原图或按 300dpi 重新渲染该页")
    # 宽高比畸变检测
    native = img.size[0] / img.size[1]
    shown = w_in / h_in
    if abs(native - shown) / native > 0.03:
        rep.warnings.append(
            f"图片 {name} 宽高比被改动（原始 {native:.2f}，当前 {shown:.2f}），可能被拉伸变形")
    if w_in < 2.5 and not is_logo_shape(shape, page_w_in):
        rep.warnings.append(f"图片 {name} 宽度仅 {w_in:.2f}\"，投影偏小")
    # 越界检测：图片必须完整落在画布内，否则放映/导出时会被裁切
    left_in, top_in = emu_to_in(shape.left), emu_to_in(shape.top)
    right_in, bottom_in = left_in + w_in, top_in + h_in
    if left_in < -0.01 or top_in < -0.01 or right_in > page_w_in + 0.01 or bottom_in > page_h_in + 0.01:
        rep.errors.append(
            f"图片 {name} 越出页面（L={left_in:.2f} T={top_in:.2f} R={right_in:.2f} "
            f"B={bottom_in:.2f}，画布 {page_w_in:.2f}x{page_h_in:.2f}），放映时会被裁切。"
            "请整体缩小或内移图片")
    elif right_in > page_w_in - 0.05 or bottom_in > page_h_in - 0.05:
        rep.warnings.append(
            f"图片 {name} 贴页边（R={right_in:.2f} B={bottom_in:.2f}），请目视确认导出 PDF 时未被裁切")


def is_logo_shape(shape, page_w_in: float) -> bool:
    """判断某图片是否可视为右上角校徽：位于右上角区域，或形状名含 logo/校徽。"""
    if shape.shape_type != 13 and not hasattr(shape, "image"):
        return False
    try:
        _ = shape.image
    except Exception:
        return False
    left_in, top_in = emu_to_in(shape.left), emu_to_in(shape.top)
    w_in, h_in = emu_to_in(shape.width), emu_to_in(shape.height)
    name = (shape.name or "").lower()
    if any(hint in name for hint in LOGO_NAME_HINTS):
        return True
    if w_in <= 0 or h_in <= 0:
        return False
    right_in = left_in + w_in
    in_top_right = (left_in >= LOGO_LEFT_MIN and top_in <= LOGO_TOP_MAX
                    and right_in <= page_w_in
                    and LOGO_W_MIN <= w_in <= LOGO_W_MAX
                    and LOGO_H_MIN <= h_in <= LOGO_H_MAX)
    return in_top_right


def check_logo(slide, rep: SlideReport, page_w_in: float) -> None:
    """每一页（含封面）右上角必须有中南大学校徽。"""
    for sh in slide.shapes:
        if is_logo_shape(sh, page_w_in):
            return
    rep.errors.append(
        "右上角缺少中南大学校徽——每一页（含封面）都必须放"
        "references/assets/csu-logo.png（右上角 L≈10.42\" T≈0.18\" W≈2.38\" H≈0.66\"）")


def check_cover(prs, slides, report: dict[int, SlideReport],
                expect_title: str | None) -> None:
    """封面必须含：英文原题、作者、会议/期刊全称+年份。"""
    if not slides:
        return
    text = " ".join(s.text_frame.text for s in slides[0].shapes
                    if s.has_text_frame)
    rep = report[slides[0].slide_id]
    latin_words = re.findall(r"[A-Za-z][A-Za-z\-']+", text)
    if len(latin_words) < 4:
        rep.errors.append("封面几乎没有英文——封面必须含论文英文原题")
    if expect_title:
        norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        if norm(expect_title) not in norm(text):
            rep.errors.append(f"封面上找不到期望的英文标题「{expect_title}」")
    if not re.search(r"(19|20)\d{2}", text):
        rep.warnings.append("封面未出现 4 位年份，发表年份请写明")
    venue_kw = ("conference", "proceedings", "journal", "transactions",
                "symposium", "workshop", "meeting", "letters", "cvpr", "iccv",
                "eccv", "neurips", "nips", "icml", "iclr", "acl", "emnlp",
                "aaai", "ijcai", "kdd", "sigir", "www", "mm", "tnnls", "tpami",
                "期刊", "会议", "学报")
    if not any(k in text.lower() for k in venue_kw):
        rep.warnings.append("封面未见会议/期刊名（请写全称，缩写需附全称）")
    if "et al" not in text.lower() and len(latin_words) < 8:
        rep.warnings.append("封面的作者信息可能不全（作者多时写前 3 位 + et al.）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pptx")
    ap.add_argument("--report", help="把完整报告写入此 markdown 文件")
    ap.add_argument("--expect-title", help="期望出现在封面的英文标题，用于校验")
    args = ap.parse_args()

    prs = Presentation(args.pptx)
    page_w, page_h = emu_to_in(prs.slide_width), emu_to_in(prs.slide_height)
    ratio = page_w / page_h
    report: dict[int, SlideReport] = {}

    global_errors: list[str] = []
    if abs(ratio - 16 / 9) > 0.02:
        global_errors.append(
            f"画布比例 {page_w:.2f}x{page_h:.2f}\"（{ratio:.2f}:1），不是标准 16:9。"
            "投影时会留黑边或裁切")
    if page_w < 12:
        global_errors.append(f"画布宽度仅 {page_w:.2f}\"，非标准 13.333\"（16:9 英寸制）")

    slides = list(prs.slides)
    for i, slide in enumerate(slides, 1):
        rep = SlideReport(index=i)
        report[slide.slide_id] = rep
        n_pic = n_txt = 0
        check_logo(slide, rep, page_w)
        for shape in slide.shapes:
            if shape.shape_type == 13 or hasattr(shape, "image") and \
                    shape.shape_type is not None and str(shape.shape_type).startswith("PICTURE"):
                check_picture(shape, rep, page_w, page_h)
                n_pic += 1
            if shape.has_text_frame and shape.text_frame.text.strip():
                check_text_frame(shape, rep, page_h)
                n_txt += 1
            if shape.has_table:
                for r in shape.table.rows:
                    for c in r.cells:
                        for p in c.text_frame.paragraphs:
                            for run in p.runs:
                                s, expl = run_size_pt(run)
                                if expl and s < 12:
                                    rep.warnings.append(
                                        f"表格内字号 {s:.0f}pt 过小（表格建议 ≥ 14pt）")
                                    break
        if n_txt == 0 and n_pic == 0:
            rep.warnings.append("空白页，或内容全在版式占位符里未取到")
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        if not notes:
            rep.warnings.append("缺少讲稿备注（每页应写 3–5 句口播稿）")
        if i > 1 and len(notes.split("\n")) > 1 and len(notes) < 20:
            rep.infos.append("讲稿备注过短")

    check_cover(prs, slides, report, args.expect_title)

    # ---- 元素重叠（启发式：只报明显的大面积交叠）----
    for slide in slides:
        rep = report[slide.slide_id]
        boxes = []
        for sh in slide.shapes:
            if sh.width and sh.height and sh.width > 0 and sh.height > 0:
                boxes.append((sh, emu_to_in(sh.left), emu_to_in(sh.top),
                              emu_to_in(sh.width), emu_to_in(sh.height)))
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                s1, x1, y1, w1, h1 = boxes[a]
                s2, x2, y2, w2, h2 = boxes[b]
                ox = max(0.0, min(x1 + w1, x2 + w2) - max(x1, x2))
                oy = max(0.0, min(y1 + h1, y2 + h2) - max(y1, y2))
                if ox * oy <= 0:
                    continue
                area = min(w1 * h1, w2 * h2)
                if area > 0 and ox * oy / area > 0.55:
                    rep.warnings.append("检测到两个元素大面积重叠，请目视确认不是压字")

    # ---- 输出 ----
    lines = [f"# PPT 检查报告：{args.pptx}", "",
             f"- 页数：{len(slides)}　画布：{page_w:.2f} x {page_h:.2f} 英寸",
             f"- ERROR {sum(len(r.errors) for r in report.values()) + len(global_errors)} 项，"
             f"WARNING {sum(len(r.warnings) for r in report.values())} 项", ""]
    if global_errors:
        lines += ["## 全局问题", ""] + [f"- ERROR: {e}" for e in global_errors] + [""]
    for i, slide in enumerate(slides, 1):
        rep = report[slide.slide_id]
        if not (rep.errors or rep.warnings):
            lines.append(f"## 第 {i} 页 — 通过")
            for x in rep.infos:
                lines.append(f"- INFO: {x}")
            lines.append("")
            continue
        lines.append(f"## 第 {i} 页")
        lines += [f"- ERROR: {e}" for e in rep.errors]
        lines += [f"- WARNING: {w}" for w in rep.warnings]
        lines += [f"- INFO: {x}" for x in rep.infos]
        lines.append("")

    text = "\n".join(lines)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n报告已写入 {args.report}")

    n_err = sum(len(r.errors) for r in report.values()) + len(global_errors)
    print("\n注意：脚本查不出视觉拥挤与错位，交付前请务必打开 PPT 目视过一遍。")
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
