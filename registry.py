"""Реестр курсов на Google Диске владельца: Google Таблица + копии бэкапов Moodle.

Таблица «Курсы Moodle → Classroom» лежит в папке «Moodle → Classroom» и содержит
для каждого бэкапа: название курса, ссылку на курс, код для записи, состояние,
ссылку на бэкап на Диске и на папку с материалами. Бэкапы загружаются в подпапку
«Бэкапы Moodle» один раз (повторно не загружаются).
"""
from __future__ import annotations

import datetime
import glob
import io
import json
import os
import re
import tarfile
import zipfile

from google_target import ROOT_FOLDER, State

SHEET_NAME = "Курсы Moodle → Classroom"
BACKUPS_FOLDER = "Бэкапы Moodle"
DEFAULT_DIRS = ["inbox", os.path.join("inbox", "done"), "backups", "."]

COURSE_STATES = {
    "ACTIVE": "активен",
    "PROVISIONED": "не принят — откройте Classroom и нажмите «Принять»",
    "ARCHIVED": "в архиве",
    "DECLINED": "отклонён",
    "SUSPENDED": "заблокирован Google",
}


def course_title_from_mbz(path: str) -> str:
    """Название курса из moodle_backup.xml без полной распаковки архива."""
    data = b""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                data = z.read("moodle_backup.xml")
        else:
            with tarfile.open(path, "r:*") as t:
                for m in t:
                    if m.name.lstrip("./") == "moodle_backup.xml":
                        data = t.extractfile(m).read()
                        break
    except Exception:
        return ""
    m = re.search(rb"<original_course_fullname>(.*?)</original_course_fullname>", data, re.S)
    if not m:
        return ""
    import html
    return html.unescape(m.group(1).decode("utf-8", "replace")).strip()


def find_backups(extra: list[str]) -> list[str]:
    files = []
    for p in list(extra) + DEFAULT_DIRS:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".mbz")]
        elif os.path.isfile(p) and p.lower().endswith(".mbz"):
            files.append(p)
    seen, out = set(), []
    for f in files:
        k = os.path.realpath(f)
        if k not in seen and os.path.isfile(f):
            seen.add(k)
            out.append(f)
    return out


def _natural(s: str):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", s)]


def resolve_root(out_dir: str, target, log=print) -> str:
    """Единая папка «Moodle → Classroom». Раньше каждый курс создавал свою — переносим всё в одну."""
    gstate = State(os.path.join(out_dir, "global.json"))
    root = gstate.get("drive:root")
    states = sorted(glob.glob(os.path.join(out_dir, "state", "*.json")), key=os.path.getmtime)
    if root and not target.file_alive(root):
        root = None
    if not root:
        for f in states:  # самая ранняя существующая папка
            r = json.load(open(f)).get("drive:root")
            if r and target.file_alive(r):
                root = r
                break
    if not root:
        gstate.set("drive:root", None)
        root = target.ensure_folder(ROOT_FOLDER, None, gstate, "drive:root")
    gstate.set("drive:root", root)

    old_roots = set()
    for f in states:
        st = State(f)
        r, folder = st.get("drive:root"), st.get("drive:course")
        if r and r != root:
            if folder and target.file_alive(folder):
                target.move_into(folder, root)
            old_roots.add(r)
            st.set("drive:root", root)
    removed = 0
    for r in old_roots:
        try:
            if target.file_alive(r) and target.trash_if_empty(r):
                removed += 1
        except Exception as e:  # не критично
            log(f"  ! не удалось убрать лишнюю папку {r}: {e}")
    if old_roots:
        log(f"  папки курсов собраны в одну «{ROOT_FOLDER}»; лишних пустых папок убрано: {removed}")
    return root


def upload_backups(files: list[str], out_dir: str, target, state_path, folder: str, log=print):
    for mbz in sorted(files, key=lambda p: _natural(os.path.basename(p))):
        st = State(state_path(out_dir, mbz))
        if not st.get("course_name"):
            title = course_title_from_mbz(mbz)
            if title:
                st.set("course_name", title)
        st.set("source_file", os.path.basename(mbz))
        fid = st.get("backup_file_id")
        if fid and target.file_alive(fid):
            continue
        size_mb = os.path.getsize(mbz) / 1e6
        log(f"  загружаю бэкап {os.path.basename(mbz)} ({size_mb:.0f} МБ)")
        fid = target.upload_large(mbz, os.path.basename(mbz), "application/vnd.moodle.backup", folder, log)
        st.set("backup_file_id", fid)


