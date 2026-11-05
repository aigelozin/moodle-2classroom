"""Разбор резервной копии курса Moodle (.mbz) в нейтральную модель.

Поддерживаются бэкапы Moodle 3.x и 4.x (tar.gz и старый zip).
Ничего не обращается к сети: на выходе — dataclass-ы Course/Section/Activity/Quiz.
"""
from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional


# ---------------------------------------------------------------- HTML -> text
class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "blockquote", "pre", "table", "ul", "ol", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.media: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.links.append(href)
        if tag in ("source", "video", "audio", "iframe", "embed"):
            src = dict(attrs).get("src")
            if src and src.startswith("http"):
                self.media.append(src)
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"[изображение{': ' + alt if alt else ''}]")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: Optional[str]) -> str:
    if not html:
        return ""
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_links(html: Optional[str]) -> list[str]:
    """Встроенные видео/аудио (video/audio/source/iframe) первыми, затем обычные ссылки."""
    if not html:
        return []
    p = _TextExtractor()
    p.feed(html)
    return p.media + p.links


# ---------------------------------------------------------------- model
@dataclass
class MFile:
    id: str
    contenthash: str
    contextid: str
    component: str
    filearea: str
    itemid: str
    filepath: str
    filename: str
    mimetype: str
    filesize: int
    path: str  # путь к содержимому на диске (после распаковки)


@dataclass
class Answer:
    text: str
    fraction: float
    feedback: str = ""


@dataclass
class Question:
    id: str
    qtype: str
    name: str
    text_html: str
    defaultmark: float
    answers: list[Answer] = field(default_factory=list)
    single: bool = True            # multichoice: один верный ответ
    matches: list[tuple[str, str]] = field(default_factory=list)  # match: (вопрос, ответ)
    general_feedback: str = ""


@dataclass
class QuizSlot:
    slot: int
    page: int
    maxmark: float
    question: Optional[Question]
    note: str = ""   # почему вопрос не найден / random


@dataclass
class Activity:
    cmid: str
    modname: str
    name: str
    intro_html: str = ""
    visible: bool = True
    content_html: str = ""         # page
    external_url: str = ""         # url
    files: list[MFile] = field(default_factory=list)  # resource / folder / вложения
    intro_files: list[MFile] = field(default_factory=list)
    content_files: list[MFile] = field(default_factory=list)  # картинки страницы page
    quiz_slots: list[QuizSlot] = field(default_factory=list)
    quiz_grade: float = 0.0
    quiz_sumgrades: float = 0.0
    contextid: str = ""


@dataclass
class Section:
    id: str
    number: int
    name: str
    summary_html: str
    visible: bool
    activities: list[Activity] = field(default_factory=list)


@dataclass
class Course:
    fullname: str
    shortname: str
    summary_html: str
    moodle_release: str
    sections: list[Section]
    source: str
    workdir: str
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def _t(el, path, default=""):
    if el is None:
        return default
    x = el.find(path)
    if x is None or x.text is None:
        return default
    return x.text


def _f(el, path, default=0.0):
    try:
        return float(_t(el, path, str(default)))
    except ValueError:
        return default


def extract_mbz(mbz_path: str, dest: Optional[str] = None) -> str:
    dest = dest or tempfile.mkdtemp(prefix="mbz_")
    if zipfile.is_zipfile(mbz_path):
        with zipfile.ZipFile(mbz_path) as z:
            for n in z.namelist():
                target = os.path.realpath(os.path.join(dest, n))
                if not target.startswith(os.path.realpath(dest) + os.sep) and target != os.path.realpath(dest):
                    raise ValueError(f"Небезопасный путь в архиве: {n}")
            z.extractall(dest)
    else:
        with tarfile.open(mbz_path, "r:*") as t:
            try:
                t.extractall(dest, filter="data")
            except TypeError:  # Python < 3.12
                for m in t.getmembers():
                    target = os.path.realpath(os.path.join(dest, m.name))
                    if not target.startswith(os.path.realpath(dest)):
                        raise ValueError(f"Небезопасный путь в архиве: {m.name}")
                t.extractall(dest)
    return dest


