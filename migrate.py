#!/usr/bin/env python3
"""Перенос курсов Moodle (.mbz) в Google Classroom.

  python3 migrate.py plan  backups/               # без Google: отчёт, что и как перенесётся
  python3 migrate.py auth                         # один раз: вход в Google, сохраняется token.json
  python3 migrate.py run   backups/*.mbz          # перенос (можно перезапускать — продолжит)
  python3 migrate.py batch                        # перенести все бэкапы из inbox/ по очереди,
                                                  # готовые переложить в inbox/done/
  python3 migrate.py list                         # ссылки на перенесённые курсы (out/courses.md)

Вместо файлов можно указывать папки — берутся все .mbz внутри.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _use_project_venv():
    """Если запущено системным python3, а рядом есть .venv — перезапускаемся из .venv,
    чтобы не нужно было помнить про `. .venv/bin/activate`."""
    venv_py = os.path.join(HERE, ".venv", "bin", "python")
    if os.path.exists(venv_py) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(HERE, ".venv")) \
            and not os.environ.get("M2C_NO_VENV_SWITCH"):
        os.environ["M2C_NO_VENV_SWITCH"] = "1"
        os.execv(venv_py, [venv_py, os.path.abspath(__file__), *sys.argv[1:]])


_use_project_venv()

from convert import build_plan, plan_report  # noqa: E402
from mbz_parser import cleanup, parse_mbz      # noqa: E402


def _content_id(mbz: str) -> str:
    """Отпечаток файла: размер + начало + конец. Не зависит от пути и имени,
    поэтому перенос файла в другую папку не создаёт курс заново."""
    size = os.path.getsize(mbz)
    h = hashlib.sha1(str(size).encode())
    with open(mbz, "rb") as fh:
        h.update(fh.read(1 << 20))
        if size > (2 << 20):
            fh.seek(-(1 << 20), os.SEEK_END)
            h.update(fh.read())
    return h.hexdigest()[:10]


def state_path(out_dir: str, mbz: str) -> str:
    base = os.path.splitext(os.path.basename(mbz))[0]
    sdir = os.path.join(out_dir, "state")
    # старый формат: <имя>-<хеш пути>.json — продолжаем использовать, если он есть
    legacy = os.path.join(sdir, f"{base}-{hashlib.sha1(os.path.abspath(mbz).encode()).hexdigest()[:8]}.json")
    if os.path.exists(legacy):
        return legacy
    import glob
    import re
    # старый формат, но файл переложен (например, в inbox/): ищем по имени файла
    for f in sorted(glob.glob(os.path.join(sdir, glob.escape(base) + "-*.json"))):
        if re.fullmatch(re.escape(base) + r"-[0-9a-f]{8}\.json", os.path.basename(f)):
            return f
    cid = _content_id(mbz)
    for f in glob.glob(os.path.join(sdir, f"*-c{cid}.json")):
        return f                      # тот же файл, но переименованный
    return os.path.join(sdir, f"{base[:120]}-c{cid}.json")


def collect_mbz(paths: list[str]) -> tuple[list[str], list[str]]:
    """Раскрывает папки в списки .mbz (без подпапки done/). Возвращает (файлы, не найдено)."""
    files, missing, seen = [], [], set()
    for p in paths:
        if os.path.isdir(p):
            found = sorted(os.path.join(p, f) for f in os.listdir(p)
                           if f.lower().endswith(".mbz") and os.path.isfile(os.path.join(p, f)))
            if not found:
                print(f"В папке {p} нет .mbz")
        elif os.path.isfile(p):
            found = [p]
        else:
            missing.append(p)
            continue
        for f in found:
            key = os.path.realpath(f)
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files, missing


def is_done(out_dir: str, mbz: str) -> bool:
    import json
    p = state_path(out_dir, mbz)
    if not os.path.exists(p):
        return False
    try:
        return bool(json.load(open(p)).get("done"))
    except Exception:
        return False


class _Tee:
    """Дублирует вывод в лог-файл, чтобы не нужно было писать `| tee`."""
    def __init__(self, stream, fh):
        self.stream, self.fh = stream, fh

    def write(self, s):
        self.stream.write(s)
        self.fh.write(s)
        self.fh.flush()
        return len(s)

    def flush(self):
        self.stream.flush()
        self.fh.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def start_log(out_dir: str, command: str) -> str:
    import datetime
    os.makedirs(os.path.join(out_dir, "logs"), exist_ok=True)
    path = os.path.join(out_dir, "logs", f"{command}-{datetime.datetime.now():%Y%m%d-%H%M%S}.log")
    fh = open(path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "auth", "run", "batch", "list"])
    ap.add_argument("mbz", nargs="*", help=".mbz файлы или папки с ними (для batch по умолчанию inbox/)")
    ap.add_argument("--out", default="out", help="папка для отчётов и state (по умолчанию ./out)")
    ap.add_argument("--credentials", default="credentials.json", help="OAuth-клиент (Desktop app)")
    ap.add_argument("--token", default="token.json")
    ap.add_argument("--port", type=int, default=8765, help="порт для OAuth-редиректа")
    ap.add_argument("--manual", action="store_true",
                    help="вход без браузера на сервере: вставить адрес из браузера вручную")
    ap.add_argument("--course", help="перенести в уже созданный курс: ссылка classroom.google.com/c/... или id "
                                     "(только вместе с одним .mbz)")
    ap.add_argument("--recheck", action="store_true",
                    help="не пропускать уже перенесённые курсы, а проверить их ещё раз (дублей не будет)")
    ap.add_argument("--retries", type=int, default=1,
                    help="batch: сколько раз повторить курсы, упавшие с ошибкой (по умолчанию 1)")
    ap.add_argument("--keep", action="store_true", help="batch: не перекладывать готовые бэкапы в done/")
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

    if a.command == "batch" and not a.mbz:
        a.mbz = ["inbox"]
        os.makedirs("inbox", exist_ok=True)

    if a.command in ("run", "batch"):
        print(f"Лог: {start_log(a.out, a.command)}")

    target = None
    if a.command in ("auth", "run", "batch"):
        from google_target import GoogleTarget, get_credentials
        creds = get_credentials(a.credentials, a.token, a.port, manual=a.manual)
        if a.command == "auth":
            print(f"Готово, токен сохранён в {a.token}")
            return
        target = GoogleTarget(creds, publish_forms_to_anyone=not a.private_forms)

    if not a.mbz:
        ap.error("укажите .mbz файлы или папку с ними")

    files, missing = collect_mbz(a.mbz)
    if missing:
        import glob
        print("Не найдено: " + ", ".join(missing))
        found = sorted(glob.glob("*.mbz") + glob.glob("backups/*.mbz") + glob.glob("inbox/*.mbz")
                       + glob.glob(os.path.expanduser("~/*.mbz"))
                       + glob.glob(os.path.expanduser("~/Загрузки/*.mbz")) + glob.glob(os.path.expanduser("~/Downloads/*.mbz")))
        if found:
            print("Найденные бэкапы:\n  " + "\n  ".join(found))
            print("Укажите путь к файлу или папке, например:\n"
                  f"  python3 migrate.py {a.command} \"{os.path.dirname(found[0]) or '.'}\"")
        else:
            print("Бэкапов .mbz не найдено ни здесь, ни в backups/, inbox/, ~/Загрузки. "
                  "Найти: find ~ -name '*.mbz'")
        sys.exit(2)
    if not files:
        print("Нечего переносить.")
        return
    if a.course and len(files) != 1:
        ap.error("--course можно указать только для одного .mbz")

    results: dict[str, tuple[str, str]] = {}   # файл -> (статус, подробности)
    queue = list(files)
    for attempt in range(1 + (a.retries if a.command == "batch" else 0)):
        if attempt:
            print(f"\n### Повтор упавших курсов ({len(queue)}), попытка {attempt + 1}")
        failed = []
        for i, mbz in enumerate(queue, 1):
            res = process_one(mbz, i, len(queue), a, target)
            results[mbz] = res
            if res[0] == "error":          # повреждённые бэкапы не повторяем — без толку
                failed.append(mbz)
            elif a.command == "batch" and res[0] in ("done", "skipped") and not a.keep:
                move_to_done(mbz)
        queue = failed
        if not queue:
            break

    if target:
        print(f"\nСсылки на курсы: {write_courses_index(a.out)}")
    print_summary(results, a.command)
    states = [s for s, _ in results.values()]
    if "error" in states:
        print("\nКурсы с ошибками остались на месте — запустите ту же команду ещё раз, перенос продолжится.")
    if "broken" in states:
        print("\nПовреждённые бэкапы: скачайте их из Moodle заново и положите на место старых.")
    if "error" in states or "broken" in states:
        sys.exit(1)


def process_one(mbz: str, i: int, n: int, a, target) -> tuple[str, str]:
    """Один бэкап: разбор, отчёт и (если есть target) перенос. Возвращает (статус, подробности)."""
    print(f"\n=== [{i}/{n}] {os.path.basename(mbz)}")
    if target and not a.recheck and not a.course and is_done(a.out, mbz):
        print("  уже перенесён — пропускаю (--recheck, чтобы проверить заново)")
        return "skipped", "уже перенесён"
    course = None
    try:
        try:
            course = parse_mbz(mbz)
        except Exception as e:
            msg = ("бэкап повреждён или это не .mbz — скачайте его из Moodle заново "
                   f"({type(e).__name__}: {str(e).splitlines()[0][:120]})")
            print(f"  ✗ {msg}")
            return "broken", msg
        plan = build_plan(course, include_hidden=not a.skip_hidden)
        report = plan_report(plan)
        rpath = os.path.join(a.out, "reports", os.path.splitext(os.path.basename(mbz))[0] + ".md")
        with open(rpath, "w") as fh:
            fh.write(report)
        print(f"  {plan.name}: тем {len(plan.topics)}, "
              f"предупреждений {len(plan.warnings)}, пропущено {len(plan.skipped)} → {rpath}")
        if not target:
            return "planned", plan.name
        from google_target import State, run_plan
        st = State(state_path(a.out, mbz))
        st.set("source_file", os.path.basename(mbz))
        cid = run_plan(plan, target, st, existing_course=a.course)
        url = f"https://classroom.google.com/c/{_alias(cid)}"
        print(f"  ✓ перенесено: {url}  (id {cid})")
        return "done", f"{plan.name} — {url}"
    except KeyboardInterrupt:
        raise
    except SystemExit as e:   # понятные ошибки из google_target (например, CourseStateDenied)
        print(f"  ✗ {e}")
        return "error", str(e).split("\n")[0]
    except Exception as e:
        print(f"  ✗ ошибка: {e}")
        traceback.print_exc()
        return "error", str(e).split("\n")[0][:200]
    finally:
        if course:
            cleanup(course)


def move_to_done(mbz: str):
    d = os.path.join(os.path.dirname(mbz) or ".", "done")
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, os.path.basename(mbz))
    if os.path.exists(target):
        root, ext = os.path.splitext(target)
        import time
        target = f"{root}-{int(time.time())}{ext}"
    os.replace(mbz, target)
    print(f"  → {target}")


def print_summary(results: dict, command: str):
    labels = {"done": "✓ перенесён", "skipped": "• пропущен", "planned": "• разобран", "error": "✗ ошибка",
              "broken": "✗ повреждён"}
    print("\n" + "=" * 60)
    print(f"Итог ({command}): " + ", ".join(
        f"{labels[k]}: {sum(1 for s, _ in results.values() if s == k)}"
        for k in labels if any(s == k for s, _ in results.values())))
    for mbz, (s, info) in results.items():
        print(f"  {labels[s]:<12} {os.path.basename(mbz)}" +
              (f"\n               {info}" if s in ("error", "broken") else ""))


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
        if st.get("problems"):
            status += f"; замечаний: {len(st['problems'])}"
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
    notes = []
    for f in sorted(glob.glob(os.path.join(out_dir, "state", "*.json"))):
        try:
            st = json.load(open(f))
        except Exception:
            continue
        if st.get("problems"):
            notes += ["", f"## Замечания: {st.get('course_name', os.path.basename(f))}", "",
                      *[f"- {p}" for p in st["problems"]]]
    if notes:
        text += "\n".join(notes) + "\n"
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