def collect_rows(out_dir: str, target, log=print) -> list[dict]:
    rows = []
    for f in glob.glob(os.path.join(out_dir, "state", "*.json")):
        try:
            st = json.load(open(f))
        except Exception:
            continue
        cid = st.get("course_id")
        info = target.get_course(cid) if cid else None
        if cid and info is None:
            status_cl = "курс удалён в Classroom"
        elif info:
            status_cl = COURSE_STATES.get(info.get("courseState", ""), info.get("courseState", ""))
        else:
            status_cl = ""
        done = st.get("done")
        if not cid:
            transfer = "не перенесён"
        elif done:
            transfer = "✓ перенесён"
        else:
            transfer = "не завершён"
        code = (info or {}).get("enrollmentCode", "")
        if info and not code and info.get("courseState") == "PROVISIONED":
            code = "появится после «Принять»"
        rows.append({
            "name": (info or {}).get("name") or st.get("course_name") or st.get("source_file", ""),
            "url": (info or {}).get("alternateLink", ""),
            "code": code,
            "classroom": status_cl,
            "transfer": transfer,
            "backup_url": (f"https://drive.google.com/file/d/{st['backup_file_id']}/view"
                           if st.get("backup_file_id") else ""),
            "backup_file": st.get("source_file", ""),
            "folder_url": (f"https://drive.google.com/drive/folders/{st['drive:course']}"
                           if st.get("drive:course") and cid else ""),
            "problems": len(st.get("problems", [])),
            "sort": st.get("source_file") or st.get("course_name") or "",
        })
    rows.sort(key=lambda r: _natural(r["sort"]))
    return rows


def build_xlsx(rows: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Курсы"
    head = ["№", "Курс", "Ссылка на курс", "Код курса", "Состояние в Classroom", "Перенос",
            "Бэкап Moodle", "Файл бэкапа", "Материалы на Диске", "Замечаний"]
    ws.append(head)
    link_font = Font(color="1155CC", underline="single")
    for i, r in enumerate(rows, 1):
        ws.append([i, r["name"], "", r["code"], r["classroom"], r["transfer"], "", r["backup_file"], "",
                   r["problems"] or ""])
        row = ws.max_row
        for col, url, text in ((3, r["url"], "открыть курс"), (7, r["backup_url"], "скачать бэкап"),
                               (9, r["folder_url"], "папка курса")):
            if url:
                c = ws.cell(row=row, column=col, value=text)
                c.hyperlink = url
                c.font = link_font
        code_cell = ws.cell(row=row, column=4)
        code_cell.font = Font(bold=True, name="Consolas")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="3C4A64")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    widths = [5, 60, 16, 14, 26, 16, 16, 48, 18, 11]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(head))}{max(1, ws.max_row)}"

    info = wb.create_sheet("О таблице")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for line in [
        ["Обновлено", now],
        ["Курсов в таблице", len(rows)],
        ["Перенесено", sum(1 for r in rows if r["transfer"].startswith("✓"))],
        [],
        ["Код курса", "Ученик вводит его в Classroom: «+» → «Присоединиться к курсу»."],
        ["Бэкап Moodle", "Исходный файл .mbz — его можно восстановить в любом Moodle."],
        ["Таблица обновляется", "автоматически после каждого переноса (команды run и batch)."],
        ["", "Правки в этой таблице при обновлении перезаписываются — свои заметки ведите в копии."],
    ]:
        info.append(line)
    info.column_dimensions["A"].width = 22
    info.column_dimensions["B"].width = 90
    for c in info["A"]:
        c.font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sync_registry(out_dir: str, target, state_path, extra_paths: list[str] = (), log=print) -> str:
    """Загружает бэкапы, обновляет таблицу. Возвращает ссылку на таблицу."""
    gstate = State(os.path.join(out_dir, "global.json"))
    log("\n=== Реестр на Google Диске")
    root = resolve_root(out_dir, target, log)
    backups_folder = target.ensure_folder(BACKUPS_FOLDER, root, gstate, "drive:backups")
    if not target.file_alive(backups_folder):
        gstate.set("drive:backups", None)
        backups_folder = target.ensure_folder(BACKUPS_FOLDER, root, gstate, "drive:backups")
    files = find_backups(list(extra_paths))
    upload_backups(files, out_dir, target, state_path, backups_folder, log)
    rows = collect_rows(out_dir, target, log)
    sheet_id = target.upsert_spreadsheet(build_xlsx(rows), SHEET_NAME, root, gstate.get("sheet_id"))
    gstate.set("sheet_id", sheet_id)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    log(f"  таблица ({len(rows)} строк): {url}")
    return url