# ---------------------------------------------------------------- questions
def _parse_question(q) -> Question:
    qtype = _t(q, "qtype")
    question = Question(
        id=q.get("id", ""),
        qtype=qtype,
        name=_t(q, "name"),
        text_html=_t(q, "questiontext"),
        defaultmark=_f(q, "defaultmark", 1.0),
        general_feedback=_t(q, "generalfeedback"),
    )
    for ans in q.iter("answer"):
        if ans.find("answertext") is None:
            continue
        question.answers.append(Answer(
            text=_t(ans, "answertext"),
            fraction=_f(ans, "fraction"),
            feedback=_t(ans, "feedback"),
        ))
    mc = q.find(".//multichoice")
    if mc is not None:
        question.single = _t(mc, "single", "1") == "1"
    for m in q.iter("match"):
        qt = html_to_text(_t(m, "questiontext"))
        at = _t(m, "answertext")
        question.matches.append((qt, at))
    return question


def parse_questions(questions_xml: str):
    """Возвращает (by_question_id, by_bank_entry_id->latest Question)."""
    by_qid: dict[str, Question] = {}
    by_entry: dict[str, Question] = {}
    if not os.path.exists(questions_xml):
        return by_qid, by_entry
    root = ET.parse(questions_xml).getroot()
    # Moodle 4.x: question_bank_entry -> question_versions -> question_version -> questions -> question
    for entry in root.iter("question_bank_entry"):
        best, best_ver = None, -1
        for ver in entry.iter("question_version"):
            v = int(_f(ver, "version", 0))
            for q in ver.iter("question"):
                qq = _parse_question(q)
                by_qid[qq.id] = qq
                if v >= best_ver:
                    best, best_ver = qq, v
        if best is not None:
            by_entry[entry.get("id", "")] = best
    # Moodle 3.x: question_category -> questions -> question
    for q in root.iter("question"):
        qid = q.get("id", "")
        if qid and qid not in by_qid:
            by_qid[qid] = _parse_question(q)
    return by_qid, by_entry


