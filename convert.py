"""Превращает модель курса Moodle в план для Google Classroom.

План — чистые данные (без сетевых вызовов), поэтому его можно показать
в режиме dry-run и проверить до реального переноса.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from mbz_parser import Activity, Course, MFile, Question, html_links, html_to_text

YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([\w-]{11})")

# Ограничения API
MAX_TITLE = 300        # практичный предел для заголовков Classroom
MAX_TOPIC = 100
MAX_DESC = 30000
MAX_FORM_TEXT = 300    # заголовки вопросов/вариантов в Forms без переносов строк


def youtube_id(url: str) -> Optional[str]:
    m = YOUTUBE_RE.search(url or "")
    return m.group(1) if m else None


def one_line(s: str, limit: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------- plan model
@dataclass
class Attachment:
    kind: str                      # drive_file | link | youtube | html_doc | form
    title: str = ""
    file: Optional[MFile] = None
    url: str = ""
    video_id: str = ""
    html: str = ""
    form: Optional["FormSpec"] = None


@dataclass
class Item:
    key: str                       # стабильный ключ для повторных запусков
    kind: str                      # material | assignment
    title: str
    description: str
    draft: bool = False
    max_points: float = 0
    attachments: list[Attachment] = field(default_factory=list)
    source: str = ""               # тип активности Moodle


@dataclass
class TopicPlan:
    key: str
    name: str
    items: list[Item] = field(default_factory=list)


@dataclass
class CoursePlan:
    name: str
    section: str
    description: str
    topics: list[TopicPlan]
    skipped: list[str]
    warnings: list[str]


@dataclass
class FormSpec:
    title: str
    description: str
    requests: list[dict]
    total_points: int
    warnings: list[str]


# ---------------------------------------------------------------- quiz -> Form
def _opt_values(texts: list[str]) -> list[str]:
    out, seen = [], set()
    for t in texts:
        v = one_line(html_to_text(t), MAX_FORM_TEXT) or "—"
        base, n = v, 2
        while v in seen:
            v = f"{base} ({n})"
            n += 1
        seen.add(v)
        out.append(v)
    return out


def _question_items(q: Question, points: int, warn: list[str]) -> list[dict]:
    """Возвращает список item-ов Forms для одного вопроса Moodle."""
    text = html_to_text(q.text_html)
    if "[изображение" in text:
        warn.append(f"«{q.name}»: картинка в тексте вопроса не переносится — добавьте вручную")
    title = one_line(text.split("\n")[0] if text else q.name, MAX_FORM_TEXT) or q.name
    description = text if ("\n" in text or len(text) > MAX_FORM_TEXT) else ""
    fb = html_to_text(q.general_feedback)

    def grading(correct: list[str] | None, pts=points, choice=False):
        g = {"pointValue": pts}
        if correct:
            g["correctAnswers"] = {"answers": [{"value": c} for c in correct]}
        if fb:
            # для автопроверяемых вариантов Forms принимает только whenRight/whenWrong
            if choice and correct:
                g["whenRight"] = {"text": fb[:2000]}
                g["whenWrong"] = {"text": fb[:2000]}
            else:
                g["generalFeedback"] = {"text": fb[:2000]}
        return g

    def qitem(question: dict, t=title, d=description):
        item = {"title": t, "questionItem": {"question": question}}
        if d:
            item["description"] = d[:4000]
        return item

    qt = q.qtype
    if qt in ("multichoice", "truefalse"):
        vals = _opt_values([a.text for a in q.answers])
        if qt == "truefalse":
            vals = ["Верно" if html_to_text(a.text).lower() in ("true", "верно", "истина") else
                    "Неверно" if html_to_text(a.text).lower() in ("false", "неверно", "ложь") else v
                    for a, v in zip(q.answers, vals)]
        if q.single or qt == "truefalse":
            best = max((a.fraction for a in q.answers), default=0)
            correct = [v for a, v in zip(q.answers, vals) if a.fraction == best and best > 0][:1]
            if any(0 < a.fraction < best for a in q.answers):
                warn.append(f"«{q.name}»: частичные баллы за варианты в Forms не поддерживаются")
            ctype = "RADIO"
        else:
            correct = [v for a, v in zip(q.answers, vals) if a.fraction > 0]
            if any(a.fraction < 0 for a in q.answers):
                warn.append(f"«{q.name}»: штрафы за неверные варианты в Forms не поддерживаются")
            ctype = "CHECKBOX"
        return [qitem({"required": False, "grading": grading(correct, choice=True),
                       "choiceQuestion": {"type": ctype, "options": [{"value": v} for v in vals]}})]

    if qt in ("shortanswer", "numerical"):
        correct = [one_line(html_to_text(a.text), MAX_FORM_TEXT) for a in q.answers
                   if a.fraction >= 1 and a.text.strip() and "*" not in a.text]
        if any("*" in a.text for a in q.answers):
            warn.append(f"«{q.name}»: шаблоны с * в Forms не поддерживаются — проверьте ответы")
        if qt == "numerical":
            warn.append(f"«{q.name}»: числовой ответ проверяется как точное совпадение текста (без погрешности)")
        return [qitem({"required": False, "grading": grading(correct or None),
                       "textQuestion": {"paragraph": False}})]

    if qt == "essay":
        return [qitem({"required": False, "grading": grading(None),
                       "textQuestion": {"paragraph": True}})]

    if qt == "match" and q.matches:
        subs = [(s, a) for s, a in q.matches if s]
        answers = _opt_values(sorted({a for _, a in q.matches}))
        amap = dict(zip(sorted({a for _, a in q.matches}), answers))
        per = max(1, round(points / max(1, len(subs))))
        items = []
        for i, (stem, ans) in enumerate(subs, 1):
            items.append(qitem(
                {"required": False, "grading": grading([amap[ans]], per, choice=True),
                 "choiceQuestion": {"type": "DROP_DOWN", "options": [{"value": v} for v in answers]}},
                t=one_line(f"{title} — {stem}", MAX_FORM_TEXT),
                d=description if i == 1 else ""))
        return items

    if qt == "description":
        item = {"title": title, "textItem": {}}
        if description:
            item["description"] = description[:4000]
        return [item]

    warn.append(f"«{q.name}»: тип {qt} не поддерживается Forms — перенесён как развёрнутый ответ, проверьте вручную")
    return [qitem({"required": False, "grading": grading(None),
                   "textQuestion": {"paragraph": True}})]


def quiz_to_form(act: Activity) -> FormSpec:
    warn: list[str] = []
    items: list[dict] = []
    last_page = None
    total = 0
    for s in act.quiz_slots:
        if s.question is None:
            warn.append(f"Слот {s.slot}: {s.note}")
            continue
        if last_page is not None and s.page != last_page:
            items.append({"title": f"Страница {s.page}", "pageBreakItem": {}})
        last_page = s.page
        pts = 0 if s.question.qtype == "description" else max(1, round(s.maxmark))
        if s.maxmark and abs(s.maxmark - round(s.maxmark)) > 1e-6 and s.question.qtype != "description":
            warn.append(f"«{s.question.name}»: балл {s.maxmark:g} округлён до {pts} (Forms — только целые)")
        new = _question_items(s.question, pts, warn)
        for it in new:
            g = it.get("questionItem", {}).get("question", {}).get("grading")
            if g:
                total += g["pointValue"]
        items.extend(new)

    requests = [{"updateSettings": {"settings": {"quizSettings": {"isQuiz": True}},
                                    "updateMask": "quizSettings.isQuiz"}}]
    desc = html_to_text(act.intro_html)
    if desc:
        requests.append({"updateFormInfo": {"info": {"description": desc[:4000]},
                                            "updateMask": "description"}})
    for i, it in enumerate(items):
        requests.append({"createItem": {"item": it, "location": {"index": i}}})
    return FormSpec(title=one_line(act.name, MAX_TITLE), description=desc,
                    requests=requests, total_points=total, warnings=warn)


# ---------------------------------------------------------------- page -> HTML doc
def inline_images(html: str, files: list[MFile]) -> tuple[str, list[MFile]]:
    """Подставляет картинки @@PLUGINFILE@@ как data-URI; остальные файлы возвращает отдельно."""
    other: list[MFile] = []
    for f in files:
        ref_variants = {f"@@PLUGINFILE@@{f.filepath}{f.filename}",
                        f"@@PLUGINFILE@@{f.filepath}{urllib.parse.quote(f.filename)}"}
        if f.mimetype.startswith("image/") and f.filesize < 5_000_000:
            with open(f.path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
            uri = f"data:{f.mimetype};base64,{data}"
            for r in ref_variants:
                html = html.replace(r, uri)
        else:
            other.append(f)
    html = re.sub(r"@@PLUGINFILE@@[^\"'\s>]*", "#", html)
    return html, other


def _wrap_html(title: str, body: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body>{body}</body></html>")


def _link_attachments(html: str) -> list[Attachment]:
    out = []
    for url in dict.fromkeys(html_links(html)):
        vid = youtube_id(url)
        out.append(Attachment("youtube", video_id=vid, url=url) if vid else Attachment("link", url=url))
    return out[:20]


# ---------------------------------------------------------------- course -> plan
SUPPORTED = {"page", "resource", "url", "folder", "label", "book", "quiz"}


def build_plan(course: Course, include_hidden: bool = True) -> CoursePlan:
    topics: list[TopicPlan] = []
    skipped: list[str] = []
    warnings = list(course.warnings)

    for sec in course.sections:
        tname = sec.name or ("Общее" if sec.number == 0 else f"Тема {sec.number}")
        topic = TopicPlan(key=f"section:{sec.id}", name=one_line(tname, MAX_TOPIC))
        sec_text = html_to_text(sec.summary_html)
        if sec_text and sec.number != 0:
            topic.items.append(Item(key=f"section-summary:{sec.id}", kind="material",
                                    title=one_line(f"О разделе: {tname}", MAX_TITLE),
                                    description=sec_text[:MAX_DESC], draft=not sec.visible,
                                    source="section"))
        for act in sec.activities:
            if act.modname not in SUPPORTED:
                skipped.append(f"[{tname}] {act.modname}: «{act.name}» — тип не переносится")
                continue
            hidden = not (act.visible and sec.visible)
            if hidden and not include_hidden:
                skipped.append(f"[{tname}] {act.modname}: «{act.name}» — скрыт в Moodle")
                continue
            intro = html_to_text(act.intro_html)
            item = Item(key=f"cm:{act.cmid}", kind="material", title=one_line(act.name, MAX_TITLE),
                        description=intro[:MAX_DESC], draft=hidden, source=act.modname)

            if act.modname in ("page", "book"):
                html, other = inline_images(act.content_html, act.content_files)
                item.attachments.append(Attachment("html_doc", title=act.name,
                                                   html=_wrap_html(act.name, html)))
                item.attachments += [Attachment("drive_file", file=f) for f in other]
                item.attachments += _link_attachments(act.content_html)
            elif act.modname in ("resource", "folder"):
                if not act.files:
                    warnings.append(f"«{act.name}»: нет файлов в бэкапе")
                item.attachments += [Attachment("drive_file", file=f) for f in act.files]
            elif act.modname == "url":
                vid = youtube_id(act.external_url)
                item.attachments.append(Attachment("youtube", video_id=vid, url=act.external_url) if vid
                                        else Attachment("link", url=act.external_url))
            elif act.modname == "label":
                text = html_to_text(act.intro_html)
                if not text and not act.intro_files:
                    continue
                item.title = one_line(text.split("\n")[0] if text else "Материал", MAX_TITLE)
                item.description = text[:MAX_DESC]
                item.attachments += _link_attachments(act.intro_html)
            elif act.modname == "quiz":
                spec = quiz_to_form(act)
                warnings += [f"Тест «{act.name}»: {w}" for w in spec.warnings]
                item.kind = "assignment"
                item.max_points = spec.total_points
                item.attachments.append(Attachment("form", title=act.name, form=spec))
            # файлы, вложенные в описание
            item.attachments += [Attachment("drive_file", file=f) for f in act.intro_files
                                 if not f.mimetype.startswith("image/")]
            if len(item.attachments) > 20:
                warnings.append(f"«{act.name}»: {len(item.attachments)} вложений, Classroom допускает 20 — "
                                f"лишние будут в отдельных материалах")
            topic.items.append(item)
        if topic.items:
            topics.append(topic)

    return CoursePlan(
        name=one_line(course.fullname, 750),
        section=one_line(course.shortname, 2800),
        description=html_to_text(course.summary_html)[:30000],
        topics=topics, skipped=skipped, warnings=warnings,
    )


def plan_report(plan: CoursePlan) -> str:
    lines = [f"# {plan.name}  ({plan.section})", ""]
    n_items = sum(len(t.items) for t in plan.topics)
    n_quiz = sum(1 for t in plan.topics for i in t.items if i.kind == "assignment")
    n_files = sum(1 for t in plan.topics for i in t.items for a in i.attachments if a.kind == "drive_file")
    lines.append(f"Тем: {len(plan.topics)} · элементов: {n_items} · тестов→Forms: {n_quiz} · файлов в Drive: {n_files}")
    lines.append("")
    for t in plan.topics:
        lines.append(f"## {t.name}")
        for i in t.items:
            att = ", ".join(
                a.kind if a.kind != "drive_file" else f"файл {a.file.filename}" for a in i.attachments)
            extra = f" [{i.max_points} б.]" if i.kind == "assignment" else ""
            draft = " (черновик)" if i.draft else ""
            lines.append(f"- {i.source}: {i.title}{extra}{draft}" + (f" — {att}" if att else ""))
        lines.append("")
    if plan.skipped:
        lines += ["## Не переносится", *[f"- {s}" for s in plan.skipped], ""]
    if plan.warnings:
        lines += ["## Предупреждения", *[f"- {w}" for w in plan.warnings], ""]
    return "\n".join(lines)
