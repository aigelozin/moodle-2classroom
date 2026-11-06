"""Выполнение плана в Google: Classroom + Drive + Forms.

Все созданные объекты записываются в state-файл, поэтому повторный запуск
(после ошибки или обрыва) продолжает с места остановки, не создавая дублей.
"""
from __future__ import annotations

import json
import os
import random
import time

from convert import Attachment, CoursePlan, Item

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses",
    "https://www.googleapis.com/auth/classroom.topics",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/forms.body",
]

ROOT_FOLDER = "Moodle → Classroom"


def get_credentials(credentials_file: str, token_file: str, port: int = 8765, manual: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not os.path.exists(credentials_file):
            raise SystemExit(f"Нет {credentials_file} — скачайте OAuth-клиент (Desktop app) из Google Cloud, см. README")
        flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
        if manual:
            # сервер без браузера и без SSH-туннеля: пользователь копирует адрес из браузера
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # редирект на http://localhost
            flow.redirect_uri = f"http://localhost:{port}/"
            url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            print("\n1) Откройте ссылку в браузере на любом устройстве и войдите в Google:\n")
            print(url)
            print("\n2) После «Разрешить» браузер покажет ошибку «не удаётся открыть localhost» — это нормально.")
            print("   Скопируйте ПОЛНЫЙ адрес из адресной строки (начинается с http://localhost) и вставьте сюда.\n")
            redirect = input("Адрес: ").strip()
            flow.fetch_token(authorization_response=redirect)
            creds = flow.credentials
        else:
            print(f"\nОткройте ссылку ниже в браузере. Если скрипт на удалённом сервере — "
                  f"сначала пробросьте порт:\n  ssh -L {port}:localhost:{port} user@server\n"
                  f"или используйте: python migrate.py auth --manual\n")
            creds = flow.run_local_server(port=port, open_browser=False)
    with open(token_file, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_file, 0o600)
    return creds


def _retry(call, what: str, tries: int = 6):
    from googleapiclient.errors import HttpError
    for attempt in range(tries):
        try:
            return call.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", 0)
            if status in (429, 500, 502, 503, 504) and attempt < tries - 1:
                delay = min(60, 2 ** attempt + random.random())
                print(f"    … {what}: HTTP {status}, повтор через {delay:.0f} с")
                time.sleep(delay)
                continue
            raise


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = json.load(open(path)) if os.path.exists(path) else {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class GoogleTarget:
    def __init__(self, creds, publish_forms_to_anyone: bool = True):
        from googleapiclient.discovery import build
        self.classroom = build("classroom", "v1", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.forms = build("forms", "v1", credentials=creds, cache_discovery=False)
        self.publish_anyone = publish_forms_to_anyone

    # ------------------------------------------------------------ drive
    def ensure_folder(self, name: str, parent: str | None, state: State, key: str) -> str:
        if state.get(key):
            return state.get(key)
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent:
            body["parents"] = [parent]
        fid = _retry(self.drive.files().create(body=body, fields="id"), "папка")["id"]
        state.set(key, fid)
        return fid

    def upload_file(self, path: str, name: str, mimetype: str, folder: str) -> str:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(path, mimetype=mimetype or "application/octet-stream",
                                resumable=os.path.getsize(path) > 5_000_000)
        return _retry(self.drive.files().create(body={"name": name, "parents": [folder]},
                                                media_body=media, fields="id"), f"файл {name}")["id"]

    def html_to_doc(self, html: str, name: str, folder: str) -> str:
        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(html.encode("utf-8"), mimetype="text/html", resumable=True)
        body = {"name": name, "parents": [folder], "mimeType": "application/vnd.google-apps.document"}
        return _retry(self.drive.files().create(body=body, media_body=media, fields="id"),
                      f"документ {name}")["id"]

    # ------------------------------------------------------------ forms
    def create_form(self, spec, folder: str) -> dict:
        form = _retry(self.forms.forms().create(body={"info": {"title": spec.title,
                                                               "documentTitle": spec.title}}),
                      "форма")
        fid = form["formId"]
        # batchUpdate частями: так проще найти проблемный вопрос
        reqs = spec.requests
        for i in range(0, len(reqs), 50):
            _retry(self.forms.forms().batchUpdate(formId=fid, body={"requests": reqs[i:i + 50]}),
                   "вопросы формы")
        # с 2026 г. формы, созданные через API, по умолчанию не опубликованы
        _retry(self.forms.forms().setPublishSettings(formId=fid, body={
            "publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": True}},
            "updateMask": "publishState"}), "публикация формы")
        if self.publish_anyone:
            _retry(self.drive.permissions().create(
                fileId=fid, body={"type": "anyone", "role": "reader", "view": "published"}),
                "доступ к форме")
        # перенести форму в папку курса
        meta = _retry(self.drive.files().get(fileId=fid, fields="parents"), "папка формы")
        _retry(self.drive.files().update(fileId=fid, addParents=folder,
                                         removeParents=",".join(meta.get("parents", [])),
                                         fields="id"), "перенос формы")
        info = _retry(self.forms.forms().get(formId=fid), "форма")
        return {"id": fid, "url": info["responderUri"]}

    # ------------------------------------------------------------ classroom
    def create_course(self, plan: CoursePlan) -> str:
        """Создаёт курс. Личным Gmail Google часто не разрешает сразу ACTIVE —
        тогда пробуем PROVISIONED и без явного состояния."""
        from googleapiclient.errors import HttpError
        base = {"name": plan.name, "section": plan.section, "description": plan.description,
                "ownerId": "me"}
        last = None
        for state in ("ACTIVE", "PROVISIONED", None):
            body = dict(base)
            if state:
                body["courseState"] = state
            try:
                cid = _retry(self.classroom.courses().create(body=body), "курс")["id"]
                if state != "ACTIVE":
                    print("  ! Курс создан неактивным: откройте classroom.google.com и нажмите «Принять» "
                          "на карточке курса (сделайте это после переноса).")
                return cid
            except HttpError as e:
                last = e
                if "coursestatedenied" not in str(e).lower():
                    raise
        raise SystemExit(
            "Google не разрешает этому аккаунту создавать курсы через API (CourseStateDenied).\n"
            "Что сделать:\n"
            "  1) Откройте https://classroom.google.com этим же Gmail, выберите роль «Я преподаватель».\n"
            "  2) Нажмите «+» → «Создать курс», назовите его как курс Moodle.\n"
            "  3) Скопируйте адрес курса из браузера и запустите перенос в него:\n"
            "     python migrate.py run backups/<бэкап>.mbz --course \"https://classroom.google.com/c/...\"\n"
            f"Ответ Google: {last}")

    def check_course(self, course_id: str) -> dict:
        return _retry(self.classroom.courses().get(id=course_id), "курс")

    def create_topic(self, course_id: str, name: str) -> str:
        return _retry(self.classroom.courses().topics().create(courseId=course_id, body={"name": name}),
                      "тема")["topicId"]

    def create_item(self, course_id: str, topic_id: str, item: Item, materials: list[dict]) -> str:
        body = {"title": item.title, "topicId": topic_id,
                "state": "DRAFT" if item.draft else "PUBLISHED"}
        if item.description:
            body["description"] = item.description
        if materials:
            body["materials"] = materials
        if item.kind == "assignment":
            body.update({"workType": "ASSIGNMENT", "maxPoints": item.max_points or 0})
            r = self.classroom.courses().courseWork().create(courseId=course_id, body=body)
        else:
            r = self.classroom.courses().courseWorkMaterials().create(courseId=course_id, body=body)
        return _retry(r, f"элемент «{item.title[:40]}»")["id"]


def _material(att: Attachment, target: GoogleTarget, folder: str, state: State, key: str) -> dict | None:
    """Создаёт (или берёт из state) ресурс и возвращает материал Classroom."""
    done = state.get(key)
    if att.kind == "link":
        return {"link": {"url": att.url}} if att.url else None
    if att.kind == "youtube":
        return {"youtubeVideo": {"id": att.video_id}}
    if att.kind == "drive_file":
        fid = done or target.upload_file(att.file.path, att.file.filename, att.file.mimetype, folder)
        state.set(key, fid)
        return {"driveFile": {"driveFile": {"id": fid}, "shareMode": "VIEW"}}
    if att.kind == "html_doc":
        fid = done or target.html_to_doc(att.html, att.title, folder)
        state.set(key, fid)
        return {"driveFile": {"driveFile": {"id": fid}, "shareMode": "VIEW"}}
    if att.kind == "form":
        info = done or target.create_form(att.form, folder)
        state.set(key, info)
        return {"link": {"url": info["url"]}}
    return None


def parse_course_ref(ref: str) -> str:
    """Принимает id курса или ссылку https://classroom.google.com/c/<base64-id>."""
    import base64
    import re
    ref = ref.strip()
    m = re.search(r"/c/([A-Za-z0-9_-]+)", ref)
    if not m:
        return ref
    alias = m.group(1)
    try:
        dec = base64.urlsafe_b64decode(alias + "=" * (-len(alias) % 4)).decode()
        if dec.isdigit():
            return dec
    except Exception:
        pass
    return alias


def run_plan(plan: CoursePlan, target: GoogleTarget, state: State, log=print,
             existing_course: str | None = None):
    root = target.ensure_folder(ROOT_FOLDER, None, state, "drive:root")
    folder = target.ensure_folder(plan.name[:200], root, state, "drive:course")

    course_id = state.get("course_id")
    if existing_course and not course_id:
        course_id = parse_course_ref(existing_course)
        info = target.check_course(course_id)
        log(f"  перенос в существующий курс «{info.get('name')}» ({course_id})")
        state.set("course_id", course_id)
    if not course_id:
        course_id = target.create_course(plan)
        state.set("course_id", course_id)
        log(f"  курс создан: {course_id}")

    # Classroom показывает новые темы и элементы сверху, поэтому создаём в обратном порядке,
    # чтобы порядок совпал с Moodle.
    for topic in reversed(plan.topics):
        tkey = f"topic:{topic.key}"
        topic_id = state.get(tkey)
        if not topic_id:
            topic_id = target.create_topic(course_id, topic.name)
            state.set(tkey, topic_id)
        log(f"  тема «{topic.name}»")
        for item in reversed(topic.items):
            ikey = f"item:{item.key}"
            if state.get(ikey):
                continue
            materials = []
            for n, att in enumerate(item.attachments):
                m = _material(att, target, folder, state, f"att:{item.key}:{n}")
                if m:
                    materials.append(m)
            # Classroom: не больше 20 материалов на элемент
            chunks = [materials[i:i + 20] for i in range(0, len(materials), 20)] or [[]]
            for ci, chunk in reversed(list(enumerate(chunks))):
                ck = ikey if ci == 0 else f"{ikey}:part{ci}"
                if state.get(ck):
                    continue
                part = item if ci == 0 else Item(key=ck, kind="material",
                                                 title=f"{item.title} (часть {ci + 1})"[:300],
                                                 description="", draft=item.draft)
                state.set(ck, target.create_item(course_id, topic_id, part, chunk))
            log(f"    + {item.source}: {item.title}")
    return course_id