# ---------------------------------------------------------------- main parse
def parse_mbz(mbz_path: str, workdir: Optional[str] = None) -> Course:
    root_dir = extract_mbz(mbz_path, workdir)
    warnings: list[str] = []

    mb = ET.parse(os.path.join(root_dir, "moodle_backup.xml")).getroot()
    info = mb.find("information")
    release = _t(info, "moodle_release")

    # файлы
    files_by_ctx: dict[tuple, list[MFile]] = {}
    files_xml = os.path.join(root_dir, "files.xml")
    if os.path.exists(files_xml):
        for f in ET.parse(files_xml).getroot().iter("file"):
            fname = _t(f, "filename")
            if fname == "." or not fname:
                continue
            h = _t(f, "contenthash")
            mf = MFile(
                id=f.get("id", ""), contenthash=h, contextid=_t(f, "contextid"),
                component=_t(f, "component"), filearea=_t(f, "filearea"),
                itemid=_t(f, "itemid"), filepath=_t(f, "filepath", "/"),
                filename=fname, mimetype=_t(f, "mimetype", "application/octet-stream"),
                filesize=int(_f(f, "filesize")),
                path=os.path.join(root_dir, "files", h[:2], h),
            )
            if not os.path.exists(mf.path):
                warnings.append(f"Файл {fname} отсутствует в бэкапе (бэкап без файлов?)")
                continue
            files_by_ctx.setdefault((mf.contextid, mf.component, mf.filearea), []).append(mf)

    def files_for(ctx, component, area):
        seen, out = set(), []
        for mf in files_by_ctx.get((ctx, component, area), []):
            key = (mf.filepath, mf.filename)
            if key not in seen:
                seen.add(key)
                out.append(mf)
        return sorted(out, key=lambda m: (m.filepath, m.filename))

    by_qid, by_entry = parse_questions(os.path.join(root_dir, "questions.xml"))

    # курс
    course_xml = ET.parse(os.path.join(root_dir, "course", "course.xml")).getroot()
    fullname = _t(course_xml, "fullname") or _t(info, "original_course_fullname")
    shortname = _t(course_xml, "shortname") or _t(info, "original_course_shortname")
    summary = _t(course_xml, "summary")

    # активности по директориям из moodle_backup.xml
    acts: dict[str, Activity] = {}
    for a in mb.findall("information/contents/activities/activity"):
        d = _t(a, "directory")
        mod = _t(a, "modulename")
        cmid = _t(a, "moduleid")
        adir = os.path.join(root_dir, d)
        mxml = os.path.join(adir, "module.xml")
        visible = True
        if os.path.exists(mxml):
            visible = _t(ET.parse(mxml).getroot(), "visible", "1") == "1"
        axml = os.path.join(adir, f"{mod}.xml")
        if not os.path.exists(axml):
            warnings.append(f"Нет {mod}.xml для активности {cmid}")
            continue
        aroot = ET.parse(axml).getroot()
        ctx = aroot.get("contextid", "")
        body = aroot.find(mod)
        act = Activity(cmid=cmid, modname=mod, name=_t(body, "name") or _t(a, "title"),
                       intro_html=_t(body, "intro"), visible=visible, contextid=ctx)
        comp = f"mod_{mod}"
        act.intro_files = files_for(ctx, comp, "intro")
        if mod == "page":
            act.content_html = _t(body, "content")
            act.content_files = files_for(ctx, comp, "content")
        elif mod in ("resource", "folder"):
            act.files = files_for(ctx, comp, "content")
        elif mod == "url":
            act.external_url = _t(body, "externalurl")
        elif mod == "book":
            chapters = []
            for ch in body.iter("chapter"):
                chapters.append((int(_f(ch, "pagenum")), _t(ch, "title"), _t(ch, "content")))
            chapters.sort()
            act.content_html = "".join(f"<h2>{t}</h2>{c}" for _, t, c in chapters)
            act.content_files = files_for(ctx, "mod_book", "chapter")
        elif mod == "quiz":
            act.quiz_grade = _f(body, "grade")
            act.quiz_sumgrades = _f(body, "sumgrades")
            for qi in body.iter("question_instance"):
                slot = int(_f(qi, "slot"))
                page = int(_f(qi, "page", 1))
                maxmark = _f(qi, "maxmark", 1.0)
                q, note = None, ""
                ref = qi.find("question_reference")
                qid = _t(qi, "questionid")
                if ref is not None and _t(ref, "questionbankentryid"):
                    q = by_entry.get(_t(ref, "questionbankentryid"))
                elif qi.find("question_set_reference") is not None:
                    note = "случайный вопрос из категории (random) — в Forms не переносится"
                elif qid:
                    q = by_qid.get(qid)
                if q is not None and q.qtype == "random":
                    q, note = None, "случайный вопрос из категории (random) — в Forms не переносится"
                if q is None and not note:
                    note = "вопрос не найден в бэкапе (бэкап без банка вопросов?)"
                act.quiz_slots.append(QuizSlot(slot, page, maxmark, q, note))
            act.quiz_slots.sort(key=lambda s: s.slot)
        acts[cmid] = act

    # секции в порядке номера, активности в порядке sequence
    sections: list[Section] = []
    for s in mb.findall("information/contents/sections/section"):
        d = _t(s, "directory")
        sxml = os.path.join(root_dir, d, "section.xml")
        if not os.path.exists(sxml):
            continue
        sr = ET.parse(sxml).getroot()
        num = int(_f(sr, "number"))
        sec = Section(id=sr.get("id", ""), number=num, name=_t(sr, "name"),
                      summary_html=_t(sr, "summary"),
                      visible=_t(sr, "visible", "1") == "1")
        seq = [x for x in _t(sr, "sequence").split(",") if x]
        for cmid in seq:
            if cmid in acts:
                sec.activities.append(acts.pop(cmid))
        sections.append(sec)
    sections.sort(key=lambda s: s.number)
    if acts:  # активности, не попавшие в sequence
        orphan = Section(id="orphan", number=9999, name="Прочее", summary_html="", visible=True,
                         activities=list(acts.values()))
        sections.append(orphan)
        warnings.append(f"{len(acts)} активностей без раздела помещены в «Прочее»")

    return Course(fullname=fullname, shortname=shortname, summary_html=summary,
                  moodle_release=release, sections=sections, source=mbz_path,
                  workdir=root_dir, warnings=warnings)


def cleanup(course: Course):
    shutil.rmtree(course.workdir, ignore_errors=True)
