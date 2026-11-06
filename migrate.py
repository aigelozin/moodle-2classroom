#!/usr/bin/env python3
"""Перенос курсов Moodle (.mbz) в Google Classroom.

  python migrate.py plan  backups/*.mbz          # без Google: отчёт, что и как перенесётся
  python migrate.py auth                          # один раз: вход в Google, сохраняется token.json
  python migrate.py run   backups/*.mbz          # перенос (можно перезапускать — продолжит)
  python migrate.py list                          # ссылки на перенесённые курсы (out/courses.md)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert import build_plan, plan_report  # noqa: E402
from mbz_parser import cleanup, parse_mbz      # noqa: E402


def state_path(out_dir: str, mbz: str) -> str:
    h = hashlib.sha1(os.path.abspath(mbz).encode()).hexdigest()[:8]
    base = os.path.splitext(os.path.basename(mbz))[0]
    return os.path.join(out_dir, "state", f"{base}-{h}.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "auth", "run", "list"])
    ap.add_argument("mbz", nargs="*", help=".mbz файлы")
    ap.add_argument("--out", default="out", help="папка для отчётов и state (по умолчанию ./out)")
    ap.add_argument("--credentials", default="credentials.json", help="OAuth-клиент (Desktop app)")
    ap.add_argument("--token", default="token.json")
    ap.add_argument("--port", type=int, default=8765, help="порт для OAuth-редиректа")
    ap.add_argument("--manual", action="store_true",
                    help="вход без браузера на сервере: вставить адрес из браузера вручную")
    ap.add_argument("--course", help="перенести в уже созданный курс: ссылка classroom.google.com/c/... или id "
                                     "(только вместе с одним .mbz)")
    ap.add_argument("--skip-hidden", action="store_true", help="не переносить скрытое в Moodle")
    ap.add_argument("--private-forms", action="store_true",
                    help="не открывать тесты по ссылке для всех (доступ настроите сами)")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, "state"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "reports"), exist_ok=True)

    if a.command == "list":
        path = write_courses_index(a.out)
        print(open(path).read())
        return

    target = None
    if a.command in ("auth", "run"):
        from google_target import GoogleTarget, get_credentials
        creds = get_credentials(a.credentials, a.token, a.port, manual=a.manual)
        if a.command == "auth":
            print(f"Готово, токен сохранён в {a.token}")
            return
        target = GoogleTarget(creds, publish_forms_to_anyone=not a.private_forms)

    if not a.mbz:
        ap.error("укажите .mbz файлы")
    if a.course and len(a.mbz) != 1:
        ap.error("--course можно указать только для одного .mbz")

    missing = [p for p in a.mbz if not os.path.isfile(p)]
    if missing:
        import glob
        print("Файл не найден: " + ", ".join(missing))
        found = sorted(glob.glob("*.mbz") + glob.glob("backups/*.mbz") + glob.glob(os.path.expanduser("~/*.mbz"))
                       + glob.glob(os.path.expanduser("~/Загрузки/*.mbz")) + glob.glob(os.path.expanduser("~/Downloads/*.mbz")))
        if found:
            print("Найденные бэкапы:\n  " + "\n  ".join(found))
            print("Укажите путь к одному из них, например:\n"
                  f"  python migrate.py {a.command} \"{found[0]}\"")
        else:
            print("Бэкапов .mbz не найдено ни здесь, ни в backups/, ни в ~/Загрузки. "
                  "Найти: find ~ -name '*.mbz'")
        sys.exit(2)

    failed = []
    for mbz in a.mbz:
        print(f"\n=== {os.path.basename(mbz)}")
        course = None
        try:
            course = parse_mbz(mbz)
            plan = build_plan(course, include_hidden=not a.skip_hidden)
            report = plan_report(plan)
            rpath = os.path.join(a.out, "reports", os.path.splitext(os.path.basename(mbz))[0] + ".md")
            with open(rpath, "w") as fh:
                fh.write(report)
            print(f"  {plan.name}: тем {len(plan.topics)}, "
                  f"предупреждений {len(plan.warnings)}, пропущено {len(plan.skipped)} → {rpath}")
            if target:
                from google_target import State, run_plan
                st = State(state_path(a.out, mbz))
                cid = run_plan(plan, target, st, existing_course=a.course)
                print(f"  ✓ перенесено: https://classroom.google.com/c/{_alias(cid)}  (id {cid})")
        except Exception as e:
            failed.append(mbz)
            print(f"  ✗ ошибка: {e}")
            traceback.print_exc()
        finally:
            if course:
                cleanup(course)

    if target:
        print(f"\nСсылки на курсы: {write_courses_index(a.out)}")

    if failed:
        print(f"\nС ошибками: {len(failed)} — запустите ту же команду ещё раз, перенос продолжится.")
        sys.exit(1)


def write_courses_index(out_dir: str) -> str:
    """Собирает out/courses.md из всех state-файлов: курс, ссылка в Classroom, папка в Drive."""
    import datetime
    import glob
    import json
    rows = []
    for f in sorted(glob.glob(os.path.join(out_dir, "state", "*.json"))):
        try:
            st = json.load(open(f))
        except Exception:
            continue
        cid = st.get("course_id")
        if not cid:
            continue
        name = st.get("course_name") or os.path.basename(f).rsplit("-", 1)[0]
        done = st.get("done")
        status = ("✓ перенесён" if done else
                  "не завершён — запустите run ещё раз" if done is False else
                  "неизвестно (старая версия) — запустите run ещё раз, дублей не будет")
        folder = st.get("drive:course")
        drive = f"[папка](https://drive.google.com/drive/folders/{folder})" if folder else "—"
        rows.append(f"| {name.replace('|', '/')} | [открыть](https://classroom.google.com/c/{_alias(cid)}) "
                    f"| {drive} | {status} | `{os.path.basename(f)}` |")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    text = "\n".join([
        "# Курсы, перенесённые в Google Classroom", "",
        f"Обновлено: {now}", "",
        "| Курс | Classroom | Google Drive | Статус | State-файл |",
        "|---|---|---|---|---|",
        *rows, "",
    ]) if rows else "# Курсы, перенесённые в Google Classroom\n\nПока нет ни одного курса.\n"
    path = os.path.join(out_dir, "courses.md")
    with open(path, "w") as fh:
        fh.write(text)
    return path


def _alias(course_id: str) -> str:
    # ссылка вида classroom.google.com/c/<base64 id>
    import base64
    return base64.b64encode(course_id.encode()).decode().rstrip("=")


if __name__ == "__main__":
    main()
